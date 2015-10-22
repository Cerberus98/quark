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

import json

from oslo_config import cfg

from quark import network_strategy
from quark.tests import test_base


class TestJSONStrategy(test_base.TestBase):
    def setUp(self):
        self.context = None
        self.strategy = {"public_network": {"bridge": "xenbr0",
                                            "subnets": ["public"]}}
        strategy_json = json.dumps(self.strategy)
        cfg.CONF.set_override("default_net_strategy", strategy_json, "QUARK")

    def test_get_network(self):
        json_strategy = network_strategy.JSONStrategy()
        net = json_strategy.get_network(self.context, "public_network")
        self.assertEqual(net["bridge"], "xenbr0")

    def test_split_network_ids(self):
        json_strategy = network_strategy.JSONStrategy()
        net_ids = ["foo_net", "public_network"]
        tenant, assignable = json_strategy.split_network_ids(self.context,
                                                             net_ids)
        self.assertTrue("foo_net" in tenant)
        self.assertTrue("foo_net" not in assignable)
        self.assertTrue("public_network" not in tenant)
        self.assertTrue("public_network" in assignable)
