# Copyright 2013 Openstack Foundation
# All Rights Reserved.
#
#    Licensed under the Apache License, Version 2.0 (the "License"); you may
#    not use this file except in compliance with the License. You may obtain
#    a copy of the License at
#
#         http://www.apache.org/licenses/LICENSE-2.0
#
#    Unless required by applicable law or agreed to in writing, software
#    distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
#    WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
#    License for the specific language governing permissions and limitations
#    under the License.

"""
Quark Pluggable IPAM
"""

import random
import uuid

import netaddr
from neutron.common import exceptions
from neutron.openstack.common import log as logging
from neutron.openstack.common.notifier import api as notifier_api
from neutron.openstack.common import timeutils

from oslo.config import cfg

from quark.db import api as db_api
from quark.db import models

LOG = logging.getLogger(__name__)
CONF = cfg.CONF

quark_opts = [
    cfg.IntOpt('v6_allocation_attempts',
               default=10,
               help=_('Number of times to retry generating v6 addresses'
                      ' before failure. Also implicitly controls how many'
                      ' v6 addresses we assign to any port, as the random'
                      ' values generated will be the same every time.'))
]

CONF.register_opts(quark_opts, "QUARK")

# NOTE(mdietz): equivalent to the following line, but converting
#               v6 addresses in netaddr is very slow.
# netaddr.IPAddress("::0200:0:0:0").value
MAGIC_INT = 144115188075855872


def rfc2462_ip(mac, cidr):
    #NOTE(mdietz): see RFC2462
    int_val = netaddr.IPNetwork(cidr).value
    mac = netaddr.EUI(mac)
    int_val += mac.eui64().value
    int_val ^= MAGIC_INT
    return int_val


def rfc3041_ip(port_id, cidr):
    random.seed(int(uuid.UUID(port_id)))
    int_val = netaddr.IPNetwork(cidr).value
    while True:
        val = int_val + random.getrandbits(64)
        val ^= MAGIC_INT
        yield val


def generate_v6(mac, port_id, cidr):
    yield rfc2462_ip(mac, cidr)
    for addr in rfc3041_ip(port_id, cidr):
        yield addr


