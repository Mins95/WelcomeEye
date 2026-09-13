"""Offline V1 transport, routing and REAL PyAV decode; no device or credentials."""
from fractions import Fraction
import importlib
import json
import struct
import sys
import threading
import types
import unittest
from unittest.mock import patch

from test_v1_video_diagnostics import PACKAGE, MemorySocket, client, diag, media, protected

v1 = importlib.import_module(PACKAGE + '.v1_video')


class Clock:
    def __init__(self):
        self.now = 100.0

    def __call__(self):
        return self.now


class TimedSocket(MemorySocket):
    def __init__(self, chunks, clock):
        super().__init__(chunks)
        self.clock = clock
        self.timeout = 2.0
        self.calls = 0

    def gettimeout(self):
        return self.timeout

    def settimeout(self, value):
        self.timeout = value

    def recv(self, size):
        self.calls += 1
        if self.chunks and isinstance(self.chunks[0], tuple):
            delay, item = self.chunks.pop(0)
            self.clock.now += delay
            self.chunks.insert(0, item)
        if not self.chunks:
            self.clock.now += self.timeout
            raise TimeoutError()
        return super().recv(size)

    def shutdown(self, _how):
        pass

    def close(self):
        pass


def make_session(chunks, clock, enabled=True):
    s = client.Session('unused', 'unused', 'unused')
    s.sock = TimedSocket(chunks, clock)
    s.last_keepalive = clock()
    s.media_observer = diag.V1VideoDiagnostics()
    if enabled:
        s.enable_v1_video_receive()
    return s


def metadata(sequence, size):
    return struct.pack('<BBHIII', 0, 4, 0, sequence, 123456789, size)


def video_wire(kind, body, sequence=1, declared=None):
    header = struct.pack('<HH', kind, len(body) if declared is None else declared)
    return protected.owsp(protected.tlv(99, metadata(sequence, len(body))) + header + body)


I_FRAME = b'\x00\x00\x00\x01\x65synthetic-structure-only'
P_FRAME = b'\x00\x00\x01\x41synthetic-structure-only'


