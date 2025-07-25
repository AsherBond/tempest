# Copyright 2012 OpenStack Foundation
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

import os
import tempfile

from oslo_concurrency import lockutils
from oslo_config import cfg
from oslo_config import types
from oslo_log import log as logging

from tempest.lib import exceptions
from tempest.lib.services import clients
from tempest.test_discover import plugins


# TODO(marun) Replace use of oslo_config's global ConfigOpts
# (cfg.CONF) instance with a local instance (cfg.ConfigOpts()) once
# the cli tests move to the clients.  The cli tests rely on oslo
# incubator modules that use the global cfg.CONF.
_CONF = cfg.CONF


def register_opt_group(conf, opt_group, options):
    if opt_group:
        conf.register_group(opt_group)
    for opt in options:
        conf.register_opt(opt, group=getattr(opt_group, 'name', None))


auth_group = cfg.OptGroup(name='auth',
                          title="Options for authentication and credentials")


AuthGroup = [
    cfg.StrOpt('test_accounts_file',
               help="Path to the yaml file that contains the list of "
                    "credentials to use for running tests. If used when "
                    "running in parallel you have to make sure sufficient "
                    "credentials are provided in the accounts file. For "
                    "example if no tests with roles are being run it requires "
                    "at least `2 * CONC` distinct accounts configured in "
                    " the `test_accounts_file`, with CONC == the "
                    "number of concurrent test processes."),
    cfg.BoolOpt('use_dynamic_credentials',
                default=True,
                help="Allows test cases to create/destroy projects and "
                     "users. This option requires that OpenStack Identity "
                     "API admin credentials are known. If false, isolated "
                     "test cases and parallel execution, can still be "
                     "achieved configuring a list of test accounts"),
    cfg.ListOpt('tempest_roles',
                help="Roles to assign to all users created by tempest",
                default=[]),
    cfg.StrOpt('default_credentials_domain_name',
               default='Default',
               help="Default domain used when getting v3 credentials. "
                    "This is the name keystone uses for v2 compatibility."),
    cfg.BoolOpt('create_isolated_networks',
                default=True,
                help="If use_dynamic_credentials is set to True and Neutron "
                     "is enabled Tempest will try to create a usable network, "
                     "subnet, and router when needed for each project it "
                     "creates. However in some neutron configurations, like "
                     "with VLAN provider networks, this doesn't work. So if "
                     "set to False the isolated networks will not be created"),
    cfg.StrOpt('admin_username',
               help="Username for an administrative user. This is needed for "
                    "authenticating requests made by project isolation to "
                    "create users and projects"),
    cfg.StrOpt('admin_project_name',
               help="Project name to use for an administrative user. This is "
                    "needed for authenticating requests made by project "
                    "isolation to create users and projects"),
    cfg.StrOpt('admin_password',
               help="Password to use for an administrative user. This is "
                    "needed for authenticating requests made by project "
                    "isolation to create users and projects",
               secret=True),
    cfg.StrOpt('admin_domain_name',
               default='Default',
               help="Admin domain name for authentication (Keystone V3). "
                    "The same domain applies to user and project if "
                    "admin_user_domain_name and admin_project_domain_name "
                    "are not specified"),
    cfg.StrOpt('admin_user_domain_name',
               help="Domain name that contains the admin user (Keystone V3). "
                    "May be different from admin_project_domain_name and "
                    "admin_domain_name"),
    cfg.StrOpt('admin_project_domain_name',
               help="Domain name that contains the project given by "
                    "admin_project_name (Keystone V3). May be different from "
                    "admin_user_domain_name and admin_domain_name"),
    cfg.StrOpt('admin_system',
               default=None,
               help="The system scope on which an admin user has an admin "
                    "role assignment, if any. Valid values are 'all' or None. "
                    "This must be set to 'all' if using the "
                    "[oslo_policy]/enforce_scope=true option for the "
                    "identity service."),
]

identity_group = cfg.OptGroup(name='identity',
                              title="Keystone Configuration Options")

IdentityGroup = [
    cfg.StrOpt('catalog_type',
               default='identity',
               help="Catalog type of the Identity service."),
    cfg.BoolOpt('disable_ssl_certificate_validation',
                default=False,
                help="Set to True if using self-signed SSL certificates."),
    cfg.StrOpt('ca_certificates_file',
               default=None,
               help='Specify a CA bundle file to use in verifying a '
                    'TLS (https) server certificate.'),
    cfg.URIOpt('uri',
               schemes=['http', 'https'],
               deprecated_for_removal=True,
               deprecated_reason='The identity v2 API tests were removed '
                                 'and this option has no effect',
               help="Full URI of the OpenStack Identity API (Keystone), v2"),
    cfg.URIOpt('uri_v3',
               schemes=['http', 'https'],
               help='Full URI of the OpenStack Identity API (Keystone), v3'),
    cfg.StrOpt('auth_version',
               default='v3',
               deprecated_for_removal=True,
               deprecated_reason='Identity v2 API was removed and v3 is '
                                 'the only available identity API version now',
               help="Identity API version to be used for authentication "
                    "for API tests."),
    cfg.StrOpt('region',
               default='RegionOne',
               help="The identity region name to use. Also used as the other "
                    "services' region name unless they are set explicitly. "
                    "If no such region is found in the service catalog, the "
                    "first found one is used."),
    cfg.StrOpt('v2_admin_endpoint_type',
               default='adminURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               deprecated_for_removal=True,
               deprecated_reason='This option has no effect',
               help="The admin endpoint type to use for OpenStack Identity "
                    "(Keystone) API v2"),
    cfg.StrOpt('v2_public_endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               deprecated_for_removal=True,
               deprecated_reason='This option has no effect',
               help="The public endpoint type to use for OpenStack Identity "
                    "(Keystone) API v2"),
    cfg.StrOpt('v3_endpoint_type',
               default='public',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for OpenStack Identity "
                    "(Keystone) API v3."),
    cfg.StrOpt('admin_role',
               default='admin',
               help="Role required to administrate keystone."),
    cfg.StrOpt('default_domain_id',
               default='default',
               help="ID of the default domain"),
    cfg.BoolOpt('admin_domain_scope',
                default=False,
                help="Whether keystone identity v3 policy required "
                     "a domain scoped token to use admin APIs"),
    # Security Compliance (PCI-DSS)
    cfg.IntOpt('user_lockout_failure_attempts',
               default=2,
               help="The number of unsuccessful login attempts the user is "
                    "allowed before having the account locked. This only "
                    "takes effect when identity-feature-enabled."
                    "security_compliance is set to 'True'. For more details, "
                    "refer to keystone config options keystone.conf:"
                    "security_compliance.lockout_failure_attempts. "
                    "This feature is disabled by default in keystone."),
    cfg.IntOpt('user_lockout_duration',
               default=5,
               help="The number of seconds a user account will remain "
                    "locked. This only takes "
                    "effect when identity-feature-enabled.security_compliance "
                    "is set to 'True'. For more details, refer to "
                    "keystone config options "
                    "keystone.conf:security_compliance.lockout_duration. "
                    "Setting this option will have no effect unless you also "
                    "set identity.user_lockout_failure_attempts."),
    cfg.IntOpt('user_unique_last_password_count',
               default=2,
               help="The number of passwords for a user that must be unique "
                    "before an old password can be reused. This only takes "
                    "effect when identity-feature-enabled.security_compliance "
                    "is set to 'True'. "
                    "This config option corresponds to keystone.conf: "
                    "security_compliance.unique_last_password_count, whose "
                    "default value is 0 meaning disabling this feature. "
                    "NOTE: This config option value must be same as "
                    "keystone.conf: security_compliance.unique_last_password_"
                    "count otherwise test might fail"),
    cfg.IntOpt('user_minimum_password_age',
               default=0,
               help="The number of days that a password must be used before "
                    "the user can change it. This only takes effect when "
                    "identity-feature-enabled.security_compliance is set to "
                    "'True'. For more details, refer to keystone config "
                    "options "
                    "keystone.conf:security_compliance.minimum_password_age.")
]

