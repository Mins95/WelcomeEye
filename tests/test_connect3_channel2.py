"""Synthetic channel selector and bounded lifecycle; no intercom is contacted."""
import asyncio
import json
import struct
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_connect3_media_protocol import KEY, aes, control_response, frame_bytes, media_response
from test_connect3_media_session import Writer
from test_connect3_tcp_security import audio_frame_bytes, setup

hubs = load('connect3.hub')
live = load('connect3.live')
session = load('connect3.session')
cgi = load('connect3.cgi')
trial = load('connect3.channel2')
r002 = load('r002.hub')


class Channel2Tests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.data = {'host': '192.0.2.33', 'auth_code': 'SYNTHETIC_AUTH',
            'certificate_sha256': 'a' * 64, 'media_certificate_sha256': 'b' * 64,
            'experimental_video': True,
            'experimental_tcp_controls': True, 'experimental_outputs': True,
            'opening_code': 'SYNTHETIC_OPENING', 'media_transport': 'connect3_tcp',
            'media_tcp_approved': True,
            'trust_endpoint': {'host': '192.0.2.33', 'cgi_port': 443,
                'media_port': 34567, 'media_transport': 'connect3_tcp'},
            'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}}
        self.hub = hubs.Connect3Hub(None, SimpleNamespace(data=self.data))
        self.proxy = self.hub.channel2
        self.readers, self.writers = [], []
        self.frames, self.main_frames = [], []
        self.proxy.frame_listeners.add(lambda kind, frame: self.frames.append((kind, frame)))
        self.hub.frame_listeners.add(lambda kind, frame: self.main_frames.append((kind, frame)))
        self.video_decoder = SimpleNamespace(errors=0,
            feed=Mock(return_value=([SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG')), close=Mock())

        async def material(*args, diagnostics=None, **kwargs):
            diagnostics.update(authentication_status='accepted', tls_verified=True,
                tls_policy='certificate_pin')
            return cgi.StreamMaterial(KEY)

        async def connect(*args):
            reader, writer = asyncio.StreamReader(), Writer()
            reader.feed_data(setup() + b''.join(control_response()) +
                b''.join(media_response(frame_bytes())) +
                b''.join(media_response(audio_frame_bytes())))
            self.readers.append(reader)
            self.writers.append(writer)
            return reader, writer

        self.patchers = [
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=material)),
            patch.object(live, 'VideoDecoder', Mock(return_value=self.video_decoder)),
            patch.object(live, 'AudioDecoder', Mock(side_effect=AssertionError('Trial audio forbidden'))),
            patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('Manual discovery forbidden'))),
            patch.object(session, 'open_connect3_media_tcp', AsyncMock(side_effect=connect)),
            patch.object(session, 'open_media_tls', AsyncMock(side_effect=connect)),
            patch.object(session, 'KEEPALIVE_INTERVAL', 999),
        ]
        for patcher in self.patchers:
            patcher.start()
        await self.hub.start()

    async def asyncTearDown(self):
        try:
            await self.hub.stop()
            self.assertIsNone(self.proxy.live.task)
            self.assertIsNone(self.hub._media_claim)
            self.assertIsNone(self.proxy._deadline)
            self.assertFalse(self.proxy.consumers)
            self.assertTrue(all(writer.closed for writer in self.writers))
        finally:
            for patcher in reversed(self.patchers):
                patcher.stop()

    async def wait_for(self, predicate):
        async with asyncio.timeout(5):
            while not predicate():
                await asyncio.sleep(0)

    def commands(self, writer):
        return [packet[0] if index == 0 else aes(packet[:32], decrypt=True)[0]
                for index, packet in enumerate(writer.writes)]