class TransportTests(unittest.TestCase):
    def setUp(self):
        self.clock = Clock()
        self.patcher = patch.object(client.time, 'monotonic', self.clock)
        self.patcher.start()
        self.addCleanup(self.patcher.stop)

    def test_large_payload_survives_two_socket_timeouts_without_losing_bytes(self):
        body = I_FRAME + b'x' * 50000
        wire = video_wire(100, body)
        s = make_session([wire[:80], (2, TimeoutError()), (2, TimeoutError()), wire[80:]], self.clock)
        parts = s.read()
        self.assertEqual(parts[-1], (100, body))
        self.assertEqual(s.sock.timeout, 2)
        result = s.media_observer.snapshot()
        self.assertEqual(result['socket_receive_timeouts'], 2)
        self.assertEqual(result['complete_owsp_frames'], 1)
        self.assertEqual(result['bytes_progress_before_timeout'], 76)
        self.assertEqual(result['tlv_parse_failures'], 0)

    def test_partial_length_word_survives_timeout(self):
        wire = video_wire(100, I_FRAME)
        s = make_session([wire[:2], (2, TimeoutError()), wire[2:]], self.clock)
        self.assertEqual(s.read()[-1], (100, I_FRAME))

    def test_clean_idle_header_can_be_retried(self):
        wire = video_wire(100, I_FRAME)
        s = make_session([(2, TimeoutError()), wire], self.clock)
        with self.assertRaises(TimeoutError):
            s.read()
        self.assertEqual(s.read()[-1], (100, I_FRAME))

    def test_keepalive_continues_during_slow_payload(self):
        wire = video_wire(100, I_FRAME + b'x' * 1000)
        chunks = [wire[:4]]
        for offset in range(4, len(wire), 200):
            chunks.extend([(2, TimeoutError()), (0.1, wire[offset:offset + 200])])
        s = make_session(chunks, self.clock)
        self.assertEqual(s.read()[-1][1], I_FRAME + b'x' * 1000)
        self.assertGreaterEqual(s.keepalive_count, 1)
        for packet in s.sock.sent:
            self.assertEqual(protected.parse_tlvs(packet[8:]), [(49, bytes((16, 0, 0, 0)))])

    def test_incomplete_payload_expires_without_any_tlv_parse_and_poisoned_reader(self):
        s = make_session([struct.pack('>I', 6396), bytes(128)], self.clock)
        with patch.object(v1, 'parse_video_tlvs', side_effect=AssertionError('partial parse')):
            with self.assertRaises(protected.ProtocolError):
                s.read()
            calls = s.sock.calls
            with self.assertRaises(protected.ProtocolError):
                s.read()
        self.assertEqual(calls, s.sock.calls)
        self.assertEqual(s.media_observer.snapshot()['complete_owsp_frames'], 0)
        self.assertLessEqual(self.clock.now, 106)

    def test_payload_with_zero_bytes_received_is_not_idle_header(self):
        s = make_session([struct.pack('>I', 100)], self.clock)
        with self.assertRaises(protected.ProtocolError):
            s.read()
        self.assertTrue(s._v1_read_failed)

    def test_total_deadline_is_not_extended_by_continuous_progress(self):
        chunks = [struct.pack('>I', 1000)] + [(1, b'x') for _ in range(30)]
        s = make_session(chunks, self.clock)
        with self.assertRaises(protected.ProtocolError):
            s.read()
        self.assertEqual(self.clock.now, 120)
        self.assertEqual(s.media_observer.snapshot()['complete_owsp_frames'], 0)

    def test_padding_is_bounded_by_same_total_deadline(self):
        s = make_session([(1, bytes(4)) for _ in range(25)], self.clock)
        with self.assertRaises(protected.ProtocolError):
            s.read()
        self.assertEqual(self.clock.now, 120)

    def test_eof_during_payload_never_reaches_parser(self):
        s = make_session([struct.pack('>I', 100), b'partial', b''], self.clock)
        with self.assertRaises(ConnectionError):
            s.read()
        self.assertEqual(s.media_observer.snapshot()['complete_owsp_frames'], 0)

    def test_size_outside_bounds_is_rejected_before_payload_read(self):
        for size in (3, 1048577, 0xffffffff):
            with self.subTest(size=size):
                s = make_session([struct.pack('>I', size)], self.clock)
                with self.assertRaises(protected.ProtocolError):
                    s.read()
                self.assertEqual(s.sock.calls, 1)

    def test_two_coalesced_owsp_packets_keep_boundaries(self):
        s = make_session([video_wire(100, I_FRAME) + video_wire(101, P_FRAME, 2)], self.clock)
        self.assertEqual(s.read()[-1], (100, I_FRAME))
        self.assertEqual(s.read()[-1], (101, P_FRAME))

    def test_connect2_reader_remains_opt_out_and_timeout_unchanged(self):
        s = make_session([video_wire(100, I_FRAME)[:12], TimeoutError()], self.clock, False)
        with self.assertRaises(TimeoutError):
            s.read()
        self.assertFalse(s.v1_video_receive)
        self.assertEqual(s.media_observer.snapshot()['socket_receive_timeouts'], 0)

    def test_cancel_stops_receive_without_parsing(self):
        s = make_session([video_wire(100, I_FRAME)], self.clock)
        s.closed.set()
        with self.assertRaises(ConnectionAbortedError):
            s.read()
        self.assertEqual(s.sock.calls, 0)


