"""SDK B1 header-state fixtures, independent of any real intercom traffic."""
from dataclasses import asdict
import json
import struct
import time
import unittest

from load_integration import load

wire = load('connect3.alarm_protocol')
ring = load('connect3.ring_listener')


def state_packet(masks=(0, 0, 0), *, subtype=0, body=b''):
    header = bytearray(32)
    header[0], header[8] = 0xB1, subtype
    struct.pack_into('<I', header, 4, len(body))
    struct.pack_into('<III', header, 16, *masks)
    return bytes(header) + body


def input_packet(mask):
    header = bytearray(state_packet(subtype=10))
    struct.pack_into('<H', header, 29, mask)
    return bytes(header)


class LegacyStateDecoderTests(unittest.TestCase):
    def test_header_only_b1_preserves_three_sdk_masks_without_panel_inference(self):
        metadata = wire.parse_legacy_alarm_metadata(state_packet((0x80000001, 2, 4)))
        self.assertEqual(asdict(metadata), {
            'header_subtype': 0, 'callback_type': 0, 'kind': 'state_bitmaps',
            'state_masks': (0x80000001, 2, 4)})
        self.assertNotIn('channel', asdict(metadata))

    def test_header_only_priority_matches_sdk_even_for_a_body_subtype(self):
        for subtype in (3, 6, 8, 9, 255):
            self.assertEqual(wire.parse_legacy_alarm_metadata(
                state_packet((1, 2, 3), subtype=subtype)).kind, 'state_bitmaps')

    def test_special_header_bitmap_uses_offset_29_not_body_or_panel_selector(self):
        metadata = wire.parse_legacy_alarm_metadata(input_packet(0x8003))
        self.assertEqual((metadata.kind, metadata.callback_type, metadata.state_masks),
                         ('input_bitmap', 4, (0x8003,)))

    def test_recognized_body_forms_export_only_structural_metadata(self):
        for subtype, size, callback in ((9, 4, 1), (8, 1, 2), (6, 16, 3), (3, 16, 5)):
            secret = b'PRIVATE_BODY_DATA'[:size]
            metadata = wire.parse_legacy_alarm_metadata(
                state_packet(subtype=subtype, body=secret))
            self.assertEqual((metadata.callback_type, metadata.kind, metadata.state_masks),
                             (callback, 'structured_status', ()))
            self.assertNotIn(secret.decode(), repr(metadata))

    def test_rejects_wrong_framing_and_incompatible_body_sizes(self):
        valid = state_packet()
        for packet in (None, bytearray(valid), valid[:31], valid + b'x',
                       b'\x69' + valid[1:], state_packet(subtype=10, body=b'x'),
                       state_packet(subtype=9, body=b'x'),
                       state_packet(subtype=6, body=b'x' * 17),
                       state_packet(subtype=255, body=b'PRIVATE')):
            with self.subTest(packet_type=type(packet)):
                with self.assertRaises(wire.AlarmProtocolError):
                    wire.parse_legacy_alarm_metadata(packet)

    def test_non_sdk_bytes_never_appear_in_metadata(self):
        header = bytearray(state_packet((1, 2, 3)))
        header[9:16] = b'PRIVATE'
        header[28:32] = b'HIDE'
        serialized = json.dumps(asdict(wire.parse_legacy_alarm_metadata(bytes(header))))
        self.assertNotIn('PRIVATE', serialized)
        self.assertNotIn('HIDE', serialized)


class LegacyStateObservationTests(unittest.TestCase):
    def setUp(self):
        self.probe = ring.AutonomousDoorbellDiagnostic()
        self.probe._started = time.monotonic()

    def observe(self, packet):
        self.probe._observe(packet)
        return self.probe.diagnostics()

    def test_initial_state_repetition_and_change_are_never_a_confirmed_bell(self):
        first = self.observe(state_packet((1, 0, 0)))
        self.assertTrue(first['events'][0]['baseline'])
        self.assertFalse(first['events'][0]['state_changed'])
        self.observe(state_packet((1, 0, 0)))
        last = self.observe(state_packet((1, 2, 0)))
        self.assertEqual(last['legacy_state_frames'], 3)
        self.assertEqual(last['legacy_state_changes'], 1)
        self.assertEqual(last['observation_category'], 'legacy_state_change_observed')
        self.assertFalse(last['events'][1]['state_changed'])
        self.assertTrue(last['events'][2]['state_changed'])
        for event in last['events']:
            self.assertIsNone(event['channel'])
            self.assertEqual(event['state_interpretation'], 'unknown_not_a_confirmed_doorbell')
        self.assertEqual(last['notification_candidates'], 0)
        self.assertFalse(last['firmware_subscription_confirmed'])
        self.assertFalse(last['physical_ring_confirmed'])
        self.assertEqual(last['ring_events_emitted'], 0)

    def test_state_forms_use_independent_baselines_and_results_are_detached(self):
        self.observe(state_packet((1, 0, 0)))
        first_input = self.observe(input_packet(2))
        self.assertTrue(first_input['events'][-1]['baseline'])
        first_input['events'][-1]['state_masks'][0] = 65535
        last = self.observe(input_packet(2))
        self.assertEqual(last['legacy_state_changes'], 0)
        self.assertEqual(last['events'][-1]['state_masks'], [2])

    def test_malformed_and_opaque_b1_are_counted_without_exporting_the_body(self):
        last = self.observe(state_packet(subtype=255, body=b'PRIVATE'))
        self.assertEqual(last['legacy_invalid_records'], 1)
        self.assertFalse(last['events'][0]['structure_valid'])
        self.assertEqual(last['legacy_state_frames'], 0)
        self.assertNotIn('PRIVATE', json.dumps(last))
        last = self.observe(state_packet(subtype=3, body=b'PRIVATE_BODYDATA'))
        self.assertTrue(last['events'][-1]['structure_valid'])
        self.assertNotIn('PRIVATE', json.dumps(last))
        self.assertEqual(last['legacy_state_frames'], 0)

    def test_event_limit_does_not_stop_state_accounting(self):
        for index in range(100):
            last = self.observe(state_packet((index % 2, 0, 0)))
        self.assertEqual(len(last['events']), 64)
        self.assertEqual(last['events_omitted'], 36)
        self.assertEqual(last['legacy_state_frames'], 100)
        self.assertEqual(last['legacy_state_changes'], 99)


if __name__ == '__main__':
    unittest.main()
