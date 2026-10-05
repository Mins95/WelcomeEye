"""Synthetic consumers and frames; no real device or media compatibility claim."""
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

hub_module = load('connect3.hub')
live = load('connect3.live')
cgi = load('connect3.cgi')


class Decoder:
    errors = 0
    def feed(self, packet):
        return [SimpleNamespace(pts=1)], b'SYNTHETIC_JPEG'
    def close(self):
        pass


class LiveTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={
            'host': '192.0.2.1', 'auth_code': 'SYNTHETIC_PASSWORD', 'experimental_video': True,
            'certificate_sha256': 'a'*64}))
        self.sessions = []
        self.opened = asyncio.Event()
        self.produce = asyncio.Event()
        owner = self
        class Session:
            def __init__(self, *args):
                owner.sessions.append(self)
                self.observation = args[-1]
                self.closed = False
            async def run(self, callback):
                self.observation.update(stage='waiting_video', tcp_closed=False, media_tls_verified=True)
                owner.opened.set()
                try:
                    await owner.produce.wait()
                    await callback(SimpleNamespace(frame_type=1))
                    await asyncio.Future()
                finally:
                    await self.close()
            async def close(self):
                self.closed = True
                self.observation['tcp_closed'] = True
        async def read(*args, diagnostics=None, **kwargs):
            diagnostics['authentication_status'] = 'accepted'
            return cgi.StreamMaterial('SYNTHETIC_STREAM_KEY_OF_SUFFICIENT_LENGTH')
        self.patchers = [patch.object(live, 'QVSession', Session),
            patch.object(live, 'VideoDecoder', Decoder),
            patch.object(live, 'read_stream_material', AsyncMock(side_effect=read))]
        for patcher in self.patchers:
            patcher.start()
        self.read = live.read_stream_material
        await self.hub.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        self.assertTrue(all(session.closed for session in self.sessions))
        for patcher in reversed(self.patchers):
            patcher.stop()

    async def test_start_and_diagnostics_do_not_connect(self):
        self.hub.diagnostics()
        self.assertTrue(self.hub.capabilities.camera)
        self.assertFalse(self.hub.capabilities.talkback)
        self.assertFalse(self.hub.capabilities.gate)
        self.assertFalse(self.hub.capabilities.strike)
        self.read.assert_not_called()
        self.assertFalse(self.sessions)

    async def test_disabled_opt_in_has_no_camera_or_io(self):
        hub = hub_module.Connect3Hub(SimpleNamespace(), SimpleNamespace(data={'host': '192.0.2.1'}))
        await hub.start()
        self.assertFalse(hub.capabilities.camera)
        with self.assertRaises(RuntimeError):
            await hub.acquire('viewer')
        await hub.stop()
        self.read.assert_not_called()

    async def test_two_viewers_share_and_last_release_closes(self):
        self.produce.set()
        callback = Mock()
        self.hub.frame_listeners.add(callback)
        await asyncio.gather(self.hub.acquire('one'), self.hub.acquire('two'))
        self.assertEqual(len(self.sessions), 1)
        self.read.assert_awaited_once()
        self.assertTrue(self.hub.connected)
        self.assertEqual(self.hub.image, b'SYNTHETIC_JPEG')
        callback.assert_called_once()
        await self.hub.release('one')
        self.assertFalse(self.sessions[0].closed)
        await self.hub.release('two')
        self.assertTrue(self.sessions[0].closed)
        self.assertFalse(self.hub.connected)

    async def test_second_explicit_open_creates_one_new_session(self):
        self.produce.set()
        for _ in range(3):
            await self.hub.acquire('viewer')
            await self.hub.release('viewer')
        self.assertEqual(len(self.sessions), 3)
        self.assertEqual(self.read.await_count, 3)
        self.assertTrue(all(session.closed for session in self.sessions))

    async def test_cancel_before_first_image_releases_everything(self):
        task = asyncio.create_task(self.hub.acquire('viewer'))
        await self.opened.wait()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        self.assertTrue(self.sessions[0].closed)

    async def test_first_frame_timeout_no_retry(self):
        with patch.object(live, 'ACQUIRE_TIMEOUT', .02):
            with self.assertRaises(TimeoutError):
                await self.hub.acquire('viewer')
        self.read.assert_awaited_once()
        self.assertFalse(self.hub.consumers)
        self.assertTrue(self.sessions[0].closed)

    async def test_unload_during_acquisition_no_late_callbacks(self):
        callback = Mock()
        self.hub.listeners.add(callback)
        task = asyncio.create_task(self.hub.acquire('viewer'))
        await self.opened.wait()
        await self.hub.stop()
        await asyncio.gather(task, return_exceptions=True)
        callback.assert_not_called()
        self.assertFalse(self.hub.consumers)

    async def test_cgi_and_media_failures_are_separate(self):
        class FailedSession:
            def __init__(self, *args):
                self.obs = args[-1]
            async def run(self, callback):
                self.obs['stage'] = 'media_tls_handshake'
                raise live.MediaTLSFailure('media_certificate_pin_mismatch')
            async def close(self):
                self.obs['tcp_closed'] = True
        with patch.object(live, 'QVSession', FailedSession):
            with self.assertRaises(RuntimeError):
                await self.hub.acquire('viewer')
        diagnostic = self.hub.diagnostics()
        self.assertTrue(diagnostic['device_authenticated'])
        self.assertEqual(diagnostic['media']['last_error_reason'], 'media_certificate_pin_mismatch')
        self.assertEqual(diagnostic['media']['failed_at_stage'], 'media_tls_handshake')
        self.read.assert_awaited_once()

    async def test_privacy_diagnostics_never_expose_material_or_image(self):
        self.produce.set()
        await self.hub.acquire('viewer')
        text = json.dumps(self.hub.diagnostics())
        for secret in ('SYNTHETIC', '192.0.2.1', 'a'*64, 'auth_code', 'credential_device_uid'):
            self.assertNotIn(secret, text)
        await self.hub.release('viewer')

    async def test_diagnostics_busy_while_live_and_inverse(self):
        self.produce.set()
        await self.hub.acquire('viewer')
        with self.assertRaises(RuntimeError):
            await self.hub.execute('media_certificate')
        await self.hub.release('viewer')
        self.hub._task = asyncio.create_task(asyncio.sleep(20))
        with self.assertRaises(RuntimeError):
            await self.hub.acquire('viewer')
        self.hub._task.cancel()
        await asyncio.gather(self.hub._task, return_exceptions=True)
        self.hub._task = None

    async def test_qr_identity_guard_prevents_cgi_and_media(self):
        self.hub.entry.data.update(credential_source='apk_json', credential_device_uid='SYNTHETIC_UID')
        with patch.object(live, 'discover', AsyncMock(return_value={'credential_identity_status': 'mismatch'})):
            with self.assertRaises(RuntimeError):
                await self.hub.acquire('viewer')
        self.read.assert_not_called()
        self.assertFalse(self.sessions)


if __name__ == '__main__':
    unittest.main()