class VideoRoutingTests(unittest.TestCase):
    def setUp(self):
        self.observer = diag.V1VideoDiagnostics()
        self.receiver = v1.V1VideoReceiver(self.observer)

    def submit(self, kind, body, sequence=1, size=None):
        self.receiver.receive(99, metadata(sequence, len(body) if size is None else size))
        return self.receiver.receive(kind, body)

    def test_metadata_then_i_and_p_are_unchanged(self):
        self.assertEqual(self.submit(100, I_FRAME), (I_FRAME, True))
        self.assertEqual(self.submit(101, P_FRAME, 2), (P_FRAME, False))

    def test_audio_metadata_and_unknown_tlvs_cannot_be_promoted(self):
        for kind in (97, 57, 104, 105, 510, 777):
            self.assertIsNone(self.receiver.receive(kind, I_FRAME))

    def test_missing_or_malformed_metadata_rejected(self):
        self.assertIsNone(self.receiver.receive(100, I_FRAME))
        for body in (b'', bytes(15), bytes(17), metadata(1, 0), metadata(1, 1048577)):
            self.receiver.receive(99, body)
            self.assertIsNone(self.receiver.receive(100, I_FRAME))

    def test_metadata_cannot_be_reused_for_second_image(self):
        self.assertIsNotNone(self.submit(100, I_FRAME))
        self.assertIsNone(self.receiver.receive(101, P_FRAME))

    def test_partial_and_oversized_image_are_rejected(self):
        for size in (len(I_FRAME) - 1, len(I_FRAME) + 1):
            self.assertIsNone(self.submit(100, I_FRAME, size=size))

    def test_expired_metadata_rejected(self):
        with patch.object(v1.time, 'monotonic', return_value=100):
            self.receiver.receive(99, metadata(1, len(I_FRAME)))
        with patch.object(v1.time, 'monotonic', return_value=120):
            self.assertIsNone(self.receiver.receive(100, I_FRAME))

    def test_p_requires_i_and_contiguous_counter(self):
        self.assertIsNone(self.submit(101, P_FRAME, 1))
        self.assertIsNotNone(self.submit(100, I_FRAME, 2))
        self.assertIsNone(self.submit(101, P_FRAME, 4))
        self.assertIsNone(self.submit(101, P_FRAME, 5))
        self.assertIsNotNone(self.submit(100, I_FRAME, 6))
        self.assertIsNotNone(self.submit(101, P_FRAME, 7))

    def test_counter_wrap_is_accepted(self):
        self.assertIsNotNone(self.submit(100, I_FRAME, 0xffffffff))
        self.assertIsNotNone(self.submit(101, P_FRAME, 0))

    def test_fragments_never_reach_decoder_even_if_they_look_like_idr(self):
        for kinds in ((103, 107, 108), (106, 108), (108, 107, 103), (103, 108)):
            for kind in kinds:
                self.assertIsNone(self.submit(kind, I_FRAME))
        self.assertIsNone(self.submit(101, P_FRAME, 2))
        self.assertNotIn('complete_video_count', self.observer.snapshot()['video_receive'])

    def test_prefix_avcc_invalid_nal_and_metadata_only_nal_rejected(self):
        for body in (b'prefix' + I_FRAME, struct.pack('>I', 4) + b'\x65abc',
                     b'\x00\x00\x01\xe5abc', b'\x00\x00\x01',
                     b'\x00\x00\x01\x67SPS'):
            self.assertIsNone(self.submit(100, body))

    def test_native_tlv_flag_is_authoritative_for_non_idr_i_picture(self):
        self.assertEqual(self.submit(100, P_FRAME), (P_FRAME, True))
        self.assertEqual(self.submit(101, P_FRAME, 2), (P_FRAME, False))

    def test_native_zero_previous_counter_does_not_assert_next_value(self):
        self.assertIsNotNone(self.submit(100, I_FRAME, 0))
        self.assertIsNotNone(self.submit(101, P_FRAME, 10))

    def test_native_terminal_owsp_remainder_requires_corroboration(self):
        wire = video_wire(100, I_FRAME, declared=1)
        self.assertEqual(v1.parse_video_tlvs(wire[8:])[-1], (100, I_FRAME))
        with self.assertRaises(protected.ProtocolError):
            v1.parse_video_tlvs(struct.pack('<HH', 100, 1) + I_FRAME)
        # Connect 2 keeps the strict parser even for identical wire data.
        with self.assertRaises(protected.ProtocolError):
            protected.parse_tlvs(wire[8:])

    def test_truncated_tlv_and_excessive_tlv_count_rejected(self):
        for body in (b'x', struct.pack('<HH', 99, 16) + bytes(15),
                     protected.tlv(57, bytes(4)) * 129):
            with self.assertRaises(protected.ProtocolError):
                v1.parse_video_tlvs(body)

    def test_video_above_u16_tlv_size_uses_corroborated_owsp_boundary(self):
        body = I_FRAME + b'x' * 100000
        wire = video_wire(100, body, declared=1)
        parts = v1.parse_video_tlvs(wire[8:])
        self.assertEqual(parts[-1], (100, body))
        for kind, data in parts:
            result = self.receiver.receive(kind, data)
        self.assertEqual(result, (body, True))

    def test_diagnostics_do_not_contain_bytes_counter_or_timestamp(self):
        self.submit(100, I_FRAME, 987654321)
        result = json.dumps(self.observer.snapshot())
        for secret in (I_FRAME.decode('latin1'), I_FRAME.hex(), '987654321', '123456789'):
            self.assertNotIn(secret, result)
        self.assertIsNone(self.receiver.metadata)


