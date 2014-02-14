# Copyright 2014 Openstack Foundation
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

from neutron.openstack.common import log as logging
from oslo.config import cfg


CONF = cfg.CONF
quark_agent_opts = [
    cfg.StrOpt('notification_host',
               default="localhost",
               help=_("Host to publish messages to")),
    cfg.StrOpt('broadcast_queue',
               default="broadcast_queue",
               help=_("Queue for broadcast notifications")),
    cfg.StrOpt('device_queue',
               default="device_queue",
               help=_("Queue for device notifications"))
]

CONF.register_opts(quark_agent_opts, "QUARK")
LOG = logging.getLogger(__name__)
NAME = "NoOpDiscovery"


class NoOpDiscovery(object):
    def get_broadcast_queue(self):
        return (CONF.QUARK.notification_host,
                CONF.QUARK.broadcast_queue)

    def get_device_queue(self, device_id):
        return (CONF.QUARK.notification_host,
                CONF.quark.device_queue)
