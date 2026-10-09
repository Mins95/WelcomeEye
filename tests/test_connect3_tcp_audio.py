"""Shared C3 TCP downstream audio using synthetic QV bytes and real PyAV."""
import asyncio
from dataclasses import replace
import json
import struct
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer
from test_connect3_control import unlock_reply
from test_connect3_tcp_security import audio_frame_bytes, setup

hubs = load('connect3.hub')
live = load('connect3.live')
session = load('connect3.session')
cgi = load('connect3.cgi')
AUTH, PIN = 'SYNTHETIC_DOWNSTREAM_AUTH', 'a' * 64


class TCPAudioTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.reader, self.writer = asyncio.StreamReader(), Writer()
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data={
            'host': '192.0.2.33', 'auth_code': AUTH, 'certificate_sha256': PIN,
            'experimental_video': True, 'media_transport': 'connect3_tcp',
            'media_tcp_approved': True,
            'trust_endpoint': {'host': '192.0.2.33', 'cgi_port': 443,
                'media_port': 34567, 'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}))
        self.frames, self.audio_decoders = [], []
        self.video_decoder = SimpleNamespace(errors=0,
            feed=Mock(return_value=([SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG')), close=Mock())
        actual_audio_decoder = live.AudioDecoder
        def audio_decoder():
            decoder = actual_audio_decoder()
            self.audio_decoders.append(decoder)
            return decoder
        async def material(*args, diagnostics=None, **kwargs):
            diagnostics.update(authentication_status='accepted', tls_verified=True, tls_policy='certificate_pin')
            return cgi.StreamMaterial(KEY)
        self.patchers = [
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)),
            patch.object(live, 'VideoDecoder', Mock(return_value=self.video_decoder)),
            patch.object(live, 'AudioDecoder', Mock(side_effect=audio_decoder)),
            patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('No discovery for manual entry'))),
            patch.object(session, 'open_connect3_media_tcp', AsyncMock(return_value=(self.reader, self.writer))),
            patch.object(session, 'open_media_tls', AsyncMock(side_effect=AssertionError('No TLS fallback'))),
            patch.object(session, 'open_r002_media_tcp', AsyncMock(side_effect=AssertionError('No R002 fallback'))),
            patch.object(session, 'KEEPALIVE_INTERVAL', 999),
        ]
        for patcher in self.patchers:
            patcher.start()
        self.hub.frame_listeners.add(lambda kind, frame: self.frames.append((kind, frame)))
        await self.hub.start()

    async def asyncTearDown(self):
        try:
            await self.hub.stop()
            self.assertIsNone(self.hub.live.task)
            self.assertIsNone(self.hub.live.session)
            self.assertFalse(self.hub.consumers)
            self.assertEqual(self.writer.close_count, 1)
            self.assertTrue(all(decoder.diagnostics['closed'] for decoder in self.audio_decoders))
        finally:
            for patcher in reversed(self.patchers):
                patcher.stop()

    def send_frames(self, *frames):
        self.reader.feed_data(setup() + b''.join(control_response()) + b''.join(
            b''.join(media_response(frame)) for frame in frames))

    async def wait_frames(self, count):
        async with asyncio.timeout(5):
            while len(self.frames) < count:
                await asyncio.sleep(0)

    def commands(self):
        return [packet[0] if index == 0 else aes(packet[:32], decrypt=True)[0]
                for index, packet in enumerate(self.writer.writes)]

    def assert_shared_connection(self):
        session.open_connect3_media_tcp.assert_awaited_once()
        session.open_media_tls.assert_not_called()
        session.open_r002_media_tcp.assert_not_called()
        live.discover.assert_not_called()
        live.read_stream_material.assert_awaited_once()
        self.assertEqual(self.hub.live.session_count, 1)
        self.assertEqual(self.commands(), [0xa9, 1])
        self.assertTrue(self.hub.live.observation['play_accepted'])
        self.assertEqual(self.hub.live.observation['credential_protection'], 'qv_aes256_sha256')

    async def test_native_alaw_audio_and_h264_share_consumers_reader_and_cleanup(self):
        self.send_frames(frame_bytes(), audio_frame_bytes(), frame_bytes(), audio_frame_bytes())
        await asyncio.gather(self.hub.acquire('first'), self.hub.acquire('second'))
        await self.wait_frames(4)
        self.assertEqual([kind for kind, _ in self.frames], ['video', 'audio', 'video', 'audio'])
        audio_frames = [frame for kind, frame in self.frames if kind == 'audio']
        for frame in audio_frames:
            self.assertEqual((frame.sample_rate, frame.samples, frame.layout.name, frame.format.name),
                             (8000, 160, 'mono', 's16'))
            self.assertEqual(struct.unpack('<160h', bytes(frame.planes[0])[:320]), (8,) * 160)
        self.assertGreater(audio_frames[1].pts, audio_frames[0].pts)
        self.assertEqual(len(self.audio_decoders), 1)
        self.assert_shared_connection()
        diagnostics = self.hub.diagnostics()
        self.assertEqual(diagnostics['audio']['input_packets'], 2)
        self.assertEqual(diagnostics['audio']['codec'], 'pcm_alaw')
        self.assertEqual(diagnostics['audio']['decoded_frames'], 2)
        self.assertEqual(diagnostics['media']['tcp_nonvideo_frames_ignored'], 0)
        for secret in (KEY, AUTH, PIN, '192.0.2.33', 'SYNTHETIC_JPEG'):
            self.assertNotIn(secret, json.dumps(diagnostics))
        with self.assertRaisesRegex(RuntimeError, 'TCP microphone disabled'):
            self.hub.live.talk_parameters()
        with self.assertRaisesRegex(session.OutputFailure, '^connect3_tcp_outputs_disabled$'):
            await self.hub.live.session.execute_output(1, 'UNSENT_OPENING_CODE')
        self.assertEqual(self.commands(), [0xa9, 1])
        await self.hub.release('first')
        self.assertFalse(self.writer.closed)
        self.assertFalse(self.audio_decoders[0].diagnostics['closed'])
        await self.hub.release('second')
        self.assertEqual(self.commands(), [0xa9, 1, 7])
        self.assertTrue(self.audio_decoders[0].diagnostics['closed'])

    async def test_unknown_codec_and_invalid_audio_are_isolated_and_video_continues(self):
        self.send_frames(frame_bytes(), audio_frame_bytes(b'PRIVATE_UNSUPPORTED_AUDIO', codec=12),
                         audio_frame_bytes(b''), audio_frame_bytes(), frame_bytes())
        await self.hub.acquire('viewer')
        await self.wait_frames(3)
        self.assertEqual([kind for kind, _ in self.frames], ['video', 'audio', 'video'])
        self.assert_shared_connection()
        obs = self.hub.live.observation
        self.assertEqual(obs['decoded_frames'], 2)
        self.assertEqual(obs['audio']['input_packets'], 3)
        self.assertEqual(obs['audio']['unsupported_packets'], 1)
        self.assertEqual(obs['audio']['decode_errors'], 1)
        self.assertEqual(obs['audio']['decoded_frames'], 1)
        self.assertEqual(obs['audio']['status'], 'decoded')
        self.assertIsNone(obs['last_error_type'])
        self.assertEqual(obs['frame_type_counts']['3'], 3)
        self.assertEqual(obs['frame_codec_counts']['12'], 1)
        self.assertNotIn('PRIVATE', json.dumps(self.hub.diagnostics()))

    async def test_new_control_consent_reuses_live_session_for_one_encrypted_opening(self):
        self.hub.entry.data.update(experimental_tcp_controls=True, experimental_outputs=True,
                                   opening_code='123456')
        self.hub.capabilities = replace(self.hub.capabilities, talkback=True, strike=True, gate=True)
        self.send_frames(frame_bytes(), audio_frame_bytes())
        await self.hub.acquire('viewer')
        await self.wait_frames(2)
        self.assertTrue(self.hub.live.session._tcp_outputs_enabled)
        parameters = self.hub.live.talk_parameters()
        self.assertEqual(parameters['transport'], 'connect3_tcp')
        self.assertTrue(parameters['cgi_verified'])
        self.assertEqual(parameters['stream_key'], KEY)
        for value in (False, 1, 'true'):
            self.hub.entry.data['experimental_tcp_controls'] = value
            with self.assertRaisesRegex(RuntimeError, 'TCP microphone disabled'):
                self.hub.live.talk_parameters()
        self.hub.entry.data['experimental_tcp_controls'] = True
        controls = load('connect3.control').Connect3OutputController(self.hub)
        try:
            action = asyncio.create_task(controls.unlock(0))
            async with asyncio.timeout(5):
                while 0xfe not in self.commands():
                    await asyncio.sleep(0)
            self.reader.feed_data(unlock_reply())
            await asyncio.wait_for(action, 5)
            self.assertEqual(self.commands(), [0xa9, 1, 0xfe])
            self.assertEqual(self.hub.consumers, {'viewer'})
            self.assertFalse(self.writer.closed)
            self.assertEqual(controls.diagnostics()['response_count'], 1)
            self.assertFalse(controls.diagnostics()['physical_activation_verified'])
            session.open_connect3_media_tcp.assert_awaited_once()
            live.read_stream_material.assert_awaited_once()
        finally:
            await controls.close()

    async def test_last_consumer_waits_for_audio_worker_before_decoder_close(self):
        entered, finish = threading.Event(), threading.Event()
        lifecycle, failures = [], []
        loop = asyncio.get_running_loop()
        previous_handler = loop.get_exception_handler()
        loop.set_exception_handler(lambda _loop, context: failures.append(context))
        class BlockingAudioDecoder:
            def __init__(self):
                self.diagnostics = {'status': 'idle', 'closed': False}
            def feed(self, packet):
                entered.set()
                if not finish.wait(10):
                    raise AssertionError('Synthetic audio worker timeout')
                lifecycle.append('worker_finished')
                raise live.AudioDecodeError('audio_decode_error')
            def close(self):
                lifecycle.append('decoder_closed')
                self.diagnostics['closed'] = True
        try:
            with patch.object(live, 'AudioDecoder', BlockingAudioDecoder):
                self.send_frames(frame_bytes(), audio_frame_bytes())
                await self.hub.acquire('viewer')
                self.assertTrue(await asyncio.to_thread(entered.wait, 5))
                release = asyncio.create_task(self.hub.release('viewer'))
                async with asyncio.timeout(5):
                    while not self.writer.closed:
                        await asyncio.sleep(0)
                self.assertFalse(release.done())
                self.assertEqual(lifecycle, [])
                finish.set()
                await asyncio.wait_for(release, 5)
                await asyncio.sleep(0)
                self.assertEqual(lifecycle, ['worker_finished', 'decoder_closed'])
                self.assertEqual(self.commands(), [0xa9, 1, 7])
                self.assertEqual([kind for kind, _ in self.frames], ['video'])
                self.assertEqual(failures, [])
        finally:
            finish.set()
            loop.set_exception_handler(previous_handler)


if __name__ == '__main__':
    unittest.main()