def synthetic_h264():
    """Encode generated YUV planes with real libx264, no captured imagery."""
    import av
    if not hasattr(av, 'CodecContext'):
        raise AssertionError('Install real PyAV for decode tests; an av stub is insufficient')
    encoder = av.CodecContext.create('libx264', 'w')
    encoder.width, encoder.height = 352, 288
    encoder.pix_fmt = 'yuv420p'
    encoder.time_base = Fraction(1, 20)
    encoder.framerate = Fraction(20, 1)
    encoder.options = {'preset': 'ultrafast', 'tune': 'zerolatency',
                       'x264-params': 'keyint=100:min-keyint=100:scenecut=0:bframes=0:repeat-headers=1'}
    packets = []
    for index in range(8):
        frame = av.VideoFrame(352, 288, 'yuv420p')
        for plane_index, plane in enumerate(frame.planes):
            plane.update(bytes([40 + index * 3 if plane_index == 0 else 128]) * plane.buffer_size)
        frame.pts = index
        packets.extend(encoder.encode(frame))
    packets.extend(encoder.encode(None))
    return [(100 if packet.is_keyframe else 101, bytes(packet)) for packet in packets]


class RealPipelineTests(unittest.TestCase):
    def test_slow_v1_owsp_metadata_i_p_reach_real_pipeline_and_decode_image(self):
        packets = synthetic_h264()
        self.assertEqual(packets[0][0], 100)
        self.assertTrue(all(kind == 101 for kind, _ in packets[1:]))
        clock = Clock()
        wire = b''.join(video_wire(kind, body, index) for index, (kind, body) in enumerate(packets))
        s = make_session([wire[:50], (2, TimeoutError()), (2, TimeoutError()), wire[50:]], clock)
        receiver = v1.V1VideoReceiver(s.media_observer)
        ts, images, frames, accepted = [], [], [], []
        pipeline = media.MediaPipeline(media.StreamFormat(352, 288, 20, 8000, 0x7a19, 1),
                                       ts.append, images.append, lambda kind, frame: frames.append((kind, frame)))
        try:
            self.assertFalse(pipeline.feed_video(packets[1][1], keyframe=False))
            with patch.object(client.time, 'monotonic', clock):
                for _ in packets:
                    for kind, body in s.read():
                        video = receiver.receive(kind, body)
                        if video is not None:
                            data, keyframe = video
                            accepted.append(pipeline.feed_video(data, keyframe=keyframe))
            self.assertEqual(accepted, [True] * len(packets))
            self.assertTrue(pipeline.started)
            self.assertGreaterEqual(len(frames), len(packets) - 2)
            self.assertTrue(all((frame.width, frame.height) == (352, 288) for _, frame in frames))
            self.assertTrue(images and images[0].startswith(b'\xff\xd8'))
        finally:
            pipeline.close()
        # The muxer can buffer video while waiting for the configured audio.
        self.assertGreater(sum(map(len, ts)), 0)


