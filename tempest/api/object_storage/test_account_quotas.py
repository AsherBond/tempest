# Copyright (C) 2013 eNovance SAS <licensing@enovance.com>
#
# Licensed under the Apache License, Version 2.0 (the "License"); you may
# not use this file except in compliance with the License. You may obtain
# a copy of the License at
#
#      http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS, WITHOUT
# WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied. See the
# License for the specific language governing permissions and limitations
# under the License.

from tempest.api.object_storage import base
from tempest.common import utils
from tempest import config
from tempest.lib.common.utils import data_utils
from tempest.lib import decorators
from tempest.lib import exceptions as lib_exc

CONF = config.CONF


class AccountQuotasTest(base.BaseObjectTest):
    """Test account quotas"""

    credentials = [['operator', CONF.object_storage.operator_role],
                   ['reseller', CONF.object_storage.reseller_admin_role]]

    @classmethod
    def setup_credentials(cls):
        super(AccountQuotasTest, cls).setup_credentials()
        cls.os_reselleradmin = cls.os_roles_reseller

    @classmethod
    def resource_setup(cls):
        super(AccountQuotasTest, cls).resource_setup()
        cls.container_name = cls.create_container()

        # Retrieve a ResellerAdmin auth data and use it to set a quota
        # on the client's account
        cls.reselleradmin_auth_data = \
            cls.os_reselleradmin.auth_provider.auth_data

    def setUp(self):
        super(AccountQuotasTest, self).setUp()

        # Set the reselleradmin auth in headers for next account_client
        # request
        self.account_client.auth_provider.set_alt_auth_data(
            request_part='headers',
            auth_data=self.reselleradmin_auth_data
        )
        # Set a quota of 20 bytes on the user's account before each test
        self.set_quota = 20
        headers = {"X-Account-Meta-Quota-Bytes": self.set_quota}

        self.os_roles_operator.account_client.request(
            "POST", url="", headers=headers, body="")

    def tearDown(self):
        # Set the reselleradmin auth in headers for next account_client
        # request
        self.account_client.auth_provider.set_alt_auth_data(
            request_part='headers',
            auth_data=self.reselleradmin_auth_data
        )
        # remove the quota from the container
        headers = {"X-Remove-Account-Meta-Quota-Bytes": "x"}

        self.os_roles_operator.account_client.request(
            "POST", url="", headers=headers, body="")
        super(AccountQuotasTest, self).tearDown()

    @classmethod
    def resource_cleanup(cls):
        cls.delete_containers()
        super(AccountQuotasTest, cls).resource_cleanup()

    @decorators.attr(type="smoke")
    @decorators.idempotent_id('a22ef352-a342-4587-8f47-3bbdb5b039c4')
    @utils.requires_ext(extension='account_quotas', service='object')
    def test_upload_valid_object(self):
        """Test uploading valid object"""
        object_name = data_utils.rand_name(
            prefix=CONF.resource_name_prefix, name="TestObject")
        data = data_utils.arbitrary_string()
        resp, _ = self.object_client.create_object(self.container_name,
                                                   object_name, data)

        self.assertHeaders(resp, 'Object', 'PUT')

    @decorators.attr(type="smoke")
    @decorators.idempotent_id('93fd7776-ae41-4949-8d0c-21889804c1ca')
    @utils.requires_ext(extension='account_quotas', service='object')
    def test_overlimit_upload(self):
        """Test uploading an oversized object raises an OverLimit exception"""
        object_name = data_utils.rand_name(
            prefix=CONF.resource_name_prefix, name="TestObject")
        data = data_utils.arbitrary_string(self.set_quota + 1)

        nbefore = self._get_bytes_used()

        self.assertRaises(lib_exc.OverLimit,
                          self.object_client.create_object,
                          self.container_name, object_name, data)

        nafter = self._get_bytes_used()
        self.assertEqual(nbefore, nafter)

    @decorators.attr(type=["smoke"])
    @decorators.idempotent_id('63f51f9f-5f1d-4fc6-b5be-d454d70949d6')
    @utils.requires_ext(extension='account_quotas', service='object')
    def test_admin_modify_quota(self):
        """Test ResellerAdmin can modify/remove the quota on a user's account

        Using the account client, the test modifies the quota
        successively to:

        * "25": a random value different from the initial quota value.
        * ""  : an empty value, equivalent to the removal of the quota.
        * "20": set the quota to its initial value.
        """
        for quota in ("25", "", "20"):

            self.account_client.auth_provider.set_alt_auth_data(
                request_part='headers',
                auth_data=self.reselleradmin_auth_data
            )
            headers = {"X-Account-Meta-Quota-Bytes": quota}

            resp, _ = self.os_roles_operator.account_client.request(
                "POST", url="", headers=headers, body="")

            self.assertEqual(resp["status"], "204")
            self.assertHeaders(resp, 'Account', 'POST')

    def _get_account_metadata(self):
        resp, _ = self.account_client.list_account_metadata()
        return resp

    def _get_bytes_used(self):
        resp = self._get_account_metadata()
        return int(resp["x-account-bytes-used"])