class QuarkIpam(object):

    def allocate_mac_address(self, context, net_id, port_id, reuse_after,
                             mac_address=None):
        if mac_address:
            mac_address = netaddr.EUI(mac_address).value

        with context.session.begin():
            deallocated_mac = db_api.mac_address_find(
                context, lock_mode=True, reuse_after=reuse_after,
                scope=db_api.ONE, address=mac_address)
            if deallocated_mac:
                return db_api.mac_address_update(
                    context, deallocated_mac, deallocated=False,
                    deallocated_at=None)

        ranges = db_api.mac_address_range_find_allocation_counts(
            context, address=mac_address)

        import random
        r = random.randint(0, 9999999)
        for result in ranges:

            LOG.critical("%s - %s" % (r, "A" * 80))
            LOG.critical("Starting transaction %s !!!!!!!!!" % r)
            for retry in xrange(20):
                try:
                    with context.session.begin():
                    # This could be stale under load, but that's ok
                        rng, addr_count = result
                        LOG.critical("%s - ID: %s" % (r, rng["id"]))
                        LOG.critical("%s - Auto Assign: %s" % (r,
                            rng["next_auto_assign_mac"]))
                        mr = db_api.mac_address_range_find(context,
                                                           id=rng["id"],
                                                           lock_mode=True,
                                                           scope=db_api.ONE)
                        last = rng["last_address"]
                        first = rng["first_address"]
                        if last - first <= addr_count:
                            break

                        next_address = None
                        if mac_address:
                            next_address = mac_address
                        else:
                            next_address = mr["next_auto_assign_mac"]
                            LOG.critical("%s - Next Address: %s" % (r,
                                next_address))
                            mr["next_auto_assign_mac"] = next_address + 1
                            LOG.critical("%s - Updated: %s" % (r,
                                mr["next_auto_assign_mac"]))
                            context.session.add(mr)
                            LOG.critical("%s - %s" % (r, "A" * 80))

                        address = db_api.mac_address_create(
                            context, address=next_address,
                            mac_address_range_id=mr["id"])
                        return address
                except Exception:
                    LOG.exception()
                    continue

        raise exceptions.MacAddressGenerationFailure(net_id=net_id)

    def attempt_to_reallocate_ip(self, context, net_id, port_id, reuse_after,
                                 version=None, ip_address=None,
                                 segment_id=None, subnets=None, **kwargs):
        version = version or [4, 6]
        elevated = context.elevated()

        if version == 6 and "mac_address" in kwargs and kwargs["mac_address"]:
            # Defers to the create case. The reason why is we'd have to look
            # up subnets here to correctly generate the v6. If we split them
            # up into reallocate and create, we'd be looking up the same
            # subnets twice, which is a waste of time.
            return []

        # We never want to take the chance of an infinite loop here. Instead,
        # we'll clean up multiple bad IPs if we find them (assuming something
        # is really wrong)

        sub_ids = []
        if subnets:
            sub_ids = subnets
        else:
            if segment_id:
                subnets = db_api.subnet_find(elevated,
                                             network_id=net_id,
                                             segment_id=segment_id)
                sub_ids = [s["id"] for s in subnets]
                if not sub_ids:
                    raise exceptions.IpAddressGenerationFailure(
                        net_id=net_id)

        ip_kwargs = {
            "network_id": net_id, "reuse_after": reuse_after,
            "deallocated": True, "scope": db_api.ONE,
            "ip_address": ip_address, "lock_mode": True,
            "version": version, "order_by": "address"}

        if sub_ids:
            ip_kwargs["subnet_id"] = sub_ids

        address = db_api.ip_address_find(elevated, **ip_kwargs)

        if address:
            #NOTE(mdietz): We should always be in the CIDR but we've
            #              also said that before :-/
            if address.get("subnet"):
                cidr = netaddr.IPNetwork(address["subnet"]["cidr"])
                addr = netaddr.IPAddress(int(address["address"]),
                                         version=int(cidr.version))
                if addr in cidr:
                    updated_address = db_api.ip_address_update(
                        elevated, address, deallocated=False,
                        deallocated_at=None,
                        allocated_at=timeutils.utcnow())
                    return [updated_address]
                else:
                    # Make sure we never find it again
                    context.session.delete(address)
        return []

    def is_strategy_satisfied(self, ip_addresses):
        return ip_addresses

    def _iterate_until_available_ip(self, context, subnet, network_id,
                                    ip_policy_cidrs):
        address = True
        while address:
            next_ip_int = int(subnet["next_auto_assign_ip"])
            next_ip = netaddr.IPAddress(next_ip_int)
            if subnet["ip_version"] == 4:
                next_ip = next_ip.ipv4()
            subnet["next_auto_assign_ip"] = next_ip_int + 1

            #NOTE(mdietz): treating the IPSet as a boolean caused netaddr to
            #              attempt to enumerate the entire set!
            if (ip_policy_cidrs is not None and
                    next_ip in ip_policy_cidrs):
                continue
            address = db_api.ip_address_find(
                context, network_id=network_id, ip_address=next_ip,
                used_by_tenant_id=context.tenant_id, scope=db_api.ONE)

        ipnet = netaddr.IPNetwork(subnet["cidr"])
        next_addr = netaddr.IPAddress(
            subnet["next_auto_assign_ip"])
        if ipnet.is_ipv4_mapped() or ipnet.version == 4:
            next_addr = next_addr.ipv4()
        return next_ip

    def _allocate_from_subnet(self, context, net_id, subnet,
                              port_id, ip_address=None, **kwargs):
        ip_policy_cidrs = models.IPPolicy.get_ip_policy_cidrs(subnet)
        # Creating this IP for the first time
        next_ip = None
        if ip_address:
            next_ip = ip_address
            address = db_api.ip_address_find(
                context, network_id=net_id, ip_address=next_ip,
                used_by_tenant_id=context.tenant_id, scope=db_api.ONE)
            if address:
                raise exceptions.IpAddressGenerationFailure(
                    net_id=net_id)
        else:
            next_ip = self._iterate_until_available_ip(
                context, subnet, net_id, ip_policy_cidrs)

        context.session.add(subnet)
        address = db_api.ip_address_create(
            context, address=next_ip, subnet_id=subnet["id"],
            version=subnet["ip_version"], network_id=net_id)
        address["deallocated"] = 0
        return address

    def _allocate_from_v6_subnet(self, context, net_id, subnet,
                                 port_id, ip_address=None, **kwargs):
        """This attempts to allocate v6 addresses as per RFC2462 and RFC3041.
        To accomodate this, we effectively treat all v6 assignment as a
        first time allocation utilizing the MAC address of the VIF. Because
        we recycle MACs, we will eventually attempt to recreate a previously
        generated v6 address. Instead of failing, we've opted to handle
        reallocating that address in this method.

        This should provide a performance boost over attempting to check
        each and every subnet in the existing reallocate logic, as we'd
        have to iterate over each and every subnet returned
        """
        if not (ip_address is None and "mac_address" in kwargs and
                kwargs["mac_address"]):
            return self._allocate_from_subnet(context, net_id, subnet,
                                              ip_address, **kwargs)
        else:
            ip_policy_cidrs = models.IPPolicy.get_ip_policy_cidrs(subnet)
            for tries, ip_address in enumerate(
                generate_v6(kwargs["mac_address"]["address"], port_id,
                            subnet["cidr"])):
                if tries > CONF.QUARK.v6_allocation_attempts - 1:
                    raise exceptions.IpAddressGenerationFailure(
                        net_id=net_id)

                ip_address = netaddr.IPAddress(ip_address)

                #NOTE(mdietz): treating the IPSet as a boolean caused netaddr
                #              to attempt to enumerate the entire set!
                if (ip_policy_cidrs is not None and
                        ip_address in ip_policy_cidrs):
                    continue

                address = db_api.ip_address_find(
                    context, network_id=net_id, ip_address=ip_address,
                    used_by_tenant_id=context.tenant_id, scope=db_api.ONE,
                    lock_mode=True)

                break

            if address:
                return db_api.ip_address_update(
                    context, address, deallocated=False, deallocated_at=None,
                    allocated_at=timeutils.utcnow())
            else:
                return db_api.ip_address_create(
                    context, address=ip_address, subnet_id=subnet["id"],
                    version=subnet["ip_version"], network_id=net_id)

    def _allocate_ips_from_subnets(self, context, net_id, subnets,
                                   port_id, ip_address=None, **kwargs):
        new_addresses = []
        for subnet in subnets:
            address = None
            if int(subnet["ip_version"]) == 4:
                address = self._allocate_from_subnet(context, net_id,
                                                     subnet, port_id,
                                                     ip_address, **kwargs)
            else:
                address = self._allocate_from_v6_subnet(context, net_id,
                                                        subnet, port_id,
                                                        ip_address, **kwargs)
            if address:
                new_addresses.append(address)

        return new_addresses

    def _notify_new_addresses(self, context, new_addresses):
        for addr in new_addresses:
            payload = dict(used_by_tenant_id=addr["used_by_tenant_id"],
                           ip_block_id=addr["subnet_id"],
                           ip_address=addr["address_readable"],
                           device_ids=[p["device_id"] for p in addr["ports"]],
                           created_at=addr["created_at"])
            notifier_api.notify(context,
                                notifier_api.publisher_id("network"),
                                "ip_block.address.create",
                                notifier_api.CONF.default_notification_level,
                                payload)

    def allocate_ip_address(self, context, net_id, port_id, reuse_after,
                            segment_id=None, version=None, ip_address=None,
                            subnets=None, **kwargs):
        elevated = context.elevated()
        if ip_address:
            ip_address = netaddr.IPAddress(ip_address)

        new_addresses = []

        realloc_ips = self.attempt_to_reallocate_ip(context, net_id,
                                                    port_id, reuse_after,
                                                    version=None,
                                                    ip_address=ip_address,
                                                    segment_id=segment_id,
                                                    subnets=subnets,
                                                    **kwargs)
        if self.is_strategy_satisfied(realloc_ips):
            return realloc_ips

        new_addresses.extend(realloc_ips)
        if not subnets:
            subnets = self._choose_available_subnet(
                elevated, net_id, version, segment_id=segment_id,
                ip_address=ip_address, reallocated_ips=realloc_ips)
        else:
            subnets = [self.select_subnet(context, net_id, ip_address,
                                          segment_id, subnet_ids=subnets)]

        ips = self._allocate_ips_from_subnets(context, net_id, subnets,
                                              port_id, ip_address, **kwargs)
        new_addresses.extend(ips)

        self._notify_new_addresses(context, new_addresses)
        return new_addresses

    def deallocate_ip_address(self, context, address):
        address["deallocated"] = 1
        payload = dict(used_by_tenant_id=address["used_by_tenant_id"],
                       ip_block_id=address["subnet_id"],
                       ip_address=address["address_readable"],
                       device_ids=[p["device_id"] for p in address["ports"]],
                       created_at=address["created_at"],
                       deleted_at=timeutils.utcnow())
        notifier_api.notify(context,
                            notifier_api.publisher_id("network"),
                            "ip_block.address.delete",
                            notifier_api.CONF.default_notification_level,
                            payload)

    def deallocate_ips_by_port(self, context, port=None, **kwargs):
        ips_removed = []
        for addr in port["ip_addresses"]:
            if "ip_address" in kwargs:
                ip = kwargs["ip_address"]
                if ip != netaddr.IPAddress(int(addr["address"])):
                    continue

            # Note: only deallocate ip if this is the
            # only port mapped
            if len(addr["ports"]) == 1:
                self.deallocate_ip_address(context, addr)
            ips_removed.append(addr)

        port["ip_addresses"] = list(
            set(port["ip_addresses"]) - set(ips_removed))

    def deallocate_mac_address(self, context, address):
        mac = db_api.mac_address_find(context, address=address,
                                      scope=db_api.ONE)
        if not mac:
            raise exceptions.NotFound(
                message="No MAC address %s found" % netaddr.EUI(address))
        db_api.mac_address_update(context, mac, deallocated=True,
                                  deallocated_at=timeutils.utcnow())

    def select_subnet(self, context, net_id, ip_address, segment_id,
                      subnet_ids=None, **filters):
        subnets = db_api.subnet_find_allocation_counts(context, net_id,
                                                       segment_id=segment_id,
                                                       scope=db_api.ALL,
                                                       subnet_id=subnet_ids,
                                                       **filters)
        for subnet, ips_in_subnet in subnets:
            ipnet = netaddr.IPNetwork(subnet["cidr"])
            if ip_address and ip_address not in ipnet:
                continue
            ip_policy_cidrs = None
            if not ip_address:
                ip_policy_cidrs = models.IPPolicy.get_ip_policy_cidrs(subnet)
            policy_size = ip_policy_cidrs.size if ip_policy_cidrs else 0
            if ipnet.size > (ips_in_subnet + policy_size):
                return subnet


