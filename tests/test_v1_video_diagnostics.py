"""No device access: structural diagnostics must preserve read/parse behavior."""
import importlib
import importlib.util
import json
from pathlib import Path
import struct
import sys
import time
import types
import unittest
from unittest.mock import patch

PACKAGE = 'welcomeeye_v1_test'
package = types.ModuleType(PACKAGE)
package.__path__ = [str(Path(__file__).resolve().parents[1] / 'custom_components' / 'welcomeeye_local')]
sys.modules[PACKAGE] = package
# Structural H264 helpers do not use PyAV. No mock is used for decoding tests.
if importlib.util.find_spec('av') is None:
    sys.modules.setdefault('av', types.ModuleType('av'))
client = importlib.import_module(PACKAGE + '.client')
protected = importlib.import_module(PACKAGE + '.protected')
diag = importlib.import_module(PACKAGE + '.v1_video_diagnostics')
media = importlib.import_module(PACKAGE + '.media')


class MemorySocket:
    def __init__(self, chunks):
        self.chunks = list(chunks)
        self.sent = []

    def recv(self, size):
        item = self.chunks.pop(0)
        if isinstance(item, BaseException):
            raise item
        if len(item) > size:
            self.chunks.insert(0, item[size:])
        return item[:size]

    def sendall(self, packet):
        self.sent.append(packet)


def session(chunks, observed=True):
    result = client.Session('unused', 'unused', 'unused')
    result.sock = MemorySocket(chunks)
    result.last_keepalive = time.monotonic()
    if observed:
        result.media_observer = diag.V1VideoDiagnostics()
    return result


class ReadTests(unittest.TestCase):
    def test_zero_length_padding_remains_skipped_before_idle_timeout(self):
        s = session([bytes(8), TimeoutError()], observed=False)
        with self.assertRaises(TimeoutError):
            s.read()
        self.assertEqual(s.zero_frame_count, 2)
        self.assertEqual(s.read_count, 0)

    def test_native_audio_metadata_is_not_video(self):
        payload = protected.tlv(97, bytes(8)) + protected.tlv(98, b'\xd5' * 320)
        s = session([protected.owsp(payload)])
        self.assertEqual(s.read(), protected.parse_tlvs(payload))
        result = s.media_observer.snapshot()
        self.assertEqual(result['complete_owsp_frames'], 1)
        self.assertEqual(result['successfully_parsed_frames'], 1)
        row = result['recent_frame_structures'][0]['tlvs'][0]
        self.assertTrue(row['matches_native_audio_metadata_size'])
        self.assertIsNone(row['declared_body']['framing_candidate'])
        self.assertEqual(s.sock.sent, [])

    def test_length_word_does_not_prove_complete_frame(self):
        s = session([struct.pack('>I', 6396), b'\x00' * 128, TimeoutError('not logged')])
        with self.assertRaises(TimeoutError):
            s.read()
        self.assertEqual(s.last_frame_size, 6396)
        self.assertEqual(s.read_count, 1)
        result = s.media_observer.snapshot()
        self.assertEqual(result['complete_owsp_frames'], 0)
        self.assertIsNone(result['last_complete_size'])
        self.assertEqual(result['recent_read_errors'], [{
            'phase': 'owsp_payload', 'requested_bytes': 6396,
            'received_bytes': 128, 'error_type': 'TimeoutError'}])

    def test_partial_length_word_and_eof_are_distinguished(self):
        s = session([b'\x00\x00', b''])
        with self.assertRaises(ConnectionError):
            s.read()
        self.assertEqual(s.media_observer.snapshot()['recent_read_errors'][0], {
            'phase': 'length_word', 'requested_bytes': 4,
            'received_bytes': 2, 'error_type': 'ConnectionError'})

    def test_malformed_tlv_is_observed_but_still_rejected(self):
        payload = protected.tlv(97, bytes(8)) + struct.pack('<HH', 100, 500) + b'\x00\x00\x00\x01\x65abc'
        for observed in (True, False):
            s = session([protected.owsp(payload)], observed)
            with self.assertRaises(protected.ProtocolError):
                s.read()
            if observed:
                result = s.media_observer.snapshot()
                self.assertEqual(result['complete_owsp_frames'], 1)
                self.assertEqual(result['tlv_parse_failures'], 1)
                self.assertEqual(result['recent_frame_structures'][0]['strict_tlv_status'], 'truncated_payload')

    def test_terminal_length_mismatch_does_not_change_parser(self):
        payload = struct.pack('<HH', 100, 1) + b'\x65' + b'\xff\xff\xff'
        s = session([protected.owsp(payload)])
        with self.assertRaises(protected.ProtocolError):
            s.read()
        result = s.media_observer.snapshot()
        self.assertEqual(result['sampled_native_terminal_length_mismatches'], 1)

    def test_connect2_payloads_and_metadata_are_byte_identical(self):
        parts = [(99, bytes(16)), (100, b'\x00\x00\x00\x01\x65IDR'),
                 (101, b'\x00\x00\x01\x41P'), (98, b'\xd5' * 320)]
        for kind, body in parts:
            wire = protected.owsp(protected.tlv(kind, body))
            for observed in (False, True):
                s = session([wire[:2], wire[2:8], wire[8:]], observed)
                self.assertEqual(s.read(), [(kind, body)])
                self.assertEqual(s.sock.sent, [])

    def test_disabled_observer_performs_no_structural_scans(self):
        with patch.object(diag, '_structure', side_effect=AssertionError('must stay disabled')):
            s = session([protected.owsp(protected.tlv(100, b'video'))], False)
            self.assertEqual(s.read(), [(100, b'video')])


