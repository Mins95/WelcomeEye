"""Synthetic persisted entries: presentation choices never perform device I/O."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from load_integration import load
from test_connect3_config import flow

policy = load('opening_commands')
PRIMARY = {'strike_1_enabled', 'gate_1_enabled'}
SECONDARY = {'strike_2_enabled', 'gate_2_enabled'}
CONSENTS = tuple(policy.SECONDARY_CONSENTS.values())


class OpeningPolicyTests(unittest.TestCase):
    def test_older_primary_defaults_and_literal_boolean_preferences(self):
        self.assertTrue(policy.command_enabled({}, 'strike_1'))
        self.assertTrue(policy.command_enabled({}, 'gate_1'))
        self.assertFalse(policy.command_enabled({}, 'strike_2'))
        for value in (False, 1, 'true', None):
            data = {'strike_1_enabled': value}
            self.assertFalse(policy.command_enabled(data, 'strike_1'))
            self.assertTrue(policy.command_enabled(data, 'gate_1'))
        self.assertFalse(policy.command_enabled({}, 'unknown'))

    def test_secondary_migration_requires_both_panel_and_exact_consent(self):
        old = {'second_channel_enabled': True, CONSENTS[0]: True}
        before = deepcopy(old)
        self.assertTrue(policy.command_enabled(old, 'strike_2'))
        self.assertFalse(policy.command_enabled(old, 'gate_2'))
        self.assertEqual(policy.secondary_preferences(old), {'strike_2_enabled': True})
        self.assertEqual(old, before)
        for data in ({CONSENTS[0]: True}, {**old, 'second_channel_enabled': False},
                     {**old, 'strike_2_enabled': False},
                     {'second_channel_enabled': True, 'strike_2_enabled': True}):
            self.assertFalse(policy.command_enabled(data, 'strike_2'))

    def test_only_connect3_reuses_bound_channel_observations(self):
        channels = load('connect3.channels')
        data = {'protocol_family': 'connect3_qv_experimental', 'host': '192.0.2.1',
                'certificate_sha256': 'a' * 64, CONSENTS[0]: True}
        data['observed_media_channels'] = {'binding': channels.media_profile_binding(data),
                                          'channels': [1, 2]}
        self.assertTrue(policy.command_enabled(data, 'strike_2'))
        self.assertFalse(policy.command_enabled({**data, 'second_channel_enabled': False}, 'strike_2'))
        self.assertFalse(policy.command_enabled({**data, 'protocol_family': 'r002_experimental'}, 'strike_2'))


class OpeningCommandsFlowTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, variant, **extra):
        instance = flow()
        family = ('connect3_qv_experimental' if variant == 'connect3' else
                  'r002_experimental' if variant == 'connect2_r002' else 'legacy_owsp')
        data = {'host': '192.0.2.1', 'device_variant': variant,
            'protocol_family': family, 'password': 'SYNTHETIC_PASSWORD',
            'auth_code': 'SYNTHETIC_AUTH', 'opening_code': 'SYNTHETIC_OPENING',
            'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
            'experimental_video': True, 'experimental_outputs': True,
            'media_transport': 'tls', 'cgi_port': 443, 'media_port': 8443,
            'second_channel_enabled': True,
            'trust_endpoint': {'host': '192.0.2.1', 'cgi_port': 443,
                'media_port': 8443, 'media_transport': 'tls'},
            **extra}
        entry = SimpleNamespace(data=data, entry_id='fixture', unique_id='retained',
            title='Custom monitor name', options={'existing_snapshot_preference': False})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    def assert_no_network(self, instance):
        instance.test_module.inspect_trust.assert_not_called()
        instance.test_module.probe_tcp_setup.assert_not_called()
        instance.test_module.discover_candidates.assert_not_called()
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_independent_primary_choices_all_families_and_no_secret_resubmission(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            instance, entry = self.configured(variant)
            original = deepcopy(entry.data)
            result = await instance.async_step_reconfigure({'opening_commands': {
                'strike_1_enabled': False, 'gate_1_enabled': True}})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertFalse(policy.command_enabled(entry.data, 'strike_1'))
            self.assertTrue(policy.command_enabled(entry.data, 'gate_1'))
            for key in ('auth_code', 'opening_code', 'password', 'certificate_sha256',
                        'media_certificate_sha256', 'trust_endpoint'):
                self.assertEqual(entry.data[key], original[key])
            self.assertEqual(entry.unique_id, 'retained')
            self.assertEqual(entry.options, {'existing_snapshot_preference': False})
            self.assertEqual(entry.title, 'Custom monitor name')
            self.assert_no_network(instance)
            instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_section_exposes_secondary_only_with_second_panel(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            for enabled in (False, True):
                instance, _ = self.configured(variant, second_channel_enabled=enabled)
                form = await instance.async_step_reconfigure()
                fields = set(form['data_schema']['opening_commands'])
                self.assertEqual(fields, PRIMARY | (SECONDARY if enabled else set()))
                self.assertNotIn('SYNTHETIC', repr(form))
                self.assert_no_network(instance)

    async def test_secondary_enable_requires_separate_confirmation_for_each_target(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            instance, entry = self.configured(variant)
            original = deepcopy(entry.data)
            result = await instance.async_step_reconfigure({'opening_commands': {
                'strike_1_enabled': False, 'gate_1_enabled': True,
                'strike_2_enabled': True, 'gate_2_enabled': False}})
            self.assertEqual(result['type'], 'form')
            self.assertEqual(set(result['data_schema']), {CONSENTS[0]})
            self.assertEqual(entry.data, original)
            confirm = (instance.async_step_connect3_output_trials if variant == 'connect3'
                       else instance.async_step_secondary_output_trials)
            await confirm({CONSENTS[0]: True})
            self.assertTrue(policy.command_enabled(entry.data, 'strike_2'))
            self.assertFalse(policy.command_enabled(entry.data, 'gate_2'))
            self.assertFalse(policy.command_enabled(entry.data, 'strike_1'))
            self.assertTrue(policy.command_enabled(entry.data, 'gate_1'))
            self.assert_no_network(instance)
            instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_declined_secondary_enable_is_atomic_and_does_not_clear_tls_repair(self):
        for variant in ('connect2_r001', 'connect3'):
            instance, entry = self.configured(variant)
            original = deepcopy(entry.data)
            await instance.async_step_reconfigure({'opening_commands': {
                'strike_1_enabled': False, 'strike_2_enabled': True}})
            confirm = (instance.async_step_connect3_output_trials if variant == 'connect3'
                       else instance.async_step_secondary_output_trials)
            result = await confirm({CONSENTS[0]: False})
            self.assertEqual(result['reason'], 'connect3_output_trial_declined')
            self.assertEqual(entry.data, original)
            instance.test_module.async_clear_tls_issue.assert_not_called()
            self.assert_no_network(instance)

    async def test_panel_disable_retains_choices_revokes_consent_and_reenable_requires_approval(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            instance, entry = self.configured(variant, **{field: True for field in CONSENTS})
            await instance.async_step_reconfigure({'second_channel_enabled': False})
            self.assertTrue(entry.data['strike_2_enabled'])
            self.assertTrue(entry.data['gate_2_enabled'])
            self.assertFalse(any(entry.data[field] for field in CONSENTS))
            self.assertFalse(policy.command_enabled(entry.data, 'strike_2'))
            self.assertFalse(policy.command_enabled(entry.data, 'gate_2'))
            await instance.async_step_reconfigure({'second_channel_enabled': True})
            self.assertFalse(policy.command_enabled(entry.data, 'strike_2'))
            self.assertFalse(policy.command_enabled(entry.data, 'gate_2'))
            result = await instance.async_step_reconfigure({'opening_commands': {
                'strike_2_enabled': True, 'gate_2_enabled': False}})
            self.assertEqual(set(result['data_schema']), {CONSENTS[0]})

    async def test_invalid_boolean_never_writes_any_preference(self):
        for variant in ('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'):
            for value in (1, None, 'true'):
                instance, entry = self.configured(variant)
                original = deepcopy(entry.data)
                result = await instance.async_step_reconfigure({'opening_commands': {
                    'strike_1_enabled': False, 'gate_1_enabled': value}})
                self.assertTrue(result['errors'])
                self.assertEqual(entry.data, original)
                self.assert_no_network(instance)

    async def test_complete_connect3_form_changes_only_choices_without_tls_or_secret_loss(self):
        instance, entry = self.configured('connect3', experimental_tcp_controls=False,
            experimental_channel2_microphone=False, **{CONSENTS[0]: True})
        original = deepcopy(entry.data)
        submitted = {'host': entry.data['host'], 'auth_code': '', 'opening_code': '',
            'experimental_video': True, 'second_channel_enabled': True,
            'opening_commands': {'strike_1_enabled': False, 'gate_1_enabled': True,
                'strike_2_enabled': True, 'gate_2_enabled': False},
            'advanced': {'cgi_port': 443, 'media_port': 8443,
                'media_transport': 'tls', 'experimental_outputs': True,
                'experimental_tcp_controls': False, 'clear_credentials': False,
                'installation_qr': '', 'certificate_sha256': '',
                'media_certificate_sha256': '', CONSENTS[0]: True, CONSENTS[1]: False,
                'experimental_channel2_microphone': False}}
        result = await instance.async_step_reconfigure(submitted)
        self.assertEqual(result['reason'], 'reconfigure_successful')
        self.assertFalse(policy.command_enabled(entry.data, 'strike_1'))
        self.assertTrue(policy.command_enabled(entry.data, 'gate_1'))
        self.assertTrue(policy.command_enabled(entry.data, 'strike_2'))
        self.assertFalse(policy.command_enabled(entry.data, 'gate_2'))
        for key, value in original.items():
            self.assertEqual(entry.data[key], value)
        self.assertEqual(entry.unique_id, 'retained')
        self.assert_no_network(instance)
        instance.test_module.async_clear_tls_issue.assert_not_called()

    async def test_primary_choice_does_not_bypass_global_output_consent(self):
        for variant in ('connect2_r002', 'connect3'):
            instance, entry = self.configured(variant, experimental_outputs=False)
            await instance.async_step_reconfigure({'opening_commands': {
                'strike_1_enabled': False, 'gate_1_enabled': True}})
            self.assertFalse(entry.data['experimental_outputs'])
            self.assert_no_network(instance)

    async def test_revoking_global_permission_preserves_preferences_but_cannot_reactivate(self):
        for variant in ('connect2_r002', 'connect3'):
            instance, entry = self.configured(variant, **{field: True for field in CONSENTS})
            await instance.async_step_reconfigure({'experimental_outputs': False})
            self.assertTrue(entry.data['strike_2_enabled'])
            self.assertTrue(entry.data['gate_2_enabled'])
            self.assertFalse(any(entry.data[field] for field in CONSENTS))
            self.assertFalse(policy.command_enabled(entry.data, 'strike_2'))
            await instance.async_step_reconfigure({'experimental_outputs': True})
            self.assertFalse(policy.command_enabled(entry.data, 'strike_2'))

    async def test_advanced_and_root_legacy_submission_remain_compatible(self):
        instance, entry = self.configured('connect3')
        await instance.async_step_reconfigure({'opening_commands': {'strike_1_enabled': True},
            'strike_1_enabled': False})
        self.assertFalse(entry.data['strike_1_enabled'])
        self.assertNotIn('opening_commands', entry.data)
        self.assert_no_network(instance)
