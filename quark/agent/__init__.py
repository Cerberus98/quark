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


from oslo.config import cfg

from quark.agent import noop_agent
from quark.agent import noop_discovery

CONF = cfg.CONF
quark_agent_opts = [
    cfg.StrOpt('agent_driver',
               default="NoOpAgent",
               help=_("Agent driver to use")),
    cfg.StrOpt('agent_discovery',
               default="NoOpDiscovery",
               help=_("Agent discovery service to use"))
]

CONF.register_opts(quark_agent_opts, "QUARK")

AGENTS = {
    noop_agent.NAME.lower(): noop_agent.NoOpAgent
}

DISCOVERY = {
    noop_discovery.NAME.lower(): noop_discovery.NoOpDiscovery
}


class AgentNotifier(object):
    def __init__(self):
        self.notifier = AGENTS[CONF.QUARK.agent_driver.lower()]()
        self.discovery = DISCOVERY[CONF.QUARK.agent_discovery.lower()]()

    def notify_device_updated(self, device_id):
        host, queue = self.discovery.get_device_queue(device_id)
        self.notifier.notify_device_updated(host, queue, device_id)

    def notify(self):
        host, queue = self.discovery.get_broadcast_queue()
        self.notifier.notify(host, queue)


AGENT_API = AgentNotifier()
