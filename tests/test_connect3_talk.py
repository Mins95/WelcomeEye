"""Synthetic QV talk fixtures and local codecs; no device/network requests."""
import asyncio
from hashlib import sha256
import json
import math
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

import av

from load_integration import load
from test_connect3_media_protocol import KEY, MATERIAL, aes, control_response
from test_connect3_media_session import Writer, setup

t = load('connect3.talk')
p = load('connect3.protocol')
RealAudioEncoder = t.AudioEncoder


def open_response(*, mask=1, variant=0, result=0):
    raw = bytearray(32)
    raw[0], raw[11], raw[14] = 0x0B, result, variant
    struct.pack_into('<HH', raw, 9, 32, result)
    struct.pack_into('<H', raw, 12, mask)
    return aes(bytes(raw)) + aes(sha256(bytes(raw)).digest())


def pcm(samples=160, *, rate=8000, channels='mono'):
    frame = av.AudioFrame(format='s16', layout=channels, samples=samples)
    data = [int(15000 * math.sin(2 * math.pi * 440 * i / rate)) for i in range(samples)]
    if channels == 'stereo':
        data = [item for item in data for _ in range(2)]
    raw = struct.pack('<' + 'h' * len(data), *data)
    frame.planes[0].update(raw)
    frame.sample_rate = rate
    return frame


class FakeEncoder:
    def __init__(self, codec):
        self.codec = codec
    def feed(self, frame):
        return [b'SYNTHETIC_AUDIO' * 40]


class WireTests(unittest.TestCase):
    def test_setup_open_and_request_exact_fields(self):
        self.assertEqual(t.build_setup(), b'\xa9' + bytes(8) + b'\2' + bytes(22))
        wire = t.build_open(MATERIAL, 'SYNTHETIC_PASSWORD', timestamp_seconds=123)
        raw = aes(wire[:32], decrypt=True)
        params = b'adminapp2&&SYNTHETIC_PASSWORD\0\0'
        self.assertEqual(raw[0], 11)
        self.assertEqual(struct.unpack_from('<Q', raw, 1)[0], 123)
        self.assertEqual(struct.unpack_from('<H', raw, 13)[0], 65535)
        body = aes(wire[32:], decrypt=True)
        self.assertEqual(body[:len(params)], params)
        self.assertEqual(body[len(params):len(params)+32], sha256(raw + params).digest())
        for sending, command in ((True, 12), (False, 13)):
            wire = t.build_request(MATERIAL, sending=sending, enabled=True,
                codec_index=4, timestamp_seconds=123)
            raw = aes(wire[:32], decrypt=True)
            self.assertEqual(raw[0], command)
            self.assertEqual(raw[11:14], b'\xff\xff\1')
            self.assertEqual(raw[14:17], b'\4\x40\x1f' if sending else bytes(3))

    def test_negotiation_prefers_native_aac_and_does_not_guess_unknown_codec(self):
        self.assertEqual(t.select_codec(0x31), (4, 8))
        self.assertEqual(t.select_codec(0x21), (0, 4))
        self.assertEqual(t.select_codec(2), (1, 5))
        self.assertEqual(t.select_codec(32), (5, 9))
        for mask in (0, 4, 8, 64):
            with self.assertRaises(t.TalkError):
                t.select_codec(mask)

    def test_audio_two_native_variants_encrypt_only_first32_body_bytes(self):
        payload = bytes(range(80))
        for variant in (0, 1, 4):
            wire = t.build_audio(MATERIAL, payload, 4, variant, timestamp_seconds=1704067200,
                                 milliseconds=321)
            raw = aes(wire[:32], decrypt=True)
            self.assertEqual(raw[0], 0xA2)
            self.assertEqual(raw[9:11], b'\x20\0')
            self.assertEqual(raw[15:18], bytes(3))
            body = aes(wire[32:64], decrypt=True) + wire[64:]
            self.assertEqual(len(body), struct.unpack_from('<I', raw, 11)[0])
            if variant == 0:
                self.assertEqual(body[:8], b'\0\0\1\xf0\x0e\2\x50\0')
                self.assertEqual(body[8:], payload)
            else:
                frame = p.FrameAssembler().feed(body)[0]
                self.assertEqual((frame.frame_type, frame.codec, frame.channels, frame.sample_rate),
                                 (3, 4, 1, 8000))
                self.assertEqual(frame.payload, payload)
                self.assertEqual(frame.milliseconds, 321)
                self.assertEqual(frame.packed_time, 24 << 26 | 1 << 22 | 1 << 17)
            # CBC tail remains plain, with no extra SHA or invented padding.
            self.assertEqual(wire[64:], body[32:])
        with self.assertRaisesRegex(t.TalkError, 'short_talk_audio_frame'):
            t.build_audio(MATERIAL, b'short', 8, 0, timestamp_seconds=0)

    def test_real_pcm_g711_and_aac_encoders_bounded_browser_input(self):
        for codec in (4, 5, 8, 9):
            encoder = t.AudioEncoder(codec)
            packets = []
            for _ in range(20):
                packets += encoder.feed(pcm())
            self.assertTrue(packets, codec)
            self.assertLess(encoder.fifo.samples, encoder.chunk_samples)
            if codec == 9:
                self.assertEqual(len(packets[0]), 960)
            elif codec in (4, 5):
                self.assertEqual(len(packets[0]), 480)
                decoder = av.CodecContext.create(t.CODECS[codec], 'r')
                decoder.sample_rate, decoder.layout = 8000, 'mono'
                frames = decoder.decode(av.Packet(packets[0]))
                self.assertEqual(frames[0].samples, 480)
            else:
                self.assertFalse(packets[0].startswith(b'\xff\xf1'))  # no ADTS injection
                self.assertEqual(encoder.context.frame_size, 1024)
        encoder = t.AudioEncoder(4)
        for invalid in (pcm(1601), pcm(160, rate=100), pcm(40001, rate=192000)):
            with self.assertRaises(t.TalkError):
                encoder.feed(invalid)


class TalkTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer = asyncio.StreamReader(), Writer()
        self.live_session = object()
        self.live = SimpleNamespace(connected=True, session=self.live_session,
            talk_parameters=lambda: dict(host='192.0.2.1', port=8443, pin='a'*64,
                                         stream_key=KEY, password='SYNTHETIC_PASSWORD'))
        self.hub = SimpleNamespace(stopped=False, live=self.live)
        self.talk = t.Talkback(self.hub)
        self.open_patch = patch.object(t, 'open_media_tls', AsyncMock(return_value=(self.reader, self.writer)))
        self.connect = self.open_patch.start()
        self.encoder_patch = patch.object(t, 'AudioEncoder', FakeEncoder)
        self.encoder_patch.start()

    async def asyncTearDown(self):
        await self.talk.close()
        self.open_patch.stop()
        self.encoder_patch.stop()
        self.assertEqual(self.writer.close_count, 1 if self.connect.await_count else 0)
        self.assertIsNone(self.talk._read_task)
        self.assertIsNone(self.talk._writer)
        self.assertIsNone(self.talk._material)
        self.assertIsNone(self.talk._encoder)
        self.assertIsNone(self.talk._live_session)
        for secret in (KEY, 'SYNTHETIC_PASSWORD', '192.0.2.1', 'a'*64):
            self.assertNotIn(secret, json.dumps(self.talk.diagnostics))

    def wire(self, *, mask=1, variant=0):
        return (setup() + open_response(mask=mask, variant=variant)
            + b''.join(control_response(command=12)) + b''.join(control_response(command=13)))

    def commands(self):
        return [data[0] if index == 0 else aes(data[:32], decrypt=True)[0]
                for index, data in enumerate(self.writer.writes)]

    async def start(self, **kwargs):
        self.reader.feed_data(self.wire(**kwargs))
        await self.talk.start('viewer')

    async def test_fragmented_negotiation_one_audio_socket_no_video_acquisition(self):
        task = asyncio.create_task(self.talk.start('viewer'))
        wire = self.wire(variant=1)
        for index in range(0, len(wire), 3):
            self.reader.feed_data(wire[index:index+3])
            await asyncio.sleep(0)
        await task
        self.connect.assert_awaited_once()
        self.assertTrue(self.talk.active)
        self.assertEqual(self.commands(), [169, 11, 12, 13, 13])
        self.assertEqual(self.talk.diagnostics['bytes_received'], len(wire))
        await self.talk.feed('viewer', pcm())
        self.assertEqual(self.commands()[-1], 162)
        self.assertEqual(self.talk.diagnostics['frames_sent'], 1)
        self.talk.disable('viewer')
        count = len(self.writer.writes)
        await self.talk.feed('viewer', pcm())
        self.assertEqual(len(self.writer.writes), count)
        await self.talk.stop('viewer')
        self.assertEqual(self.commands()[-1], 7)
        self.assertTrue(self.live.connected)
        self.assertIs(self.live.session, self.live_session)
        self.assertTrue(self.talk.diagnostics['transmit_accepted'])
        self.assertFalse(self.talk.diagnostics['physically_verified'])

    async def test_diagnostics_distinguish_mono_audio_from_native_talk_selector(self):
        def check():
            diagnostics = self.talk.diagnostics
            self.assertEqual(diagnostics['channels'], 1)  # Compatibility field: mono.
            self.assertEqual(diagnostics['audio_channel_count'], 1)
            self.assertEqual(diagnostics['talk_selector'], 65535)
            self.assertFalse(diagnostics['physically_verified'])
        check()
        await self.start()
        check()
        raw_open = aes(self.writer.writes[1][:32], decrypt=True)
        self.assertEqual(struct.unpack_from('<H', raw_open, 13)[0],
            self.talk.diagnostics['talk_selector'])
        await self.talk.stop('viewer')
        check()

    async def test_other_viewer_cannot_send_stop_or_take_owner(self):
        await self.start()
        with self.assertRaises(t.TalkError):
            await self.talk.start('other')
        await self.talk.feed('other', pcm())
        await self.talk.stop('other')
        self.assertTrue(self.talk.active)
        self.assertEqual(self.talk.diagnostics['frames_sent'], 0)

    async def test_reject_unsupported_codec_and_failed_ack_single_attempt(self):
        self.reader.feed_data(setup() + open_response(mask=4))
        with self.assertRaisesRegex(t.TalkError, 'unsupported_talk_codec'):
            await self.talk.start('viewer')
        self.assertEqual(self.commands(), [169, 11, 7])
        self.connect.assert_awaited_once()
        self.assertFalse(self.talk.active)

    async def test_receive_ack_alone_is_not_ready(self):
        self.reader.feed_data(setup() + open_response() + b''.join(control_response(command=13)))
        task = asyncio.create_task(self.talk.start('viewer'))
        for _ in range(30):
            await asyncio.sleep(0)
        self.assertFalse(self.talk.active)
        self.reader.feed_data(b''.join(control_response(command=12, result=1)))
        with self.assertRaisesRegex(t.TalkError, 'talk_request_rejected'):
            await task
        self.assertEqual(self.commands(), [169, 11, 12, 13, 7])

    async def test_disable_during_setup_never_reactivates(self):
        task = asyncio.create_task(self.talk.start('viewer'))
        for _ in range(20):
            await asyncio.sleep(0)
        self.talk.disable('viewer')
        self.reader.feed_data(setup())
        with self.assertRaises(t.TalkError):
            await task
        self.assertEqual(self.commands(), [169])
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk.owner)

    async def test_eof_partial_setup_no_retry_or_open(self):
        self.reader.feed_data(b'\xa9\0\0')
        self.reader.feed_eof()
        with self.assertRaises(asyncio.IncompleteReadError):
            await self.talk.start('viewer')
        self.assertEqual(self.talk.diagnostics['bytes_received'], 3)
        self.assertEqual(self.commands(), [169])
        self.connect.assert_awaited_once()

    async def test_setup_timeout_and_cancellation_cleanup(self):
        with patch.object(t, 'START_TIMEOUT', .02):
            with self.assertRaises(TimeoutError):
                await self.talk.start('viewer')
        self.assertIsNone(self.talk.owner)
        self.assertEqual(self.commands(), [169])

    async def test_monitor_eof_isolated_from_live_and_releases_owner(self):
        await self.start()
        self.reader.feed_eof()
        await self.talk._read_task
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk.owner)
        self.assertEqual(self.talk.diagnostics['last_error_type'], 'IncompleteReadError')
        self.assertTrue(self.live.connected)

    async def test_start_cancel_settles_socket_without_open_or_retry(self):
        task = asyncio.create_task(self.talk.start('viewer'))
        for _ in range(20):
            await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.connect.assert_awaited_once()
        self.assertEqual(self.commands(), [169])
        self.assertIsNone(self.talk.owner)
        self.assertTrue(self.live.connected)

    async def test_close_error_still_clears_owner_and_secret_state(self):
        await self.start()
        self.writer.wait_closed.side_effect = RuntimeError('PRIVATE_CLOSE_DATA')
        await self.talk.stop('viewer')
        self.assertIsNone(self.talk.owner)
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk._writer)
        self.assertEqual(self.talk.diagnostics['cleanup_error_type'], 'RuntimeError')
        self.assertNotIn('PRIVATE_CLOSE_DATA', json.dumps(self.talk.diagnostics))
        self.assertTrue(self.live.connected)

    async def test_feed_failure_is_contained_and_mic_can_be_enabled_again(self):
        await self.start()
        self.talk._encoder.feed = lambda frame: (_ for _ in ()).throw(ValueError('PRIVATE_BYTES'))
        await self.talk.feed('viewer', pcm())
        self.assertFalse(self.talk.active)
        self.assertIsNone(self.talk.owner)
        self.assertNotIn('PRIVATE_BYTES', json.dumps(self.talk.diagnostics))
        self.reader, self.writer = asyncio.StreamReader(), Writer()
        self.connect.return_value = self.reader, self.writer
        await self.start()
        await self.talk.feed('viewer', pcm())
        self.assertTrue(self.talk.active)
        self.assertEqual(self.talk.diagnostics['frames_sent'], 1)

    async def test_short_aac_silence_is_counted_without_padding_or_stopping_microphone(self):
        await self.start(mask=16, variant=1)
        encoder = RealAudioEncoder(8)
        observed = []
        def encode(frame):
            packets = encoder.feed(frame)
            observed.extend(packets)
            return packets
        self.talk._encoder = SimpleNamespace(feed=encode)
        silence = av.AudioFrame(format='s16', layout='mono', samples=160)
        silence.sample_rate = 8000
        silence.planes[0].update(bytes(320))
        for _ in range(45):
            self.talk.heartbeat('viewer')
            await self.talk.feed('viewer', silence)
        short = [packet for packet in observed if len(packet) + p.FRAME_HEADER_SIZE < 32]
        self.assertTrue(short, 'Real AAC silence must exercise the strict short-prefix boundary')
        self.assertEqual(self.talk.diagnostics['short_audio_packets_dropped'], len(short))
        self.assertEqual(self.talk.diagnostics['last_audio_drop_reason'], 'short_aac_encrypted_prefix')
        self.assertTrue(self.talk.active)
        self.assertIsNone(self.talk.diagnostics['last_error_type'])
        self.assertGreater(encoder.samples, 0)  # Dropping never rolls back the source clock.
        self.assertNotIn(7, self.commands())
        for packet in short:
            with self.assertRaisesRegex(t.TalkError, 'short_talk_audio_frame'):
                t.build_audio(MATERIAL, packet, 8, 1, timestamp_seconds=123)
        before = self.talk.diagnostics['frames_sent']
        samples_before = encoder.samples
        for index in range(40):
            frame = av.AudioFrame(format='s16', layout='mono', samples=160)
            frame.sample_rate = 8000
            values = [int(6000 * math.sin(2 * math.pi * 330 * (index * 160 + i) / 8000)
                + 4000 * math.sin(2 * math.pi * 710 * (index * 160 + i) / 8000)) for i in range(160)]
            frame.planes[0].update(struct.pack('<160h', *values))
            self.talk.heartbeat('viewer')
            await self.talk.feed('viewer', frame)
        self.assertGreater(encoder.samples, samples_before)
        self.assertGreater(self.talk.diagnostics['frames_sent'], before)
        self.assertTrue(self.talk.active)
        self.assertNotIn(7, self.commands())

    async def test_short_non_aac_audio_still_fails_strictly(self):
        await self.start(variant=1)
        self.talk._encoder.feed = lambda frame: [b'short']
        await self.talk.feed('viewer', pcm())
        self.assertFalse(self.talk.active)
        self.assertEqual(self.talk.diagnostics['short_audio_packets_dropped'], 0)
        self.assertEqual(self.talk.diagnostics['last_error_reason'], 'short_talk_audio_frame')

    async def test_media_identity_and_heartbeat_gate_close_without_video_commands(self):
        await self.start()
        self.live.session = object()
        await self.talk.feed('viewer', pcm())
        self.assertFalse(self.talk.active)
        self.assertEqual(self.commands(), [169, 11, 12, 13, 13, 7])

    async def test_stop_waits_for_encoder_and_sends_no_buffered_audio(self):
        await self.start()
        entered, release = threading.Event(), threading.Event()
        def encoding(frame):
            entered.set()
            release.wait(2)
            return [b'NEVER_SEND_THIS_AUDIO' * 40]
        self.talk._encoder.feed = encoding
        feed = asyncio.create_task(self.talk.feed('viewer', pcm()))
        await asyncio.to_thread(entered.wait, 1)
        stopping = asyncio.create_task(self.talk.stop('viewer'))
        for _ in range(10):
            await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        self.assertFalse(self.talk.active)
        release.set()
        await asyncio.gather(feed, stopping)
        self.assertNotIn(162, self.commands())

    async def test_repeated_stop_cancel_keeps_cleanup_owned(self):
        await self.start()
        entered, release = asyncio.Event(), asyncio.Event()
        async def draining():
            if aes(self.writer.writes[-1][:32], decrypt=True)[0] == 7:
                entered.set()
                await release.wait()
        self.writer.drain.side_effect = draining
        stopping = asyncio.create_task(self.talk.stop('viewer'))
        await entered.wait()
        stopping.cancel()
        await asyncio.sleep(0)
        stopping.cancel()
        release.set()
        with self.assertRaises(asyncio.CancelledError):
            await stopping
        self.assertIsNone(self.talk.owner)
        self.assertIsNone(self.talk._read_task)
        self.assertEqual(self.commands().count(7), 1)


if __name__ == '__main__':
    unittest.main()
