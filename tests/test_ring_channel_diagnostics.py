"""Only accepted legacy/LT rings add channel metadata; no device or cloud I/O."""
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, call

from load_integration import load
from test_doorbell_trial import V1, V2, alarm, make_hub
from test_fresh_snapshot import Hub, capture_module

cloud_protocol = load('v1_cloud_protocol')


class RingChannelTests(unittest.TestCase):
    def test_local_channels_remain_distinct_after_decoder_and_dedup(self):
        hub = make_hub(V2)
        for channel in (16, 17, 16):
            hub.ring_listener._record_alarm_parts('fixture-device', 510,
                alarm(alarm_type=14, channel=channel))
        self.assertEqual(hub.hass.bus.async_fire.call_args_list, [
            call('welcomeeye_local.ring', {'entry_id': 'fixture-entry', 'channel': 16, 'ring_sequence': 1}),
            call('welcomeeye_local.ring', {'entry_id': 'fixture-entry', 'channel': 17, 'ring_sequence': 2})])
        self.assertEqual(hub.ring_image.request.call_count, 2)
        report = hub.ring_channel_diagnostics()
        self.assertEqual(report['raw_channel_counts'],
                         {'legacy_local': {'16': 1, '17': 1}, 'cloud_lt': {}})
        self.assertEqual((report['last_source'], report['last_raw_channel'], report['last_event_channel']),
                         ('legacy_local', 17, 17))
        self.assertFalse(report['physical_mapping_verified'])
        # Unknown alarm types, keepalives and repeated packets add no observation.
        hub.ring_listener._record_alarm_parts('fixture-device', 510,
            alarm(alarm_type=123, channel=2))
        hub.ring_listener._record_alarm_parts('fixture-device', 57, b'\0' * 4)
        self.assertEqual(hub.ring_channel_diagnostics(), report)
        self.assertEqual(hub.hass.bus.async_fire.call_count, 2)

    def test_cloud_raw_channel_plus_one_is_not_a_local_channel_conversion(self):
        hub = make_hub(V1)
        hub.entry.data['v1_cloud_doorbell_enabled'] = True
        hub._v1_cloud_uid = hub.entry.unique_id
        for raw in (0, 1):
            event = cloud_protocol.parse_ring_notification({'message_content':
                f'0|fixture-device|{raw}|14|20261009120000|[PRIVATE_NAME]'}, 'fixture-device')
            hub._cloud_ring(event.channel)
        self.assertEqual(hub.hass.bus.async_fire.call_args_list, [
            call('welcomeeye_local.ring', {'entry_id': 'fixture-entry', 'channel': 1,
                                         'ring_sequence': 1, 'source': 'cloud'}),
            call('welcomeeye_local.ring', {'entry_id': 'fixture-entry', 'channel': 2,
                                         'ring_sequence': 2, 'source': 'cloud'})])
        self.assertEqual(hub.ring_image.request.call_args_list, [call(1, None), call(2, None)])
        report = hub.ring_channel_diagnostics()
        self.assertEqual(report['raw_channel_counts'],
                         {'legacy_local': {}, 'cloud_lt': {'0': 1, '1': 1}})
        self.assertEqual((report['last_source'], report['last_raw_channel'], report['last_event_channel']),
                         ('cloud_lt', 1, 2))
        self.assertFalse(report['physical_mapping_verified'])
        for private in ('fixture-device', 'PRIVATE_NAME', '20261009120000'):
            self.assertNotIn(private, json.dumps(report))

    def test_existing_guards_leave_observations_empty(self):
        local = make_hub(V2)
        local.stopped = True
        local._ring(SimpleNamespace(channel=16))
        local._cloud_ring(1)  # R001 does not gain a cloud path.
        cloud = make_hub(V1)
        cloud._v1_cloud_uid = cloud.entry.unique_id
        cloud._cloud_ring(1)  # Opt-in is off.
        cloud._ring(SimpleNamespace(channel=16))  # V1 has no local listener.
        cloud.entry.data['v1_cloud_doorbell_enabled'] = True
        for channel in (0, 257, None, True, '2'):
            cloud._cloud_ring(channel)
        cloud._v1_cloud_uid = 'OTHER_PRIVATE_UID'
        cloud._cloud_ring(1)
        for hub in (local, cloud):
            self.assertEqual(hub.ring_channel_diagnostics()['raw_channel_counts'],
                             {'legacy_local': {}, 'cloud_lt': {}})
            self.assertIsNone(hub.ring_channel_diagnostics()['last_source'])
            hub.hass.bus.async_fire.assert_not_called()
            hub.ring_image.request.assert_not_called()

    def test_metadata_is_bounded_copied_and_never_emits_a_ring(self):
        hub = make_hub(V2)
        for raw in range(256):
            hub._record_ring_channel('legacy_local', raw, raw)
            hub._record_ring_channel('cloud_lt', raw, raw + 1)
        original = hub.ring_channel_diagnostics()
        for source, raw, event in [('unknown', 1, 1), ('legacy_local', -1, -1),
                ('legacy_local', 256, 256), ('cloud_lt', 1, 1), ('legacy_local', True, 1)]:
            hub._record_ring_channel(source, raw, event)
        self.assertEqual(hub.ring_channel_diagnostics(), original)
        self.assertEqual([len(counts) for counts in original['raw_channel_counts'].values()], [256, 256])
        hub._ring_channels['legacy_local'][1] = original['counter_limit']
        hub._record_ring_channel('legacy_local', 1, 1)
        report = hub.ring_channel_diagnostics()
        self.assertEqual(report['raw_channel_counts']['legacy_local']['1'], original['counter_limit'])
        report['raw_channel_counts']['legacy_local'].clear()
        self.assertEqual(len(hub.ring_channel_diagnostics()['raw_channel_counts']['legacy_local']), 256)
        hub.hass.bus.async_fire.assert_not_called()
        hub.ring_image.request.assert_not_called()
        self.assertEqual(hub.ring_count, 0)