service_clients_group = cfg.OptGroup(name='service-clients',
                                     title="Service Clients Options")

ServiceClientsGroup = [
    cfg.IntOpt('http_timeout',
               default=60,
               help='Timeout in seconds to wait for the http request to '
                    'return'),
    cfg.StrOpt('proxy_url',
               help='Specify an http proxy to use.')
]

identity_feature_group = cfg.OptGroup(name='identity-feature-enabled',
                                      title='Enabled Identity Features')

IdentityFeatureGroup = [
    cfg.BoolOpt('trust',
                default=True,
                help='Does the identity service have delegation and '
                     'impersonation enabled'),
    cfg.BoolOpt('api_v2',
                default=False,
                deprecated_for_removal=True,
                deprecated_reason='The identity v2 API tests were removed '
                                  'and this option has no effect',
                help='Is the v2 identity API enabled'),
    cfg.BoolOpt('api_v2_admin',
                default=True,
                deprecated_for_removal=True,
                deprecated_reason='The identity v2 API tests were removed '
                                  'and this option has no effect',
                help="Is the v2 identity admin API available?"),
    cfg.BoolOpt('api_v3',
                default=True,
                deprecated_for_removal=True,
                deprecated_reason='Identity v2 API was removed and v3 is '
                                  'the only available identity API version '
                                  'now',
                help='Is the v3 identity API enabled'),
    cfg.ListOpt('api_extensions',
                default=['all'],
                help="A list of enabled identity extensions with a special "
                     "entry all which indicates every extension is enabled. "
                     "Empty list indicates all extensions are disabled. "
                     "To get the list of extensions run: "
                     "'openstack extension list --identity'"),
    cfg.BoolOpt('domain_specific_drivers',
                default=False,
                help='Are domain specific drivers enabled? '
                     'This configuration value should be same as '
                     '[identity]->domain_specific_drivers_enabled '
                     'in keystone.conf.'),
    cfg.BoolOpt('security_compliance',
                default=False,
                help='Does the environment have the security compliance '
                     'settings enabled?'),
    cfg.BoolOpt('access_rules',
                default=True,
                deprecated_for_removal=True,
                deprecated_reason='Access rules for application credentials '
                                  'is a default feature since Train',
                help='Does the environment have access rules enabled?'),
    cfg.BoolOpt('immutable_user_source',
                default=False,
                help='Set to True if the environment has a read-only '
                     'user source. This will skip all tests that attempt to '
                     'create, delete, or modify users. This should not be set '
                     'to True if using dynamic credentials')
]

compute_group = cfg.OptGroup(name='compute',
                             title='Compute Service Options')