    async def test_trial_uses_parent_credentials_and_fixed_channel_without_audio_or_controls(self):
        await self.proxy.acquire('viewer')
        await self.wait_for(lambda: self.proxy.live.observation.get('ignored_audio_frames') == 1)
        self.assertIs(self.proxy.entry, self.hub.entry)
        live.read_stream_material.assert_awaited_once_with('192.0.2.33', 'SYNTHETIC_AUTH',
            port=443, certificate_sha256='a' * 64, diagnostics={
                'authentication_status': 'accepted', 'tls_verified': True,
                'tls_policy': 'certificate_pin'})
        session.open_connect3_media_tcp.assert_awaited_once()
        session.open_media_tls.assert_not_called()
        live.discover.assert_not_called()
        live.AudioDecoder.assert_not_called()
        header = aes(self.writers[0].writes[1][:32], decrypt=True)
        self.assertEqual(struct.unpack_from('<H', header, 13)[0], 2)
        self.assertEqual(header[16], 1)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.assertEqual([kind for kind, _ in self.frames], ['video'])
        self.assertEqual(self.main_frames, [])
        self.assertIsNone(self.hub.image)
        self.assertEqual(self.proxy.image, b'SYNTHETIC_JPEG')
        for capability in ('talkback', 'strike', 'gate', 'downstream_audio', 'manual_snapshot'):
            self.assertFalse(getattr(self.proxy.capabilities, capability))
        with self.assertRaisesRegex(RuntimeError, 'video only'):
            self.proxy.live.talk_parameters()
        with self.assertRaisesRegex(session.OutputFailure, 'channel_trial_video_only'):
            await self.proxy.live.session.execute_output(1, 'UNSENT')
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        await self.proxy.release('viewer')
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1, 7])
        self.assertIsNone(self.proxy._deadline)
        for secret in ('SYNTHETIC', KEY, 'a' * 64, 'b' * 64, '192.0.2.33'):
            self.assertNotIn(secret, json.dumps(self.hub.diagnostics()))

    async def test_tls_trial_keeps_media_pin_and_blocks_output_at_write_boundary(self):
        self.data['media_transport'] = 'tls'
        self.data.pop('trust_endpoint')
        self.data.pop('tls_certificate_expires')
        await self.proxy.acquire('viewer')
        session.open_media_tls.assert_awaited_once()
        self.assertEqual(session.open_media_tls.await_args.args[:3], ('192.0.2.33', 8443, 'b' * 64))
        session.open_connect3_media_tcp.assert_not_called()
        with self.assertRaisesRegex(session.OutputFailure, 'channel_trial_video_only'):
            await self.proxy.live.session._send(b'UNSENT', physical=True)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])

    async def test_main_controls_and_explicit_diagnostics_refuse_before_network_during_trial(self):
        await self.proxy.acquire('viewer')
        with self.assertRaisesRegex(RuntimeError, 'other channel busy'):
            await self.hub.acquire('main')
        with self.assertRaises(RuntimeError):
            await self.hub.control.unlock(0)
        with self.assertRaises(RuntimeError):
            await self.hub.execute('access')
        self.assertEqual(live.read_stream_material.await_count, 1)
        self.assertEqual(len(self.writers), 1)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.assertTrue(self.proxy.connected)
        self.assertEqual(self.hub.control.diagnostics()['request_sent_count'], 0)

    async def test_main_reservation_and_busy_controls_prevent_trial_without_any_network(self):
        for field, value in (('_media_claim', self.hub.live),):
            setattr(self.hub, field, value)
            with self.assertRaisesRegex(RuntimeError, 'busy'):
                await self.proxy.acquire('trial')
            setattr(self.hub, field, None)
        self.hub.control._busy = True
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub.control._busy = False
        self.hub.talkback.owner = object()
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub.talkback.owner = None
        self.hub._task = asyncio.create_task(asyncio.sleep(60))
        with self.assertRaisesRegex(RuntimeError, 'busy'):
            await self.proxy.acquire('trial')
        self.hub._task.cancel()
        await asyncio.gather(self.hub._task, return_exceptions=True)
        self.hub._task = None
        live.read_stream_material.assert_not_called()
        self.assertIsNone(self.proxy._deadline)
        self.assertEqual(self.writers, [])

    async def test_two_trial_viewers_share_single_session_and_deadline(self):
        await asyncio.gather(self.proxy.acquire('one'), self.proxy.acquire('two'))
        deadline = self.proxy._deadline
        self.assertEqual(len(self.writers), 1)
        await self.proxy.release('one')
        self.assertIs(self.proxy._deadline, deadline)
        self.assertFalse(self.writers[0].closed)
        await self.proxy.release('two')
        self.assertTrue(self.writers[0].closed)
        self.assertIsNone(self.proxy._deadline)

    async def test_deadline_closes_viewers_and_session_then_allows_another_explicit_trial(self):
        closed = AsyncMock()
        self.proxy.close_listeners.add(closed)
        await self.proxy.acquire('viewer')
        self.assertIsNotNone(self.proxy._deadline)
        self.proxy._deadline.cancel()
        self.proxy._expire()  # Simulated timer: do not wait a real minute.
        await self.proxy._close_task
        closed.assert_awaited_once()
        self.assertFalse(self.proxy.consumers)
        self.assertFalse(self.proxy.connected)
        self.assertIsNone(self.hub._media_claim)
        self.assertTrue(self.writers[0].closed)
        self.assertEqual(self.proxy.diagnostics()['close_reason'], 'trial_timeout')
        await self.proxy.acquire('again')
        self.assertEqual(len(self.writers), 2)
        self.assertTrue(self.proxy.connected)
        await self.proxy.release('again')

    async def test_deadline_includes_pending_https_acquisition_and_cleans_without_credentials_retry(self):
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=pending)) as read:
            acquiring = asyncio.create_task(self.proxy.acquire('viewer'))
            await asyncio.wait_for(started.wait(), 5)
            self.assertIsNotNone(self.proxy._deadline)
            self.proxy._deadline.cancel()
            self.proxy._expire()
            await self.proxy._close_task
            with self.assertRaises(RuntimeError):
                await acquiring
            read.assert_awaited_once()
        self.assertEqual(self.writers, [])
        self.assertIsNone(self.hub._media_claim)
        self.assertIsNone(self.proxy._deadline)

    async def test_cancel_acquisition_unloads_timer_and_reservation_without_orphans(self):
        started = asyncio.Event()
        async def pending(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=pending)):
            acquiring = asyncio.create_task(self.proxy.acquire('viewer'))
            await asyncio.wait_for(started.wait(), 5)
            acquiring.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await acquiring
        self.assertIsNone(self.proxy._deadline)
        self.assertIsNone(self.proxy.live.task)
        self.assertIsNone(self.hub._media_claim)

    async def test_failed_cgi_never_opens_media_and_releases_reservation(self):
        with patch.object(live, 'read_stream_material', AsyncMock(side_effect=cgi.CGIError('auth_code_rejected'))) as read:
            with self.assertRaises(RuntimeError):
                await self.proxy.acquire('viewer')
            read.assert_awaited_once()
        self.assertEqual(self.writers, [])
        self.assertEqual(self.proxy.live.observation['last_error_reason'], 'auth_code_rejected')
        self.assertIsNone(self.hub._media_claim)
        self.assertIsNone(self.proxy._deadline)

    async def test_unload_during_trial_closes_listener_timer_and_rejects_reopen(self):
        closed = AsyncMock()
        self.proxy.close_listeners.add(closed)
        await self.proxy.acquire('viewer')
        await self.hub.stop()
        closed.assert_awaited_once()
        self.assertEqual(self.proxy.diagnostics()['close_reason'], 'integration_unload')
        with self.assertRaises(RuntimeError):
            await self.proxy.acquire('again')
        self.assertEqual(len(self.writers), 1)

    async def test_video_enables_only_idle_trial_and_r002_never_gets_trial(self):
        hub = hubs.Connect3Hub(None, SimpleNamespace(data=dict(self.data)))
        self.assertIsNotNone(hub.channel2)
        self.assertIsNone(hub.channel2.live.task)
        self.assertIsNone(hub.channel2._deadline)
        await hub.stop()
        hub = hubs.Connect3Hub(None, SimpleNamespace(data={**self.data, 'experimental_video': False}))
        self.assertIsNone(hub.channel2)
        await hub.stop()
        hub = r002.R002InvestigationHub(None, SimpleNamespace(data=self.data))
        self.assertIsNone(hub.channel2)
        await hub.stop()

    async def test_invalid_channel_policy_is_rejected_before_transport(self):
        for channel, video_only in ((0, True), (3, True), (True, True), (2, False), (2, 1)):
            with self.assertRaisesRegex(session.qv.MediaProtocolError, 'invalid_media_channel_policy'):
                session.QVSession('192.0.2.33', 8443, 'a'*64, KEY, 'UNSENT', {},
                    channel=channel, video_only=video_only)


if __name__ == '__main__':
    unittest.main()