class QuarkIpamANY(QuarkIpam):
    @classmethod
    def get_name(self):
        return "ANY"

    def _choose_available_subnet(self, context, net_id, version=None,
                                 segment_id=None, ip_address=None,
                                 reallocated_ips=None):
        filters = {}
        if version:
            filters["ip_version"] = version
        subnet = self.select_subnet(context, net_id, ip_address, segment_id,
                                    **filters)
        if subnet:
            return [subnet]
        raise exceptions.IpAddressGenerationFailure(net_id=net_id)


class QuarkIpamBOTH(QuarkIpam):
    @classmethod
    def get_name(self):
        return "BOTH"

    def is_strategy_satisfied(self, reallocated_ips):
        req = [4, 6]
        for ip in reallocated_ips:
            if ip is not None:
                req.remove(ip["version"])
        if len(req) == 0:
            return True
        return False

    def attempt_to_reallocate_ip(self, context, net_id, port_id,
                                 reuse_after, version=None,
                                 ip_address=None, segment_id=None,
                                 subnets=None, **kwargs):
        both_versions = []
        for ver in (4, 6):
            address = super(QuarkIpamBOTH, self).attempt_to_reallocate_ip(
                context, net_id, port_id, reuse_after, ver, ip_address,
                segment_id, subnets=subnets, **kwargs)
            both_versions.extend(address)
        return both_versions

    def _choose_available_subnet(self, context, net_id, version=None,
                                 segment_id=None, ip_address=None,
                                 reallocated_ips=None):
        both_subnet_versions = []
        need_versions = [4, 6]
        for i in reallocated_ips:
            if i["version"] in need_versions:
                need_versions.remove(i["version"])
        filters = {}
        for ver in need_versions:
            filters["ip_version"] = ver
            sub = self.select_subnet(context, net_id, ip_address, segment_id,
                                     **filters)

            if sub:
                both_subnet_versions.append(sub)
        if not reallocated_ips and not both_subnet_versions:
            raise exceptions.IpAddressGenerationFailure(net_id=net_id)

        return both_subnet_versions