class StructureTests(unittest.TestCase):
    def test_annex_b_search_already_finds_prefixed_nals(self):
        body = b'prefix!!\x00\x00\x00\x01\x67SPS\x00\x00\x01\x68PPS\x00\x00\x01\x65IDR'
        self.assertTrue(media.inspect_h264_packet(body).detected)
        shape = diag._shape(body)
        self.assertEqual(shape['start_code_offsets'][0], 8)
        self.assertEqual(shape['nal_types_candidate'], [5, 7, 8])

    def test_avcc_requires_full_body_from_offset_zero(self):
        body = struct.pack('>I', 4) + b'\x65abc'
        self.assertEqual(diag._shape(body)['framing_candidate'], 'avcc')
        self.assertIsNone(diag._shape(b'prefix!!' + body)['framing_candidate'])

    def test_native_fragment_types_are_counted_without_reassembly(self):
        observer = diag.V1VideoDiagnostics()
        for kind in (102, 103, 106, 107, 108, 109):
            payload = protected.tlv(kind, bytes(16) if kind in (102, 109) else b'fragment')
            observer.complete(bytes(4) + payload)
        self.assertEqual(observer.snapshot()['sampled_media_tlv_counts'],
                         {str(k): 1 for k in (102, 103, 106, 107, 108, 109)})

    def test_no_payload_identity_or_absolute_clock_is_retained(self):
        secret = b'PRIVATE_VIDEO_SENTINEL'
        clock = 1987654321
        frame = struct.pack('<I', clock) + protected.tlv(97, struct.pack('<BBHI', 0, 4, 123, clock))
        frame += protected.tlv(98, secret * 3)
        observer = diag.V1VideoDiagnostics()
        observer.complete(frame)
        encoded = json.dumps(observer.snapshot())
        self.assertNotIn(secret.decode(), encoded)
        self.assertNotIn(secret.hex(), encoded)
        self.assertNotIn(str(clock), encoded)
        self.assertNotIn('123', encoded)
        self.assertNotIn(frame, vars(observer).values())

    def test_observation_memory_and_scan_are_bounded(self):
        observer = diag.V1VideoDiagnostics()
        for _ in range(150):
            observer.complete(bytes(4) + protected.tlv(97, bytes(8)))
        result = observer.snapshot()
        self.assertEqual(result['sampled_frames'], 128)
        self.assertTrue(result['sampling_capped'])
        self.assertEqual(len(result['recent_frame_structures']), 8)
        shape = diag._shape(b'\x00\x00\x01\x65x' * 20000)
        self.assertTrue(shape['scan_truncated'])
        self.assertEqual(len(shape['start_code_offsets']), 8)
        self.assertLessEqual(shape['scanned_bytes'], 65536)

    def test_snapshot_is_detached_from_observer(self):
        observer = diag.V1VideoDiagnostics()
        observer.complete(bytes(4) + protected.tlv(97, bytes(8)))
        result = observer.snapshot()
        result['recent_frame_structures'][0]['tlvs'].clear()
        self.assertEqual(len(observer.snapshot()['recent_frame_structures'][0]['tlvs']), 1)


if __name__ == '__main__':
    unittest.main()
