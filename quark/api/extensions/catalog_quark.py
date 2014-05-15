# Copyright (c) 2013 OpenStack Foundation
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#    http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or
# implied.
# See the License for the specific language governing permissions and
# limitations under the License.

from oslo.config import cfg
from neutron.api import extensions
from neutron import manager
from neutron import wsgi

RESOURCE_NAME = 'catalog'
RESOURCE_COLLECTION = "variables"
CONF = cfg.CONF

EXTENDED_ATTRIBUTES_2_0 = {
RESOURCE_COLLECTION: {}
}


class QuarkCatalogController(wsgi.Controller):
    def __init__(self, plugin):
        self._plugin = plugin

    def index(self, request):
        return {"catalog": [opt for opt in cfg.CONF]}

    def show(self, request, id):
        val = cfg.CONF.get(id)
        if isinstance(val, cfg.ConfigOpts.GroupAttr):
            return {"catalog": cfg.CONF[id].items()}
        elif val is not None:
            return {"catalog": cfg.CONF[id]}
        else:
            return {}


class Catalog_quark(object):
    @classmethod
    def get_name(cls):
        return "Quark Catalog API Extension"

    @classmethod
    def get_alias(cls):
        return "config"

    @classmethod
    def get_description(cls):
        return "Quark Catalog API Extension"

    @classmethod
    def get_namespace(cls):
        return ("http://docs.openstack.org/network/ext/"
                "quark_catalog/api/v2.0")

    @classmethod
    def get_updated(cls):
        return "2014-05-15T22:00:00-00:00"

    def get_extended_resources(self, version):
        if version == "2.0":
            return EXTENDED_ATTRIBUTES_2_0
        else:
            return {}

    @classmethod
    def get_resources(cls):
        """Returns Ext Resources."""
        plugin = manager.NeutronManager.get_plugin()
        controller = QuarkCatalogController(plugin)
        extension = extensions.ResourceExtension(Catalog_quark.get_alias(),
                                                 controller)
        return [extension]