ComputeGroup = [
    cfg.StrOpt('image_ref',
               help="Valid primary image reference to be used in tests. "
                    "This is a required option"),
    cfg.StrOpt('image_ref_alt',
               help="Valid secondary image reference to be used in tests. "
                    "This is a required option, but if only one image is "
                    "available duplicate the value of image_ref above"),
    cfg.StrOpt('certified_image_ref',
               help="Valid image reference to be used in image certificate "
                    "validation tests when enabled. This image must also "
                    "have the required img_signature_* properties set. "
                    "Additional details available within the following Nova "
                    "documentation: https://docs.openstack.org/nova/latest/"
                    "user/certificate-validation.html"),
    cfg.ListOpt('certified_image_trusted_certs',
                help="A list of trusted certificates to be used when the "
                     "image certificate validation compute feature is "
                     "enabled."),
    cfg.StrOpt('flavor_ref',
               default="1",
               help="Valid primary flavor to use in tests."),
    cfg.StrOpt('flavor_ref_alt',
               default="2",
               help='Valid secondary flavor to be used in tests.'),
    cfg.IntOpt('build_interval',
               default=1,
               help="Time in seconds between build status checks."),
    cfg.IntOpt('build_timeout',
               default=300,
               help="Timeout in seconds to wait for an instance to build. "
                    "Other services that do not define build_timeout will "
                    "inherit this value."),
    cfg.IntOpt('ready_wait',
               default=0,
               help="Additional wait time for clean state, when there is "
                    "no OS-EXT-STS extension available"),
    cfg.StrOpt('fixed_network_name',
               help="Name of the fixed network that is visible to all test "
                    "projects. If multiple networks are available for a "
                    "project, this is the network which will be used for "
                    "creating servers if tempest does not create a network or "
                    "a network is not specified elsewhere. It may be used for "
                    "ssh validation only if floating IPs are disabled."),
    cfg.StrOpt('catalog_type',
               default='compute',
               help="Catalog type of the Compute service."),
    cfg.StrOpt('region',
               default='',
               help="The compute region name to use. If empty, the value "
                    "of identity.region is used instead. If no such region "
                    "is found in the service catalog, the first found one is "
                    "used."),
    cfg.StrOpt('endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for the compute service."),
    cfg.StrOpt('volume_device_name',
               default='vdb',
               help="Expected device name when a volume is attached to "
                    "an instance. Not all hypervisors guarantee that they "
                    "will respect the user defined device name, tests may "
                    "fail if inappropriate device name is set."),
    cfg.IntOpt('shelved_offload_time',
               default=0,
               help='Time in seconds before a shelved instance is eligible '
                    'for removing from a host.  -1 never offload, 0 offload '
                    'when shelved. This configuration value should be same as '
                    'nova.conf: DEFAULT.shelved_offload_time, and '
                    'some tests will run for as long as the time.'),
    cfg.IntOpt('min_compute_nodes',
               default=1,
               help=('The minimum number of compute nodes expected. This will '
                     'be utilized by some multinode specific tests to ensure '
                     'that requests match the expected size of the cluster '
                     'you are testing with.')),
    cfg.StrOpt('hypervisor_type',
               default=None,
               help="Hypervisor type of the test target on heterogeneous "
                    "compute environment. The value can be 'QEMU', 'xen' or "
                    "something."),
    cfg.StrOpt('min_microversion',
               default=None,
               help="Lower version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Tempest selects tests based on the range between "
                    "min_microversion and max_microversion. "
                    "If both values are not specified, Tempest avoids tests "
                    "which require a microversion. Valid values are string "
                    "with format 'X.Y' or string 'latest'"),
    cfg.StrOpt('max_microversion',
               default=None,
               help="Upper version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Tempest selects tests based on the range between "
                    "min_microversion and max_microversion. "
                    "If both values are not specified, Tempest avoids tests "
                    "which require a microversion. Valid values are string "
                    "with format 'X.Y' or string 'latest'"),
    cfg.StrOpt('compute_volume_common_az',
               default=None,
               help='AZ to be used for Cinder and Nova. Set this parameter '
                    'when the cloud has nova.conf: cinder.cross_az_attach '
                    'set to false. Which means volumes attached to an '
                    'instance must be in the same availability zone in Cinder '
                    'as the instance availability zone in Nova. Set the '
                    'common availability zone in this config which will be '
                    'used to boot an instance as well as creating a volume. '
                    'NOTE: If that AZ is not in Cinder (or '
                    'allow_availability_zone_fallback=False in cinder.conf), '
                    'the volume create request will fail and the instance '
                    'will fail the build request.'),
    cfg.StrOpt('migration_source_host',
               default=None,
               help="Specify source host for live-migration, cold-migration"
                    " and resize tests. If option is not set tests will use"
                    " host automatically."),
    cfg.StrOpt('migration_dest_host',
               default=None,
               help="Specify destination host for live-migration and cold"
                    " migration. If option is not set tests will use host"
                    " automatically."),
    cfg.StrOpt('target_hosts_to_avoid',
               default='-ironic',
               help="When aggregating available hypervisors for testing,"
               " avoid migrating to and booting any test VM on hosts with"
               " a name that matches the provided pattern"),
]

placement_group = cfg.OptGroup(name='placement',
                               title='Placement Service Options')

PlacementGroup = [
    cfg.StrOpt('endpoint_type',
               default='public',
               choices=['public', 'admin', 'internal'],
               help="The endpoint type to use for the placement service."),
    cfg.StrOpt('catalog_type',
               default='placement',
               help="Catalog type of the Placement service."),
    cfg.StrOpt('region',
               default='',
               help="The placement region name to use. If empty, the value "
                    "of [identity]/region is used instead. If no such region "
                    "is found in the service catalog, the first region found "
                    "is used."),
    cfg.StrOpt('min_microversion',
               default=None,
               help="Lower version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Valid values are string with format 'X.Y' or string "
                    "'latest'"),
    cfg.StrOpt('max_microversion',
               default=None,
               help="Upper version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Valid values are string with format 'X.Y' or string "
                    "'latest'"),
]


compute_features_group = cfg.OptGroup(name='compute-feature-enabled',
                                      title="Enabled Compute Service Features")

ComputeFeaturesGroup = [
    cfg.BoolOpt('disk_config',
                default=True,
                help="If false, skip disk config tests"),
    # TODO(pkesav): Make it True by default once wallaby
    # is oldest supported stable for Tempest.
    cfg.BoolOpt('hostname_fqdn_sanitization',
                default=False,
                help="If false, skip fqdn instance sanitization tests. "
                     "Nova started sanitizing the instance name by replacing "
                     "the '.' with '-' to comply with fqdn hostname. Nova "
                     "changed that in Wallaby cycle, if your cloud is older "
                     "than wallaby then you can keep/make it False."),
    cfg.StrOpt('dhcp_domain',
               default='.novalocal',
               help="Configure a fully-qualified domain name for instance "
                    "hostnames. The value is suffixed to instance hostname "
                    "from the database to construct the hostname that "
                    "appears in the metadata API. To disable this behavior "
                    "(for example in order to correctly support "
                    "microversion's 2.94 FQDN hostnames), set this to the "
                    "empty string."),
    cfg.BoolOpt('change_password',
                default=False,
                help="Does the test environment support changing the admin "
                     "password?"),
    cfg.BoolOpt('console_output',
                default=True,
                help="Does the test environment support obtaining instance "
                     "serial console output?"),
    cfg.BoolOpt('resize',
                default=False,
                help="Does the test environment support resizing? When you "
                     "enable this feature, 'flavor_ref_alt' should be set and "
                     "it should refer to a larger flavor than 'flavor_ref' "
                     "one."),
    cfg.BoolOpt('pause',
                default=True,
                help="Does the test environment support pausing?"),
    cfg.BoolOpt('shelve',
                default=True,
                help="Does the test environment support shelving/unshelving?"),
    cfg.BoolOpt('shelve_migrate',
                default=False,
                help="Does the test environment support "
                     "cold migration of unshelved server?"),
    cfg.BoolOpt('suspend',
                default=True,
                help="Does the test environment support suspend/resume?"),
    cfg.BoolOpt('cold_migration',
                default=True,
                help="Does the test environment support cold migration?"),
    cfg.BoolOpt('live_migration',
                default=True,
                help="Does the test environment support live migration?"),
    cfg.BoolOpt('live_migrate_back_and_forth',
                default=False,
                help="Does the test environment support live migrating "
                     "VM back and forth between different versions of "
                     "nova-compute?"),
    cfg.BoolOpt('metadata_service',
                default=True,
                help="Does the test environment support metadata service? "
                     "Ignored unless validation.run_validation=true."),
    cfg.BoolOpt('block_migration_for_live_migration',
                default=False,
                help="Does the test environment use block devices for live "
                     "migration"),
    cfg.BoolOpt('can_migrate_between_any_hosts',
                default=True,
                help="Does the test environment support migrating between "
                     "any hosts? In environments with non-homogeneous compute "
                     "nodes you can set this to False so that it will select "
                     "destination host for migrating automatically"),
    cfg.BoolOpt('vnc_console',
                default=False,
                help='Enable VNC console. This configuration value should '
                     'be same as nova.conf: vnc.enabled'),
    cfg.BoolOpt('spice_console',
                default=False,
                help='Enable SPICE console. This configuration value should '
                     'be same as nova.conf: spice.enabled'),
    cfg.BoolOpt('serial_console',
                default=False,
                help='Enable serial console. This configuration value '
                     'should be the same as '
                     'nova.conf: serial_console.enabled'),
    cfg.BoolOpt('rescue',
                default=True,
                help='Does the test environment support instance rescue '
                     'mode?'),
    cfg.BoolOpt('stable_rescue',
                default=False,
                help='Does the test environment support stable device '
                     'instance rescue mode?'),
    cfg.BoolOpt('enable_instance_password',
                default=True,
                help='Enables returning of the instance password by the '
                     'relevant server API calls such as create, rebuild '
                     'or rescue. This configuration value should be same as '
                     'nova.conf: DEFAULT.enable_instance_password'),
    cfg.BoolOpt('interface_attach',
                default=True,
                help='Does the test environment support dynamic network '
                     'interface attachment?'),
    cfg.BoolOpt('snapshot',
                default=True,
                help='Does the test environment support creating snapshot '
                     'images of running instances?'),
    cfg.BoolOpt('personality',
                default=False,
                help='Does the test environment support server personality'),
    cfg.BoolOpt('attach_encrypted_volume',
                default=True,
                help='Does the test environment support attaching an '
                     'encrypted volume to a running server instance? This may '
                     'depend on the combination of compute_driver in nova and '
                     'the volume_driver(s) in cinder.'),
    cfg.BoolOpt('config_drive',
                default=True,
                help='Enable special configuration drive with metadata.'),
    cfg.ListOpt('scheduler_enabled_filters',
                default=[
                    "ComputeFilter",
                    "ComputeCapabilitiesFilter",
                    "ImagePropertiesFilter",
                    "ServerGroupAntiAffinityFilter",
                    "ServerGroupAffinityFilter",
                ],
                help="A list of enabled filters that Nova will accept as "
                     "hints to the scheduler when creating a server. If the "
                     "default value is overridden in nova.conf by the test "
                     "environment (which means that a different set of "
                     "filters is enabled than what is included in Nova by "
                     "default), then this option must be configured to "
                     "contain the same filters that Nova uses in the test "
                     "environment. A special entry 'all' indicates all "
                     "filters that are included with Nova are enabled. If "
                     "using 'all', be sure to enable all filters in "
                     "nova.conf, as tests can fail in unpredictable ways if "
                     "Nova's and Tempest's enabled filters don't match. "
                     "Empty list indicates all filters are disabled. The "
                     "full list of enabled filters is in nova.conf: "
                     "filter_scheduler.enabled_filters.",
                deprecated_opts=[cfg.DeprecatedOpt(
                    'scheduler_available_filters',
                    group='compute-feature-enabled')]),
    cfg.BoolOpt('swap_volume',
                default=False,
                help='Does the test environment support in-place swapping of '
                     'volumes attached to a server instance?'),
    cfg.BoolOpt('volume_backed_live_migration',
                default=False,
                help='Does the test environment support volume-backed live '
                     'migration?'),
    cfg.BoolOpt('volume_multiattach',
                default=False,
                help='Does the test environment support attaching a volume to '
                     'more than one instance? This depends on hypervisor and '
                     'volume backend/type and compute API version 2.60.'),
    cfg.BoolOpt('ide_bus',
                default=True,
                help='Does the test environment support attaching devices '
                     'using an IDE bus to the instance?'),
    cfg.BoolOpt('unified_limits',
                default=False,
                help='Does the test environment support unified limits?'),
]


image_group = cfg.OptGroup(name='image',
                           title="Image Service Options")

ImageGroup = [
    cfg.StrOpt('catalog_type',
               default='image',
               help='Catalog type of the Image service.'),
    cfg.StrOpt('region',
               default='',
               help="The image region name to use. If empty, the value "
                    "of identity.region is used instead. If no such region "
                    "is found in the service catalog, the first found one is "
                    "used."),
    cfg.StrOpt('endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for the image service."),
    cfg.StrOpt('alternate_image_endpoint',
               default=None,
               help="Alternate endpoint name for cross-worker testing"),
    cfg.StrOpt('alternate_image_endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help=("The endpoint type to use for the alternate image "
                     "service.")),
    cfg.BoolOpt('image_caching_enabled',
                default=False,
                help=("Flag to enable if caching is enabled by image "
                      "service, operator should set this parameter to True "
                      "if 'image_cache_dir' is set in glance-api.conf")),
    cfg.StrOpt('http_image',
               default='http://download.cirros-cloud.net/0.6.2/'
               'cirros-0.6.2-x86_64-uec.tar.gz',
               help='http accessible image'),
    cfg.StrOpt('http_qcow2_image',
               default='http://download.cirros-cloud.net/0.6.2/'
               'cirros-0.6.2-x86_64-disk.img',
               help='http qcow2 accessible image which will be used '
                    'for image conversion if enabled.'),
    cfg.IntOpt('build_timeout',
               default=300,
               help="Timeout in seconds to wait for an image to "
                    "become available."),
    cfg.IntOpt('build_interval',
               default=1,
               help="Time in seconds between image operation status "
                    "checks."),
    cfg.ListOpt('container_formats',
                default=['bare', 'ami', 'ari', 'aki', 'ovf', 'ova'],
                help="A list of image's container formats "
                     "users can specify."),
    cfg.ListOpt('disk_formats',
                default=['qcow2', 'raw', 'ami', 'ari', 'aki', 'vhd', 'vmdk',
                         'vdi', 'iso', 'vhdx'],
                help="A list of image's disk formats "
                     "users can specify."),
    cfg.StrOpt('hashing_algorithm',
               default='sha512',
               help=('Hashing algorithm used by glance to calculate image '
                     'hashes. This configuration value should be same as '
                     'glance-api.conf: hashing_algorithm config option.')),
    cfg.StrOpt('images_manifest_file',
               default=None,
               help="A path to a manifest.yml generated using the "
                    "os-test-images project"),
]

image_feature_group = cfg.OptGroup(name='image-feature-enabled',
                                   title='Enabled image service features')

ImageFeaturesGroup = [
    cfg.BoolOpt('api_v2',
                default=True,
                help="Is the v2 image API enabled",
                deprecated_for_removal=True,
                deprecated_reason='Glance v1 APIs are deprecated and v2 APIs '
                                  'are current one. In future, Tempest will '
                                  'test v2 APIs only so this config option '
                                  'will be removed.'),
    cfg.BoolOpt('import_image',
                default=True,
                help="Is image import feature enabled",
                deprecated_for_removal=True,
                deprecated_reason='Issue with image import in WSGI mode was '
                                  'fixed in Victoria, and this feature works '
                                  'in any deployment architecture now.'),
    cfg.BoolOpt('os_glance_reserved',
                default=True,
                help="Should we check that os_glance namespace is reserved",
                deprecated_for_removal=True,
                deprecated_reason='os_glance namespace is always reserved '
                                  'since Wallaby'),
    cfg.BoolOpt('manage_locations',
                default=False,
                help=('Is show_multiple_locations enabled in glance. '
                      'Note that at least one http store must be enabled as '
                      'well, because we use that location scheme to test.')),
    cfg.BoolOpt('image_conversion',
                default=False,
                help=('Is image_conversion enabled in glance.')),
    cfg.BoolOpt('image_format_enforcement',
                default=True,
                help=('Indicates that image format is enforced by glance, '
                      'such that we should not expect to be able to upload '
                      'bad images for testing other services.')),
    cfg.BoolOpt('do_secure_hash',
                default=True,
                help=('Is do_secure_hash enabled in glance. '
                      'This configuration value should be same as '
                      'glance-api.conf: do_secure_hash config option.')),
    cfg.BoolOpt('http_store_enabled',
                default=False,
                help=('Is http store is enabled in glance. '
                      'http store needs to be mentioned either in '
                      'glance-api.conf: stores or in enabled_backends '
                      'configuration option.')),
]

network_group = cfg.OptGroup(name='network',
                             title='Network Service Options')

ProfileType = types.Dict(types.List(types.String(), bounds=True))
NetworkGroup = [
    cfg.StrOpt('catalog_type',
               default='network',
               help='Catalog type of the Neutron service.'),
    cfg.StrOpt('region',
               default='',
               help="The network region name to use. If empty, the value "
                    "of identity.region is used instead. If no such region "
                    "is found in the service catalog, the first found one is "
                    "used."),
    cfg.StrOpt('endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for the network service."),
    cfg.StrOpt('project_network_cidr',
               default="10.100.0.0/16",
               help="The cidr block to allocate project ipv4 subnets from"),
    cfg.IntOpt('project_network_mask_bits',
               default=28,
               help="The mask bits for project ipv4 subnets"),
    cfg.StrOpt('project_network_v6_cidr',
               default="2001:db8::/48",
               help="The cidr block to allocate project ipv6 subnets from"),
    cfg.IntOpt('project_network_v6_mask_bits',
               default=64,
               help="The mask bits for project ipv6 subnets"),
    cfg.BoolOpt('project_networks_reachable',
                default=False,
                help="Whether project networks can be reached directly from "
                     "the test client. This must be set to True when the "
                     "'fixed' connect_method is selected."),
    cfg.StrOpt('public_network_id',
               default="",
               help="Id of the public network that provides external "
                    "connectivity"),
    cfg.StrOpt('floating_network_name',
               help="Default floating network name. Used to allocate floating "
                    "IPs when neutron is enabled."),
    cfg.StrOpt('subnet_id',
               default="",
               help="Subnet id of subnet which is used for allocation of "
                    "floating IPs. Specify when two or more subnets are "
                    "present in network."),
    cfg.StrOpt('public_router_id',
               default="",
               help="Id of the public router that provides external "
                    "connectivity. This should only be used when Neutron's "
                    "'allow_overlapping_ips' is set to 'False' in "
                    "neutron.conf. usually not needed past 'Grizzly' release"),
    cfg.IntOpt('build_timeout',
               default=300,
               help="Timeout in seconds to wait for network operation to "
                    "complete."),
    cfg.IntOpt('build_interval',
               default=1,
               help="Time in seconds between network operation status "
                    "checks."),
    cfg.StrOpt('port_vnic_type',
               choices=[None, 'normal', 'direct', 'macvtap', 'direct-physical',
                        'baremetal', 'virtio-forwarder'],
               help="vnic_type to use when launching instances"
                    " with pre-configured ports."
                    " Supported ports are:"
                    " ['normal', 'direct', 'macvtap', 'direct-physical', "
                    "'baremetal', 'virtio-forwarder']"),
    cfg.Opt('port_profile',
            type=ProfileType,
            default={},
            help="port profile to use when launching instances"
                 " with pre-configured ports."),
    cfg.ListOpt('default_network',
                default=["1.0.0.0/16", "2.0.0.0/16"],
                help="List of ip pools"
                     " for subnetpools creation"),
    cfg.BoolOpt('shared_physical_network',
                default=False,
                help="The environment does not support network separation "
                     "between tenants."),
]

network_feature_group = cfg.OptGroup(name='network-feature-enabled',
                                     title='Enabled network service features')

NetworkFeaturesGroup = [
    cfg.BoolOpt('ipv6',
                default=True,
                help="Allow the execution of IPv6 tests."),
    cfg.ListOpt('api_extensions',
                default=['all'],
                help="A list of enabled network extensions with a special "
                     "entry all which indicates every extension is enabled. "
                     "Empty list indicates all extensions are disabled. "
                     "To get the list of extensions run: "
                     "'openstack extension list --network'"),
    cfg.ListOpt('available_features',
                default=['all'],
                help="A list of available network features with a special "
                     "entry all that indicates every feature is available. "
                     "Empty list indicates all features are disabled. "
                     "This list can contain features that are not "
                     "discoverable through the API."),
    cfg.BoolOpt('ipv6_subnet_attributes',
                default=False,
                help="Allow the execution of IPv6 subnet tests that use "
                     "the extended IPv6 attributes ipv6_ra_mode "
                     "and ipv6_address_mode."
                ),
    cfg.BoolOpt('port_admin_state_change',
                default=True,
                help="Does the test environment support changing "
                     "port admin state?"),
    cfg.BoolOpt('port_security',
                default=False,
                help="Does the test environment support port security?"),
    cfg.BoolOpt('floating_ips',
                default=True,
                help='Does the test environment support floating_ips?'),
    cfg.StrOpt('qos_placement_physnet', default=None,
               help='Name of the physnet for placement based minimum '
                    'bandwidth allocation.'),
    cfg.StrOpt('provider_net_base_segmentation_id', default='3000',
               help='Base segmentation ID to create provider networks. '
                    'This value will be increased in case of conflict.'),
    cfg.BoolOpt('qos_min_bw_and_pps', default=False,
                help='Does the test environment have minimum bandwidth and '
                     'packet rate inventories configured?'),
]

dashboard_group = cfg.OptGroup(name="dashboard",
                               title="Dashboard options")

DashboardGroup = [
    cfg.URIOpt('dashboard_url',
               default='http://localhost/',
               schemes=['http', 'https'],
               help="Where the dashboard can be found"),
    cfg.BoolOpt('disable_ssl_certificate_validation',
                default=False,
                help="Set to True if using self-signed SSL certificates."),
]

validation_group = cfg.OptGroup(name='validation',
                                title='SSH Validation options')

ValidationGroup = [
    cfg.BoolOpt('run_validation',
                default=True,
                help='Enable ssh on created servers and creation of additional'
                     ' validation resources to enable remote access.'
                     ' In case the guest does not support ssh set it'
                     ' to false'),
    cfg.BoolOpt('security_group',
                default=True,
                help='Enable/disable security groups.'),
    cfg.BoolOpt('security_group_rules',
                default=True,
                help='Enable/disable security group rules.'),
    cfg.StrOpt('connect_method',
               default='floating',
               choices=[('fixed',
                         'uses the first IP belonging to the fixed network'),
                        ('floating',
                         'creates and uses a floating IP')],
               help='Default IP type used for validation'),
    cfg.StrOpt('auth_method',
               default='keypair',
               choices=['keypair'],
               help='Default authentication method to the instance. '
                    'Only ssh via keypair is supported for now. '
                    'Additional methods will be handled in a separate spec.'),
    cfg.IntOpt('ip_version_for_ssh',
               default=4,
               help='Default IP version for ssh connections.'),
    cfg.IntOpt('ping_timeout',
               default=120,
               help='Timeout in seconds to wait for ping to succeed.'),
    cfg.IntOpt('connect_timeout',
               default=60,
               help='Timeout in seconds to wait for the TCP connection to be '
                    'successful.'),
    cfg.IntOpt('ssh_timeout',
               default=300,
               help='Timeout in seconds to wait for the ssh banner.'),
    cfg.StrOpt('image_ssh_user',
               default="root",
               help="User name used to authenticate to an instance."),
    cfg.StrOpt('image_alt_ssh_user',
               default="root",
               help="User name used to authenticate to an alt instance."),
    cfg.StrOpt('image_ssh_password',
               default="password",
               help="Password used to authenticate to an instance.",
               secret=True),
    cfg.StrOpt('image_alt_ssh_password',
               default="password",
               help="Password used to authenticate to an alt instance.",
               secret=True),
    cfg.StrOpt('ssh_shell_prologue',
               default="set -eu -o pipefail; PATH=$$PATH:/sbin:/usr/sbin;",
               help="Shell fragments to use before executing a command "
                    "when sshing to a guest."),
    cfg.IntOpt('ping_size',
               default=56,
               help="The packet size for ping packets originating "
                    "from remote linux hosts"),
    cfg.IntOpt('ping_count',
               default=1,
               help="The number of ping packets originating from remote "
                    "linux hosts"),
    cfg.StrOpt('floating_ip_range',
               default='10.0.0.0/29',
               help='Unallocated floating IP range, which will be used to '
                    'test the floating IP bulk feature for CRUD operation. '
                    'This block must not overlap an existing floating IP '
                    'pool.'),
    cfg.StrOpt('network_for_ssh',
               default='public',
               help="Network used for SSH connections. Ignored if "
                    "connect_method=floating."),
    cfg.StrOpt('ssh_key_type',
               default='ecdsa',
               choices=['ecdsa', 'rsa'],
               help='Type of key to use for ssh connections.'),
    cfg.FloatOpt('allowed_network_downtime',
                 default=5.0,
                 help="Allowed VM network connection downtime during live "
                      "migration, in seconds. "
                      "When the measured downtime exceeds this value, an "
                      "exception is raised."),
    cfg.FloatOpt('allowed_metadata_downtime',
                 default=6.0,
                 help="Allowed VM metadata connection downtime during live "
                      "migration, in seconds. "
                      "When the measured downtime exceeds this value, an "
                      "exception is raised."),
]

volume_group = cfg.OptGroup(name='volume',
                            title='Block Storage Options')

VolumeGroup = [
    cfg.IntOpt('build_interval',
               default=1,
               help='Time in seconds between volume availability checks.'),
    cfg.IntOpt('build_timeout',
               default=300,
               help='Timeout in seconds to wait for a volume to become '
                    'available.'),
    cfg.StrOpt('catalog_type',
               default='block-storage',
               help="Catalog type of the Volume Service"),
    cfg.StrOpt('region',
               default='',
               help="The volume region name to use. If empty, the value "
                    "of identity.region is used instead. If no such region "
                    "is found in the service catalog, the first found one is "
                    "used."),
    cfg.StrOpt('endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for the volume service."),
    cfg.StrOpt('backup_driver',
               default='ceph',
               choices=['ceph', 'swift', 'nfs', 'glusterfs', 'posix', 'google',
                        's3'],
               help="What kind of backup_driver does cinder use?"
                    "https://docs.openstack.org/cinder/latest/configuration/"
                    "block-storage/backup-drivers.html"),
    cfg.ListOpt('backend_names',
                default=['BACKEND_1', 'BACKEND_2'],
                help='A list of backend names separated by comma. '
                     'The backend name must be declared in cinder.conf'),
    cfg.StrOpt('volume_type',
               default='',
               help='Volume type to be used while creating volume.'),
    cfg.StrOpt('volume_type_multiattach',
               default='',
               help='Multiattach volume type used while creating multiattach '
                    'volume.'),
    cfg.StrOpt('storage_protocol',
               default='iSCSI',
               help='Backend protocol to target when creating volume types'),
    cfg.StrOpt('vendor_name',
               default='Open Source',
               help='Backend vendor to target when creating volume types'),
    cfg.ListOpt('disk_format',
                default=['raw', 'qcow2'],
                help='Disk format to use when copying a volume to image'),
    cfg.IntOpt('volume_size',
               default=1,
               help='Default size in GB for volumes created by volumes tests'),
    cfg.IntOpt('volume_size_extend',
               default=1,
               help="Size in GB a volume is extended by - if a test "
                    "extends a volume, the size of the new volume will be "
                    "volume_size + volume_size_extend."),
    cfg.ListOpt('manage_volume_ref',
                default=['source-name', 'volume-%s'],
                help="A reference to existing volume for volume manage. "
                     "It contains two elements, the first is ref type "
                     "(like 'source-name', 'source-id', etc), the second is "
                     "volume name template used in storage backend"),
    cfg.ListOpt('manage_snapshot_ref',
                default=['source-name', '_snapshot-%s'],
                help="A reference to existing snapshot for snapshot manage. "
                     "It contains two elements, the first is ref type "
                     "(like 'source-name', 'source-id', etc), the second is "
                     "snapshot name template used in storage backend"),
    cfg.StrOpt('min_microversion',
               default=None,
               help="Lower version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Tempest selects tests based on the range between "
                    "min_microversion and max_microversion. "
                    "If both values are not specified, Tempest avoids tests "
                    "which require a microversion. Valid values are string "
                    "with format 'X.Y' or string 'latest'",),
    cfg.StrOpt('max_microversion',
               default=None,
               help="Upper version of the test target microversion range. "
                    "The format is 'X.Y', where 'X' and 'Y' are int values. "
                    "Tempest selects tests based on the range between "
                    "min_microversion and max_microversion. "
                    "If both values are not specified, Tempest avoids tests "
                    "which require a microversion. Valid values are string "
                    "with format 'X.Y' or string 'latest'",),
]

volume_feature_group = cfg.OptGroup(name='volume-feature-enabled',
                                    title='Enabled Cinder Features')

VolumeFeaturesGroup = [
    cfg.BoolOpt('multi_backend',
                default=False,
                help="Runs Cinder multi-backend test (requires 2 backends)"),
    cfg.BoolOpt('backup',
                default=True,
                help='Runs Cinder volumes backup test'),
    cfg.BoolOpt('snapshot',
                default=True,
                help='Runs Cinder volume snapshot test'),
    cfg.BoolOpt('clone',
                default=True,
                help='Runs Cinder volume clone test'),
    cfg.BoolOpt('manage_snapshot',
                default=False,
                help='Runs Cinder manage snapshot tests'),
    cfg.BoolOpt('manage_volume',
                default=False,
                help='Runs Cinder manage volume tests'),
    cfg.ListOpt('api_extensions',
                default=['all'],
                help='A list of enabled volume extensions with a special '
                     'entry all which indicates every extension is enabled. '
                     'Empty list indicates all extensions are disabled'),
    cfg.BoolOpt('extend_attached_volume',
                default=False,
                help='Does the cloud support extending the size of a volume '
                     'which is currently attached to a server instance? This '
                     'depends on the 3.42 volume API microversion and the '
                     '2.51 compute API microversion. Also, not all volume or '
                     'compute backends support this operation.'),
    cfg.BoolOpt('extend_attached_encrypted_volume',
                default=False,
                help='Does the cloud support extending the size of an '
                     'encrypted volume  which is currently attached to a '
                     'server instance? This depends on the 3.42 volume API '
                     'microversion and the 2.51 compute API microversion. '
                     'Also, not all volume or compute backends support this '
                     'operation.'),
    cfg.BoolOpt('extend_volume_with_snapshot',
                default=True,
                help='Does the cloud support extending the size of a volume '
                     'which has snapshot? Some drivers do not support this '
                     'operation.'),
    cfg.StrOpt('volume_types_for_data_volume',
               default=None,
               help='Volume types used for data volumes. Multiple volume '
                    'types can be assigned.'),
    cfg.BoolOpt('enable_volume_image_dep_tests',
                deprecated_name='volume_image_dep_tests',
                default=True,
                help='Run tests for dependencies between images, volumes '
                'and instance snapshots')
]


object_storage_group = cfg.OptGroup(name='object-storage',
                                    title='Object Storage Service Options')

ObjectStoreGroup = [
    cfg.StrOpt('catalog_type',
               default='object-store',
               help="Catalog type of the Object-Storage service."),
    cfg.StrOpt('region',
               default='',
               help="The object-storage region name to use. If empty, the "
                    "value of identity.region is used instead. If no such "
                    "region is found in the service catalog, the first found "
                    "one is used."),
    cfg.StrOpt('endpoint_type',
               default='publicURL',
               choices=['public', 'admin', 'internal',
                        'publicURL', 'adminURL', 'internalURL'],
               help="The endpoint type to use for the object-store service."),
    cfg.IntOpt('container_sync_timeout',
               default=600,
               help="Number of seconds to time on waiting for a container "
                    "to container synchronization complete."),
    cfg.IntOpt('container_sync_interval',
               default=5,
               help="Number of seconds to wait while looping to check the "
                    "status of a container to container synchronization"),
    cfg.StrOpt('operator_role',
               default='member',
               help="Role to add to users created for swift tests to "
                    "enable creating containers"),
    cfg.StrOpt('reseller_admin_role',
               default='ResellerAdmin',
               help="User role that has reseller admin"),
    cfg.StrOpt('realm_name',
               default='realm1',
               help="Name of sync realm. A sync realm is a set of clusters "
                    "that have agreed to allow container syncing with each "
                    "other. Set the same realm name as Swift's "
                    "container-sync-realms.conf"),
    cfg.StrOpt('cluster_name',
               default='name1',
               help="One name of cluster which is set in the realm whose name "
                    "is set in 'realm_name' item in this file. Set the "
                    "same cluster name as Swift's container-sync-realms.conf"),
    cfg.IntOpt('build_timeout',
               default=10,
               help="Timeout in seconds to wait for objects to create."),
]

object_storage_feature_group = cfg.OptGroup(
    name='object-storage-feature-enabled',
    title='Enabled object-storage features')

ObjectStoreFeaturesGroup = [
    cfg.ListOpt('discoverable_apis',
                default=['all'],
                help="A list of the enabled optional discoverable apis. "
                     "A single entry, all, indicates that all of these "
                     "features are expected to be enabled"),
    cfg.BoolOpt('container_sync',
                default=True,
                help="Execute (old style) container-sync tests"),
    cfg.BoolOpt('object_versioning',
                default=True,
                help="Execute object-versioning tests"),
    cfg.BoolOpt('discoverability',
                default=True,
                help="Execute discoverability tests"),
    cfg.StrOpt('tempurl_digest_hashlib',
               default='sha256',
               help="Hashing algorithm to use for the temp_url tests. "
                    "Needs to be supported both by Swift and the "
                    "hashlib module, for example sha1 or sha256"),
]


scenario_group = cfg.OptGroup(name='scenario', title='Scenario Test Options')

ScenarioGroup = [
    cfg.StrOpt('img_file', deprecated_name='qcow2_img_file',
               default='/opt/stack/new/devstack/files/images'
               '/cirros-0.3.1-x86_64-disk.img',
               help='Image full path.'),
    cfg.StrOpt('img_disk_format',
               default='qcow2',
               help='Image disk format'),
    cfg.StrOpt('img_container_format',
               default='bare',
               help='Image container format'),
    cfg.DictOpt('img_properties', help='Glance image properties. '
                'Use for custom images which require them'),
    cfg.StrOpt('dhcp_client',
               default='udhcpc',
               choices=["udhcpc", "dhclient", "dhcpcd", ""],
               help='DHCP client used by images to renew DHCP lease. '
                    'If left empty, update operation will be skipped. '
                    'Supported clients: "udhcpc", "dhclient", "dhcpcd"'),
    cfg.StrOpt('protocol',
               default='icmp',
               choices=('icmp', 'tcp', 'udp'),
               help='The protocol used in security groups tests to check '
                    'connectivity.'),
    cfg.StrOpt('target_dir',
               default='/tmp',
               help='Directory in which to write the timestamp file.'),
]


service_available_group = cfg.OptGroup(name="service_available",
                                       title="Available OpenStack Services")

ServiceAvailableGroup = [
    cfg.BoolOpt('cinder',
                default=True,
                help="Whether or not cinder is expected to be available"),
    cfg.BoolOpt('neutron',
                default=True,
                help="Whether or not neutron is expected to be available"),
    cfg.BoolOpt('glance',
                default=True,
                help="Whether or not glance is expected to be available"),
    cfg.BoolOpt('swift',
                default=True,
                help="Whether or not swift is expected to be available"),
    cfg.BoolOpt('nova',
                default=True,
                help="Whether or not nova is expected to be available"),
    cfg.BoolOpt('horizon',
                default=True,
                help="Whether or not horizon is expected to be available"),
]

enforce_scope_group = cfg.OptGroup(name="enforce_scope",
                                   title="OpenStack Services with "
                                         "enforce scope")


EnforceScopeGroup = [
    cfg.BoolOpt('nova',
                default=False,
                help='Does the compute service API policies enforce scope and '
                     'new defaults? This configuration value should be '
                     'enabled when nova.conf: [oslo_policy]. '
                     'enforce_new_defaults and nova.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in nova conf.'),
    cfg.BoolOpt('neutron',
                default=False,
                help='Does the network service API policies enforce scope and '
                     'new defaults? This configuration value should be '
                     'enabled when neutron.conf: [oslo_policy]. '
                     'enforce_new_defaults and neutron.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in neutron conf.'),
    cfg.BoolOpt('glance',
                default=False,
                help='Does the Image service API policies enforce scope and '
                     'new defaults? This configuration value should be '
                     'enabled when glance.conf: [oslo_policy]. '
                     'enforce_new_defaults and glance.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in glance conf.'),
    cfg.BoolOpt('cinder',
                default=False,
                help='Does the Volume service API policies enforce scope and '
                     'new defaults? This configuration value should be '
                     'enabled when cinder.conf: [oslo_policy]. '
                     'enforce_new_defaults and cinder.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in cinder conf.'),
    cfg.BoolOpt('keystone',
                default=False,
                help='Does the Identity service API policies enforce scope '
                     'and new defaults? This configuration value should be '
                     'enabled when keystone.conf: [oslo_policy]. '
                     'enforce_new_defaults and keystone.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in keystone conf.'),
    cfg.BoolOpt('placement',
                default=False,
                help='Does the placement service API policies enforce scope '
                     'and new defaults? This configuration value should be '
                     'enabled when placement.conf: [oslo_policy]. '
                     'enforce_new_defaults and nova.conf: [oslo_policy]. '
                     'enforce_scope options are enabled in placement conf.'),
]

debug_group = cfg.OptGroup(name="debug",
                           title="Debug System")

DebugGroup = [
    cfg.StrOpt('trace_requests',
               default='',
               help="""A regex to determine which requests should be traced.

This is a regex to match the caller for rest client requests to be able to
selectively trace calls out of specific classes and methods. It largely
exists for test development, and is not expected to be used in a real deploy
of tempest. This will be matched against the discovered ClassName:method
in the test environment.

Expected values for this field are:

 * ClassName:test_method_name - traces one test_method
 * ClassName:setUp(Class) - traces specific setup functions
 * ClassName:tearDown(Class) - traces specific teardown functions
 * ClassName:_run_cleanups - traces the cleanup functions

If nothing is specified, this feature is not enabled. To trace everything
specify .* as the regex.
""")
]


profiler_group = cfg.OptGroup(name="profiler",
                              title="OpenStack Profiler")

ProfilerGroup = [
    cfg.StrOpt('key',
               help="The secret key to enable OpenStack Profiler. The value "
                    "should match the one configured in OpenStack services "
                    "under `[profiler]/hmac_keys` property. The default empty "
                    "value keeps profiling disabled"),
]

DefaultGroup = [
    cfg.BoolOpt('pause_teardown',
                default=False,
                help="""Whether to pause a test in global teardown.

The best use case is investigating used resources of one test.
A test can be run as follows:
$ stestr run --pdb TEST_ID
or
$ python -m testtools.run TEST_ID"""),
    cfg.StrOpt('resource_name_prefix',
               default='tempest',
               help="Define the prefix name for the resources created by "
                    "tempest. Tempest cleanup CLI will use this config option "
                    "to cleanup only the resources that match the prefix. "
                    "Make sure this prefix does not match with the resource "
                    "name you do not want Tempest cleanup CLI to delete."),
    cfg.BoolOpt('record_resources',
                default=False,
                help="Allows to record all resources created by Tempest. "
                     "These resources are stored in file resource_list.json, "
                     "which can be later used for resource deletion by "
                     "command tempest cleanup. The resource_list.json file "
                     "will be appended in case of multiple Tempest runs, "
                     "so the file will contain a list of resources created "
                     "during all Tempest runs."),
]

_opts = [
    (auth_group, AuthGroup),
    (compute_group, ComputeGroup),
    (compute_features_group, ComputeFeaturesGroup),
    (identity_group, IdentityGroup),
    (service_clients_group, ServiceClientsGroup),
    (identity_feature_group, IdentityFeatureGroup),
    (image_group, ImageGroup),
    (image_feature_group, ImageFeaturesGroup),
    (network_group, NetworkGroup),
    (network_feature_group, NetworkFeaturesGroup),
    (dashboard_group, DashboardGroup),
    (validation_group, ValidationGroup),
    (volume_group, VolumeGroup),
    (volume_feature_group, VolumeFeaturesGroup),
    (object_storage_group, ObjectStoreGroup),
    (object_storage_feature_group, ObjectStoreFeaturesGroup),
    (scenario_group, ScenarioGroup),
    (service_available_group, ServiceAvailableGroup),
    (enforce_scope_group, EnforceScopeGroup),
    (debug_group, DebugGroup),
    (placement_group, PlacementGroup),
    (profiler_group, ProfilerGroup),
    (None, DefaultGroup)
]


def register_opts():
    ext_plugins = plugins.TempestTestPluginManager()
    # Register in-tree tempest config options
    for g, o in _opts:
        register_opt_group(_CONF, g, o)
    # Call external plugin config option registration
    ext_plugins.register_plugin_opts(_CONF)


def list_opts():
    """Return a list of oslo.config options available.

    The purpose of this is to allow tools like the Oslo sample config file
    generator to discover the options exposed to users.
    """
    ext_plugins = plugins.TempestTestPluginManager()
    # Make a shallow copy of the options list that can be
    # extended by plugins. Send back the group object
    # to allow group help text to be generated.
    opt_list = [(g, o) for g, o in _opts]
    opt_list.extend(ext_plugins.get_plugin_options_list())
    return opt_list


# This should never be called outside of this module
class TempestConfigPrivate(object):
    """Provides OpenStack configuration information."""

    DEFAULT_CONFIG_DIR = os.path.join(os.getcwd(), "etc")

    DEFAULT_CONFIG_FILE = "tempest.conf"

    def __getattr__(self, attr):
        # Handles config options from the default group
        return getattr(_CONF, attr)

    def _set_attrs(self):
        # This methods ensures that config options in Tempest as well as
        # in Tempest plugins can be accessed via:
        #     CONF.<normalised_group_name>.<key_name>
        # where:
        #     normalised_group_name = group_name.replace('-', '_')
        # Attributes are set at __init__ time *only* for known option groups
        self.auth = _CONF.auth
        self.compute = _CONF.compute
        self.compute_feature_enabled = _CONF['compute-feature-enabled']
        self.identity = _CONF.identity
        self.service_clients = _CONF['service-clients']
        self.identity_feature_enabled = _CONF['identity-feature-enabled']
        self.image = _CONF.image
        self.image_feature_enabled = _CONF['image-feature-enabled']
        self.network = _CONF.network
        self.network_feature_enabled = _CONF['network-feature-enabled']
        self.dashboard = _CONF.dashboard
        self.validation = _CONF.validation
        self.volume = _CONF.volume
        self.volume_feature_enabled = _CONF['volume-feature-enabled']
        self.object_storage = _CONF['object-storage']
        self.object_storage_feature_enabled = _CONF[
            'object-storage-feature-enabled']
        self.scenario = _CONF.scenario
        self.service_available = _CONF.service_available
        self.enforce_scope = _CONF.enforce_scope
        self.debug = _CONF.debug
        logging.tempest_set_log_file('tempest.log')
        # Setting attributes for plugins
        # NOTE(andreaf) Plugins have no access to the TempestConfigPrivate
        # instance at discovery time, so they have no way of setting these
        # aliases themselves.
        ext_plugins = plugins.TempestTestPluginManager()
        for group, _ in ext_plugins.get_plugin_options_list():
            if isinstance(group, cfg.OptGroup):
                # If we have an OptGroup
                group_name = group.name
                group_dest = group.dest
            else:
                # If we have a group name as a string
                group_name = group
                group_dest = group.replace('-', '_')
            # NOTE(andreaf) We can set the attribute safely here since in
            # case of name conflict we would not have reached this point.
            setattr(self, group_dest, _CONF[group_name])

    def __init__(self, parse_conf=True, config_path=None):
        """Initialize a configuration from a conf directory and conf file."""
        super(TempestConfigPrivate, self).__init__()
        config_files = []
        failsafe_path = "/etc/tempest/" + self.DEFAULT_CONFIG_FILE

        if config_path:
            path = config_path
        else:
            # Environment variables override defaults...
            conf_dir = os.environ.get('TEMPEST_CONFIG_DIR',
                                      self.DEFAULT_CONFIG_DIR)
            conf_file = os.environ.get('TEMPEST_CONFIG',
                                       self.DEFAULT_CONFIG_FILE)

            path = os.path.join(conf_dir, conf_file)

        if not os.path.isfile(path):
            path = failsafe_path

        # only parse the config file if we expect one to exist. This is needed
        # to remove an issue with the config file up to date checker.
        if parse_conf:
            config_files.append(path)
        logging.register_options(_CONF)
        if os.path.isfile(path):
            _CONF([], project='tempest', default_config_files=config_files)
        else:
            _CONF([], project='tempest')

        logging_cfg_path = "%s/logging.conf" % os.path.dirname(path)
        if ((not hasattr(_CONF, 'log_config_append') or
             _CONF.log_config_append is None) and
            os.path.isfile(logging_cfg_path)):
            # if logging conf is in place we need to set log_config_append
            _CONF.log_config_append = logging_cfg_path

        logging.setup(_CONF, 'tempest')
        LOG = logging.getLogger('tempest')
        LOG.info("Using tempest config file %s", path)
        register_opts()
        self._set_attrs()
        if parse_conf:
            _CONF.log_opt_values(LOG, logging.DEBUG)


class TempestConfigProxy(object):
    _config = None
    _path = None

    _extra_log_defaults = [
        ('paramiko.transport', logging.INFO),
        ('requests.packages.urllib3.connectionpool', logging.WARN),
    ]

    def _fix_log_levels(self):
        """Tweak the oslo log defaults."""
        for name, level in self._extra_log_defaults:
            logging.getLogger(name).logger.setLevel(level)

    def __getattr__(self, attr):
        if not self._config:
            self._fix_log_levels()
            lock_dir = os.path.join(tempfile.gettempdir(), 'tempest-lock')
            lockutils.set_defaults(lock_dir)
            self._config = TempestConfigPrivate(config_path=self._path)

            # Pushing tempest internal service client configuration to the
            # service clients register. Doing this in the config module ensures
            # that the configuration is available by the time we register the
            # service clients.
            # NOTE(andreaf) This has to be done at the time the first
            # attribute is accessed, to ensure all plugins have been already
            # loaded, options registered, and _config is set.
            _register_tempest_service_clients()

            # Registering service clients and pushing their configuration to
            # the service clients register. Doing this in the config module
            # ensures that the configuration is available by the time we
            # discover tests from plugins.
            plugins.TempestTestPluginManager()._register_service_clients()

        return getattr(self._config, attr)

    def set_config_path(self, path):
        self._path = path
        # FIXME(masayukig): bug#1783751 To pass the config file path to child
        # processes, we need to set the environment variables here as a
        # workaround.
        os.environ['TEMPEST_CONFIG_DIR'] = os.path.dirname(path)
        os.environ['TEMPEST_CONFIG'] = os.path.basename(path)


CONF = TempestConfigProxy()


def service_client_config(service_client_name=None):
    """Return a dict with the parameters to init service clients

    Extracts from CONF the settings specific to the service_client_name and
    api_version, and formats them as dict ready to be passed to the service
    clients __init__:

        * `region` (default to identity)
        * `catalog_type`
        * `endpoint_type`
        * `build_timeout` (object-storage and identity default to compute)
        * `build_interval` (object-storage and identity default to compute)

    The following common settings are always returned, even if
    `service_client_name` is None:

        * `disable_ssl_certificate_validation`
        * `ca_certs`
        * `trace_requests`
        * `http_timeout`
        * `proxy_url`

    The dict returned by this does not fit a few service clients:

        * The endpoint type is not returned for identity client, since it takes
          three different values for v2 admin, v2 public and v3
        * The `ServersClient` from compute accepts an optional
          `enable_instance_password` parameter, which is not returned.
        * The `VolumesClient` for both v1 and v2 volume accept an optional
          `default_volume_size` parameter, which is not returned.
        * The `TokenClient` and `V3TokenClient` have a very different
          interface, only auth_url is needed for them.

    :param service_client_name: str Name of the service. Supported values are
        'compute', 'identity', 'image', 'network', 'object-storage', 'volume'
    :return: dictionary of __init__ parameters for the service clients
    :rtype: dict
    """
    _parameters = {
        'disable_ssl_certificate_validation':
            CONF.identity.disable_ssl_certificate_validation,
        'ca_certs': CONF.identity.ca_certificates_file,
        'trace_requests': CONF.debug.trace_requests,
        'http_timeout': CONF.service_clients.http_timeout,
        'proxy_url': CONF.service_clients.proxy_url,
    }

    if service_client_name is None:
        return _parameters

    # Get the group of options first, by normalising the service_group_name
    # Services with a '-' in the name have an '_' in the option group name
    config_group = service_client_name.replace('-', '_')
    # NOTE(andreaf) Check if the config group exists. This allows for this
    # helper to be used for settings from registered plugins as well
    try:
        options = getattr(CONF, config_group)
    except cfg.NoSuchOptError:
        # Option group not defined
        raise exceptions.UnknownServiceClient(services=service_client_name)
    # Set endpoint_type
    # Identity uses different settings depending on API version, so do not
    # return the endpoint at all.
    if service_client_name != 'identity':
        _parameters['endpoint_type'] = getattr(options, 'endpoint_type')
    # Set build_*
    # Object storage and identity groups do not have conf settings for
    # build_* parameters, and we default to compute in any case
    for setting in ['build_timeout', 'build_interval']:
        if not hasattr(options, setting) or not getattr(options, setting):
            _parameters[setting] = getattr(CONF.compute, setting)
        else:
            _parameters[setting] = getattr(options, setting)
    # Set region
    # If a service client does not define region or region is not set
    # default to the identity region
    if not hasattr(options, 'region') or not getattr(options, 'region'):
        _parameters['region'] = CONF.identity.region
    else:
        _parameters['region'] = getattr(options, 'region')
    # Set service
    _parameters['service'] = getattr(options, 'catalog_type')
    return _parameters


def _register_tempest_service_clients():
    # Register tempest own service clients using the same mechanism used
    # for external plugins.
    # The configuration data is pushed to the registry so that automatic
    # configuration of tempest own service clients is possible both for
    # tempest as well as for the plugins.
    service_clients = clients.tempest_modules()
    registry = clients.ClientsRegistry()
    all_clients = []
    for service_client in service_clients:
        module = service_clients[service_client]
        configs = service_client.split('.')[0]
        service_client_data = dict(
            name=service_client.replace('.', '_').replace('-', '_'),
            service_version=service_client,
            module_path=module.__name__,
            client_names=module.__all__,
            **service_client_config(configs)
        )
        all_clients.append(service_client_data)
    # NOTE(andreaf) Internal service clients do not actually belong
    # to a plugin, so using '__tempest__' to indicate a virtual plugin
    # which holds internal service clients.
    registry.register_service_client('__tempest__', all_clients)
