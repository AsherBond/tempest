# Copyright 2014 OpenStack Foundation
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

from tempest.api.network import base
from tempest.common import utils
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib.common.utils import test_utils
from tempest.lib import decorators

CONF = config.CONF


class AllowedAddressPairTestJSON(base.BaseNetworkTest):
    """Tests the Neutron Allowed Address Pair API extension

    The following API operations are tested with this extension:

        create port
        list ports
        update port
        show port

    v2.0 of the Neutron API is assumed. It is also assumed that the following
    options are defined in the [network-feature-enabled] section of
    etc/tempest.conf

        api_extensions
    """

    @classmethod
    def skip_checks(cls):
        super(AllowedAddressPairTestJSON, cls).skip_checks()
        if not utils.is_extension_enabled('allowed-address-pairs', 'network'):
            msg = "Allowed Address Pairs extension not enabled."
            raise cls.skipException(msg)

    @classmethod
    def resource_setup(cls):
        super(AllowedAddressPairTestJSON, cls).resource_setup()
        cls.network = cls.create_network()
        cls.create_subnet(cls.network)
        port = cls.create_port(cls.network)
        cls.ip_address = port['fixed_ips'][0]['ip_address']
        cls.mac_address = port['mac_address']

    @decorators.idempotent_id('86c3529b-1231-40de-803c-00e40882f043')
    def test_create_list_port_with_address_pair(self):
        """Create and list port with allowed address pair attribute"""
        allowed_address_pairs = [{'ip_address': self.ip_address,
                                  'mac_address': self.mac_address}]
        body = self.ports_client.create_port(
            network_id=self.network['id'],
            name=data_utils.rand_name(
                self.__class__.__name__, prefix=CONF.resource_name_prefix),
            allowed_address_pairs=allowed_address_pairs)
        port_id = body['port']['id']
        self.addCleanup(self.ports_client.wait_for_resource_deletion,
                        port_id)
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.ports_client.delete_port, port_id)

        # Confirm port was created with allowed address pair attribute
        body = self.ports_client.list_ports()
        ports = body['ports']
        port = [p for p in ports if p['id'] == port_id]
        msg = 'Created port not found in list of ports returned by Neutron'
        self.assertTrue(port, msg)
        self._confirm_allowed_address_pair(port[0], self.ip_address)

    def _update_port_with_address(self, address, mac_address=None, **kwargs):
        # Create a port without allowed address pair
        body = self.ports_client.create_port(
            network_id=self.network['id'],
            name=data_utils.rand_name(
                self.__class__.__name__, prefix=CONF.resource_name_prefix))
        port_id = body['port']['id']
        self.addCleanup(self.ports_client.wait_for_resource_deletion,
                        port_id)
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.ports_client.delete_port, port_id)
        if mac_address is None:
            mac_address = self.mac_address

        # Update allowed address pair attribute of port
        allowed_address_pairs = [{'ip_address': address,
                                  'mac_address': mac_address}]
        if kwargs:
            allowed_address_pairs.append(kwargs['allowed_address_pairs'])
        body = self.ports_client.update_port(
            port_id, allowed_address_pairs=allowed_address_pairs)
        allowed_address_pair = body['port']['allowed_address_pairs']
        # NOTE(slaweq): Attribute "active" is added to the
        # allowed_address_pairs in the Xena release.
        # To make our existing allowed_address_pairs API tests to be passing in
        # both cases, with and without that "active" attribute, we need to
        # removes that field from the allowed_address_pairs which are returned
        # by the Neutron server.
        # We could make expected results of those tests to be dependent on the
        # available Neutron's API extensions but in that case existing tests
        # may fail randomly as all tests are always using same IP addresses
        # thus allowed_address_pair may be active=True or active=False.
        for pair in allowed_address_pair:
            pair.pop('active', None)
        self.assertCountEqual(allowed_address_pair, allowed_address_pairs)

    @decorators.idempotent_id('9599b337-272c-47fd-b3cf-509414414ac4')
    def test_update_port_with_address_pair(self):
        """Update port with allowed address pair"""
        self._update_port_with_address(self.ip_address)

    @decorators.idempotent_id('4d6d178f-34f6-4bff-a01c-0a2f8fe909e4')
    def test_update_port_with_cidr_address_pair(self):
        """Update allowed address pair with cidr"""
        # NOTE(slaweq): We need to use the next IP subnet to the one which
        # is configured in the tempest config as the self.cidr will include
        # "distributed" port created by the ML2/OVN backend and adding this
        # particular IP address to the allowed address pair is forbidden by
        # the ML2/OVN backend.
        self._update_port_with_address(str(self.cidr.next()))

    @decorators.idempotent_id('b3f20091-6cd5-472b-8487-3516137df933')
    def test_update_port_with_multiple_ip_mac_address_pair(self):
        """Update allowed address pair port with multiple ip and mac"""
        resp = self.ports_client.create_port(
            network_id=self.network['id'],
            name=data_utils.rand_name(
                self.__class__.__name__, prefix=CONF.resource_name_prefix))
        newportid = resp['port']['id']
        self.addCleanup(self.ports_client.wait_for_resource_deletion,
                        newportid)
        self.addCleanup(test_utils.call_and_ignore_notfound_exc,
                        self.ports_client.delete_port, newportid)
        ipaddress = resp['port']['fixed_ips'][0]['ip_address']
        macaddress = resp['port']['mac_address']

        # Update allowed address pair port with multiple ip and  mac
        allowed_address_pairs = {'ip_address': ipaddress,
                                 'mac_address': macaddress}
        self._update_port_with_address(
            self.ip_address, self.mac_address,
            allowed_address_pairs=allowed_address_pairs)

    def _confirm_allowed_address_pair(self, port, ip):
        msg = 'Port allowed address pairs should not be empty'
        self.assertTrue(port['allowed_address_pairs'], msg)
        ip_address = port['allowed_address_pairs'][0]['ip_address']
        mac_address = port['allowed_address_pairs'][0]['mac_address']
        self.assertEqual(ip_address, ip)
        self.assertEqual(mac_address, self.mac_address)


class AllowedAddressPairIpV6TestJSON(AllowedAddressPairTestJSON):
    _ip_version = 6
