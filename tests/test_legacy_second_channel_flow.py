"""Second-panel preferences retain legacy identities and unrelated settings."""
from copy import deepcopy
from types import SimpleNamespace
import unittest

from load_integration import cap
from test_connect3_config import flow
import test_r002_config_flow as legacy_tests


class LegacySecondChannelFlowTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, variant, **options):
        instance = flow()
        entry = SimpleNamespace(entry_id='existing', unique_id='retained', title='Intercom',
            options={'ring_image_capture': False}, data={
                'host': '192.0.2.1', 'username': 'SYNTHETIC_USER', 'password': 'SYNTHETIC_PASSWORD',
                'protocol_family': cap.family_for(variant), 'device_variant': variant,
                'v1_cloud_doorbell_enabled': True, **options})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    async def test_legacy_setup_accepts_boolean_without_channel_probe(self):
        for value in (False, True):
            instance = legacy_tests.FlowTests().flow()
            result = await instance.async_step_legacy({'host': '192.0.2.1', 'username': 'admin',
                'password': 'SYNTHETIC_PASSWORD', 'second_channel_enabled': value})
            self.assertEqual(result['type'], 'create_entry')
            self.assertIs(result['data']['second_channel_enabled'], value)
            instance.hass.async_add_executor_job.assert_awaited_once()
        for value in ('true', 1, None):
            instance = legacy_tests.FlowTests().flow()
            result = await instance.async_step_legacy({'host': '192.0.2.1', 'username': 'admin',
                'password': 'SYNTHETIC_PASSWORD', 'second_channel_enabled': value})
            self.assertEqual(result['type'], 'form')
            instance.hass.async_add_executor_job.assert_not_called()

    async def test_legacy_reconfigure_updates_only_option_without_network(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001, cap.DeviceVariant.LEGACY_UNKNOWN):
            instance, entry = self.configured(variant)
            before = deepcopy(entry.data)
            form = await instance.async_step_reconfigure()
            self.assertIn('second_channel_enabled', form['data_schema'])
            self.assertNotIn('SYNTHETIC', repr(form))
            for value in (True, False):
                result = await instance.async_step_reconfigure({'second_channel_enabled': value,
                    'password': 'INJECTED', 'host': '192.0.2.2'})
                self.assertEqual(result['reason'], 'reconfigure_successful')
                self.assertEqual(entry.data, {**before, 'second_channel_enabled': value})
                self.assertEqual(entry.unique_id, 'retained')
                self.assertEqual(entry.title, 'Intercom')
                self.assertEqual(entry.options, {'ring_image_capture': False})
            instance.hass.async_add_executor_job.assert_not_called()
            instance.test_module.inspect_trust.assert_not_called()
            instance.test_module.discover_candidates.assert_not_called()

    async def test_omission_preserves_existing_enabled_options_and_invalid_input_does_not_write(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001, cap.DeviceVariant.LEGACY_UNKNOWN):
            instance, entry = self.configured(variant, second_channel_enabled=True)
            before = deepcopy(entry.data)
            await instance.async_step_reconfigure({})
            self.assertEqual(entry.data, before)
            for value in ('true', 1, None):
                result = await instance.async_step_reconfigure({'second_channel_enabled': value})
                self.assertEqual(result['type'], 'form')
                self.assertTrue(result['errors'])
                self.assertEqual(entry.data, before)

    async def test_r002_second_panel_is_explicit_and_never_inferred_from_c3_observations(self):
        from load_integration import load
        for choices in ({}, {'second_channel_enabled': False}):
            instance, entry = self.configured(cap.DeviceVariant.R002, experimental_video=True)
            entry.data['observed_media_channels'] = {
                'binding': load('connect3.channels').media_profile_binding(entry.data),
                'channels': [1, 2]}
            form = await instance.async_step_reconfigure()
            self.assertIn('second_channel_enabled', form['data_schema'])
            result = await instance.async_step_reconfigure({'host': '192.0.2.1', **choices})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertFalse(entry.data['second_channel_enabled'])
            current = load('r002.hub').R002InvestigationHub(None, entry)
            self.assertIsNone(current.channel2)
            self.assertEqual(current.confirmed_media_channels, frozenset())
            await current.stop()
            result = await instance.async_step_reconfigure({
                'host': '192.0.2.1', 'second_channel_enabled': True})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertTrue(entry.data['second_channel_enabled'])
            current = load('r002.hub').R002InvestigationHub(None, entry)
            self.assertIsNotNone(current.channel2)
            self.assertIsNone(current.channel2.live.task)
            for target in ('strike_2', 'gate_2'):
                self.assertFalse(current.control.target_enabled(target))
            await current.stop()
            self.assertEqual(entry.unique_id, 'retained')
            self.assertEqual(entry.data['device_variant'], cap.DeviceVariant.R002)
            instance.hass.async_add_executor_job.assert_not_called()
            instance.test_module.inspect_trust.assert_not_called()
            instance.test_module.discover_candidates.assert_not_called()
            rejected = await instance.async_step_legacy_reconfigure({'second_channel_enabled': True})
            self.assertEqual(rejected['reason'], 'reconfigure_not_supported')


if __name__ == '__main__':
    unittest.main()
