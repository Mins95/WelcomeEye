"""Auxiliary input is video/photo only; synthetic hubs, no device access."""
from copy import deepcopy
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from load_integration import cap, load
from test_legacy_multichannel import Hub
import test_legacy_second_channel_flow as secondary_flow
import test_r002_config_flow as legacy_flow

legacy = load('legacy_channel')
opening = load('opening_commands')


class AuxiliarySourceTests(unittest.IsolatedAsyncioTestCase):
    async def test_saved_secondary_permissions_never_grant_auxiliary_relays_or_microphone(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001):
            entry = SimpleNamespace(entry_id='synthetic', data={
                'host': '192.0.2.1', 'channel': 16, 'device_variant': variant,
                'second_channel_enabled': True, 'second_channel_input': 3,
                'experimental_channel2_microphone': True,
                'channel2_strike_trial_enabled': True, 'channel2_gate_trial_enabled': True})
            hub = Hub(SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock())), entry)
            hub.stopped = False
            try:
                self.assertEqual(hub.channel2.wire_channel, 18)
                self.assertTrue(hub.channel2.capabilities.manual_snapshot)
                self.assertFalse(hub.channel2.capabilities.talkback)
                self.assertFalse(legacy.secondary_microphone_enabled(hub))
                for target in ('strike_2', 'gate_2'):
                    self.assertFalse(opening.command_enabled(entry.data, target))
                    self.assertEqual(hub.control.target_status(target)['reason'],
                                     'auxiliary_camera_outputs_not_supported')
                    with self.assertRaisesRegex(load('control').ProtocolError, 'auxiliary_camera'):
                        await hub.control.unlock_target(target)
                with self.assertRaises(load('talkback').ProtocolError):
                    await hub.channel2.talkback.start(object())
                self.assertTrue(hub.control.target_enabled('strike_1'))
                self.assertTrue(hub.control.target_enabled('gate_1'))
                diag = hub.channel2.diagnostics()
                self.assertEqual(diag['physical_input'], 3)
                self.assertEqual(diag['source_kind'], 'auxiliary_camera')
                self.assertIsNone(diag['microphone']['wire_audio_channel'])
                self.assertIsNone(hub.thread)
                self.assertEqual(hub.control.request_send_attempt_count, 0)
            finally:
                await hub.stop()

    async def test_auxiliary_selection_does_not_change_local_or_cloud_ring_identity(self):
        entry = SimpleNamespace(entry_id='synthetic', unique_id='synthetic_uid', data={
            'host': '192.0.2.1', 'device_variant': cap.DeviceVariant.R001,
            'second_channel_enabled': True, 'second_channel_input': 3})
        events = []
        hub = Hub(SimpleNamespace(bus=SimpleNamespace(async_fire=lambda _, data: events.append(data))), entry)
        hub.stopped = False
        try:
            for channel in (1, 16, 2, 17, 3, 18):
                hub.ring_image.request.reset_mock()
                hub.ring_image.reject_unsupported_channel.reset_mock()
                hub._ring(SimpleNamespace(channel=channel))
                self.assertEqual(events[-1]['channel'], channel)
                self.assertEqual(hub.ring_image.request.called, channel in (1, 16))
                self.assertEqual(hub.ring_image.reject_unsupported_channel.called, channel not in (1, 16))
            entry.data.update(device_variant=cap.DeviceVariant.V1, v1_cloud_doorbell_enabled=True)
            hub._v1_cloud_uid = entry.unique_id
            for channel in (1, 2):
                hub.ring_image.request.reset_mock()
                hub._cloud_ring(channel)
                self.assertEqual(events[-1]['channel'], channel)
                self.assertEqual(hub.ring_image.request.called, channel == 1)
            self.assertIsNone(hub.thread)
        finally:
            await hub.stop()

    async def test_reconfigure_revokes_permissions_in_both_directions_without_changing_identity(self):
        for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001):
            flow, entry = secondary_flow.LegacySecondChannelFlowTests().configured(variant,
                second_channel_enabled=True, second_channel_input=2,
                experimental_channel2_microphone=True,
                channel2_strike_trial_enabled=True, channel2_gate_trial_enabled=True)
            original = deepcopy(entry.data)
            for value in ('3', '2'):
                result = await flow.async_step_reconfigure({'second_channel_input': value,
                    'experimental_channel2_microphone': True,
                    'channel2_strike_trial_enabled': True, 'channel2_gate_trial_enabled': True})
                self.assertEqual(result['reason'], 'reconfigure_successful')
                self.assertEqual(entry.data['second_channel_input'], int(value))
                for field in ('experimental_channel2_microphone', 'channel2_strike_trial_enabled',
                              'channel2_gate_trial_enabled'):
                    self.assertFalse(entry.data[field])
                self.assertEqual(entry.unique_id, 'retained')
                for field in ('host', 'username', 'password'):
                    self.assertEqual(entry.data[field], original[field])
                if value == '3':
                    form = await flow.async_step_reconfigure()
                    self.assertNotIn('experimental_channel2_microphone', form['data_schema'])
                    self.assertNotIn('strike_2_enabled', form['data_schema']['opening_commands'])
            flow.hass.async_add_executor_job.assert_not_called()
            # Returning to a panel does not restore approvals; a new relay choice
            # goes through the existing separate confirmation screen.
            result = await flow.async_step_reconfigure({'strike_2_enabled': True})
            self.assertEqual(result['step_id'], 'secondary_output_trials')

    async def test_selector_validation_and_normalization_before_connection_or_save(self):
        for value in ('2', '3', 2, 3):
            flow = legacy_flow.FlowTests().flow()
            result = await flow.async_step_legacy({'host': '192.0.2.1', 'username': 'synthetic',
                'password': 'synthetic', 'second_channel_input': value})
            self.assertEqual(result['data']['second_channel_input'], int(value))
        for value in (True, False, None, 18, 3.0, '18', '3.0', '', []):
            flow = legacy_flow.FlowTests().flow()
            result = await flow.async_step_legacy({'host': '192.0.2.1', 'username': 'synthetic',
                'password': 'synthetic', 'second_channel_input': value})
            self.assertEqual(result['type'], 'form')
            flow.hass.async_add_executor_job.assert_not_called()
            for variant in (cap.DeviceVariant.V1, cap.DeviceVariant.R001):
                flow, entry = secondary_flow.LegacySecondChannelFlowTests().configured(variant)
                before = deepcopy(entry.data)
                result = await flow.async_step_reconfigure({'second_channel_input': value})
                self.assertEqual(result['type'], 'form')
                self.assertTrue(result['errors'])
                self.assertEqual(entry.data, before)

    async def test_legacy_input_key_never_changes_qv_family_capabilities(self):
        for variant in (cap.DeviceVariant.R002, cap.DeviceVariant.CONNECT3):
            data = {'device_variant': variant, 'protocol_family': cap.family_for(variant),
                    'second_channel_enabled': True, 'second_channel_input': 3}
            self.assertFalse(opening.legacy_auxiliary_source(data))


if __name__ == '__main__':
    unittest.main()