class QuarkIpamBOTHREQ(QuarkIpamBOTH):
    @classmethod
    def get_name(self):
        return "BOTH_REQUIRED"

    def _choose_available_subnet(self, context, net_id, version=None,
                                 segment_id=None, ip_address=None,
                                 reallocated_ips=None):
        subnets = super(QuarkIpamBOTHREQ, self)._choose_available_subnet(
            context, net_id, version, segment_id, ip_address, reallocated_ips)

        if len(reallocated_ips) + len(subnets) < 2:
            raise exceptions.IpAddressGenerationFailure(net_id=net_id)
        return subnets


class IpamRegistry(object):
    def __init__(self):
        self.strategies = {
            QuarkIpamANY.get_name(): QuarkIpamANY(),
            QuarkIpamBOTH.get_name(): QuarkIpamBOTH(),
            QuarkIpamBOTHREQ.get_name(): QuarkIpamBOTHREQ()}

    def is_valid_strategy(self, strategy_name):
        if strategy_name in self.strategies:
            return True
        return False

    def get_strategy(self, strategy_name):
        if self.is_valid_strategy(strategy_name):
            return self.strategies[strategy_name]
        fallback = CONF.QUARK.default_ipam_strategy
        LOG.warn("IPAM strategy %s not found, "
                 "using default %s" % (strategy_name, fallback))
        return self.strategies[fallback]


IPAM_REGISTRY = IpamRegistry()