class RingChannelExportTests(unittest.IsolatedAsyncioTestCase):
    async def test_downloaded_diagnostics_include_only_copied_protocol_metadata(self):
        diagnostics = capture_module('diagnostics.py', {'VERSION': 'test',
            'get_crc32c_diagnostics': lambda: {}, 'discovery_diagnostics': lambda: {}})
        hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()),
                               async_add_executor_job=AsyncMock(return_value={}))
        entry = SimpleNamespace(data={'detected_model': V2}, options={}, entry_id='test-entry',
                                domain='welcomeeye_local', unique_id='PRIVATE_UID')
        hub = Hub(hass, entry)
        entry.runtime_data = hub
        hub.stopped = False
        hub.control = None
        hub.ring_listener = None
        hub.talkback.diagnostics = {}
        hub.ring_image.request = Mock()
        try:
            # Media state and images alone never create an observed ring channel.
            hub._state(True)
            hub._image(b'PRIVATE_IMAGE_BYTES')
            self.assertIsNone(hub.ring_channel_diagnostics()['last_source'])
            hub._ring(SimpleNamespace(channel=17, timestamp='PRIVATE_TIMESTAMP'))
            exported = await diagnostics['async_get_config_entry_diagnostics'](hass, entry)
            report = exported['doorbell']['observed_protocol_channels']
            self.assertEqual(report, hub.ring_channel_diagnostics())
            self.assertEqual(report['raw_channel_counts']['legacy_local'], {'17': 1})
            for secret in ('PRIVATE_UID', 'PRIVATE_TIMESTAMP', 'PRIVATE_IMAGE_BYTES'):
                self.assertNotIn(secret, json.dumps(exported))
            report['raw_channel_counts']['legacy_local']['17'] = 99
            self.assertEqual(hub.ring_channel_diagnostics()['raw_channel_counts']['legacy_local']['17'], 1)
            self.assertEqual(hass.bus.async_fire.call_count, 1)
        finally:
            if hub.ring_timer:
                hub.ring_timer.cancel()


if __name__ == '__main__':
    unittest.main()