class WorkerPipelineTests(unittest.IsolatedAsyncioTestCase):
    """Exercise the actual hub worker with only its device/HA boundary faked."""

    async def run_worker(self, model, dimensions):
        helpers = types.ModuleType('homeassistant.helpers')
        helpers.device_registry = types.ModuleType('homeassistant.helpers.device_registry')
        with patch.dict(sys.modules, {'homeassistant': types.ModuleType('homeassistant'),
                                      'homeassistant.helpers': helpers,
                                      'homeassistant.helpers.device_registry': helpers.device_registry}):
            hub_module = importlib.import_module(PACKAGE + '.hub')
        packets = synthetic_h264()
        fmt = bytearray(40)
        fmt[4:8] = b'H264'
        struct.pack_into('<HH', fmt, 12, *dimensions)
        fmt[16] = 20
        struct.pack_into('<I', fmt, 20, 8000)
        struct.pack_into('<HH', fmt, 28, 0x7a19, 1)
        entry = types.SimpleNamespace(data={'host': 'unused', 'username': 'unused',
                                           'password': 'unused', 'detected_model': model})
        hub = hub_module.WelcomeEyeHub(types.SimpleNamespace(), entry)
        stop = threading.Event()
        clock = Clock()
        audio_body = b'\xd5' * 400
        audio_wire = protected.owsp(protected.tlv(97, bytes(8)) + protected.tlv(98, audio_body))
        wire = b''.join(video_wire(kind, body, n) + audio_wire
                        for n, (kind, body) in enumerate(packets))
        s = make_session([wire[:50], (2, TimeoutError()), wire[50:]], clock, False)
        s.media_observer = None
        s.connect = lambda: [(203, bytes(fmt))]
        commands = []
        s.send_start_av = lambda: commands.append('start_av')
        s.send_stop_av = lambda: commands.append('stop_av')
        s.send_manufacturer = lambda payload: commands.append(('query', payload))
        # Connect 2 must keep its old timeout behavior; no pause in that fixture.
        if model == 'WelcomeEye Connect 2':
            s.sock.chunks = [wire]
        recv = s.sock.recv

        def recv_until_done(size):
            if not s.sock.chunks:
                stop.set()
                return b''
            return recv(size)

        s.sock.recv = recv_until_done
        hub.loop = types.SimpleNamespace(call_soon_threadsafe=lambda callback, *args: callback(*args))
        hub._dispatch = lambda generation, callback, *args: callback(*args)
        frames, images, calls, audio_calls = [], [], [], []
        hub._frame = lambda kind, frame: frames.append((kind, frame))
        hub._image = images.append
        hub._ts = lambda data: None
        hub._state = lambda *args: None
        hub._observe_device_model = lambda *args: None
        original = media.MediaPipeline.feed_video
        original_audio = media.MediaPipeline.feed_audio

        def record(pipeline, body, *, keyframe=False):
            calls.append((bytes(body), keyframe))
            return original(pipeline, body, keyframe=keyframe)

        def record_audio(pipeline, body):
            audio_calls.append(bytes(body))
            return original_audio(pipeline, body)

        with patch.object(hub_module, 'Session', return_value=s), \
                patch.object(media.MediaPipeline, 'feed_video', record), \
                patch.object(media.MediaPipeline, 'feed_audio', record_audio), \
                patch.object(client.time, 'monotonic', clock):
            hub._worker(0, stop)
        self.assertEqual(calls, [(body, kind == 100) for kind, body in packets])
        self.assertEqual(audio_calls, [audio_body] * len(packets))
        self.assertTrue(images)
        self.assertGreaterEqual(sum(kind == 'video' for kind, _ in frames), len(packets) - 2)
        self.assertEqual(sum(kind == 'audio' for kind, _ in frames), len(packets))
        self.assertTrue(s.closed.is_set())
        self.assertEqual(hub.video_packets_received, len(packets))
        return hub, s, commands

    async def test_known_v1_worker_enables_buffering_and_really_decodes(self):
        hub, s, commands = await self.run_worker('WelcomeEye Connect V1', (352, 288))
        self.assertTrue(s.v1_video_receive)
        self.assertEqual(commands[0], 'start_av')
        self.assertEqual(commands[-1], 'stop_av')
        counts = hub.v1_video_diagnostics.snapshot()['video_receive']
        self.assertEqual(counts['tlv100_count'], 1)
        self.assertEqual(counts['tlv101_count'], 7)

    async def test_v1_discovered_from_format_enables_same_path(self):
        hub, s, commands = await self.run_worker('WelcomeEye', (352, 288))
        self.assertTrue(s.v1_video_receive)
        self.assertEqual(hub.selected_video_tlv, 100)

    async def test_connect2_worker_preserves_video_bytes_flags_and_old_receiver(self):
        hub, s, commands = await self.run_worker('WelcomeEye Connect 2', (720, 576))
        self.assertFalse(s.v1_video_receive)
        self.assertIsNone(hub.v1_video_diagnostics)
        self.assertEqual(commands, [])


if __name__ == '__main__':
    unittest.main()
