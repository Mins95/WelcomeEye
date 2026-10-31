"""R002 manual photos reuse real decoded QV media and temporary storage only."""
import asyncio
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import cap, load

sys.path.insert(0, str(Path(__file__).resolve().parents[1] / 'tools'))
from qv_runtime_support import PASSWORD, STREAM_KEY, SyntheticQVPeer

hubs = load('r002.hub')
live = load('connect3.live')
session = load('connect3.session')
cgi = load('connect3.cgi')
storage = load('media_storage')


def data(**updates):
    return {'host': '192.0.2.10', 'protocol_family': cap.ProtocolFamily.R002,
        'device_variant': cap.DeviceVariant.R002, 'r002_connection_mode': 'manual',
        'auth_code': PASSWORD, 'experimental_video': True,
        'media_transport': 'connect3_tcp', 'media_tcp_approved': True,
        'certificate_sha256': 'a' * 64,
        'trust_endpoint': {'host': '192.0.2.10', 'cgi_port': 443, 'media_port': 34567,
                           'media_transport': 'connect3_tcp'},
        'tls_certificate_expires': {'cgi': '2099-01-01T00:00:00+00:00'}, **updates}


class R002SnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        self.hubs, self.peers = [], []
        self.hass = SimpleNamespace(
            config=SimpleNamespace(media_dirs={'local': self.temp.name}, time_zone='UTC'),
            async_add_executor_job=asyncio.to_thread,
            bus=SimpleNamespace(async_fire=lambda *args: self.events.append(args)))

        async def material(host, auth, *, diagnostics, **options):
            self.assertEqual((host, auth, options['port']), ('192.0.2.10', PASSWORD, 443))
            self.assertEqual(options['certificate_sha256'], 'a' * 64)
            diagnostics.update(tls_verified=True, tls_policy='certificate_pin',
                               authentication_status='accepted')
            return cgi.StreamMaterial(STREAM_KEY)

        async def connect(*args, **kwargs):
            peer = SyntheticQVPeer(audio_channels=0, audio_frame_type=3)
            self.peers.append(peer)
            return peer.reader, peer

        async def resolve(host, observation):
            self.assertEqual(host, '192.0.2.10')
            observation.update(endpoint_source='fresh_qv_discovery',
                discovery_model_matched=True, endpoint_profile='r002_qv_apk_tcp_34567')
            return {'cgi_port': 443, 'port': 34567, 'transport': 'r002_tcp'}

        self.read = AsyncMock(side_effect=material)
        self.connect = AsyncMock(side_effect=connect)
        self.legacy_connect = AsyncMock(side_effect=connect)
        self.resolve = AsyncMock(side_effect=resolve)
        self.patches = [
            patch.object(live, 'read_stream_material', self.read),
            patch.object(live, 'discover', AsyncMock(side_effect=AssertionError('No extra broadcast'))),
            patch.object(hubs, 'resolve_endpoint', self.resolve),
            patch.object(session, 'open_connect3_media_tcp', self.connect),
            patch.object(session, 'open_r002_media_tcp', self.legacy_connect),
            patch.object(session, 'open_media_tls', AsyncMock(side_effect=AssertionError('No fallback'))),
            patch.object(session, 'KEEPALIVE_INTERVAL', 999),
        ]
        for item in self.patches:
            item.start()

    async def asyncTearDown(self):
        try:
            for hub in self.hubs:
                await hub.stop()
                self.assertIsNone(hub._media_claim)
                self.assertFalse(hub.live._image_waiters)
                self.assertFalse(hub.consumers)
            for peer in self.peers:
                self.assertTrue(peer.closed)
                self.assertEqual(peer.close_count, 1)
                self.assertEqual(peer.outputs, [])
        finally:
            for item in reversed(self.patches):
                item.stop()
            self.temp.cleanup()

    async def hub(self, **updates):
        # Initialization/start must not acquire a media connection. The fake
        # HA facade is installed after Repairs startup, outside this unit scope.
        entry = SimpleNamespace(entry_id='r002-snapshot-entry', data=data(**updates))
        hub = hubs.R002InvestigationHub(None, entry)
        self.hubs.append(hub)
        await hub.start()
        hub.hass = self.hass
        if hub.channel2 is not None:
            hub.channel2.hass = self.hass
        return hub

    async def wait_for(self, predicate):
        async with asyncio.timeout(3):
            while not predicate():
                await asyncio.sleep(.005)

    async def pause(self, peer):
        peer.producer.cancel()
        await asyncio.gather(peer.producer, return_exceptions=True)
        # Drain packets already queued by the producer before testing freshness.
        await self.wait_for(lambda: not peer.reader._buffer)
        await asyncio.sleep(.01)

    async def test_enabled_only_with_media_and_preserves_r002_identity(self):
        hub = await self.hub(second_channel_enabled=True)
        self.assertEqual(hub.variant, cap.DeviceVariant.R002)
        self.assertEqual(hub.protocol_family, cap.ProtocolFamily.R002)
        self.assertTrue(hub.capabilities.manual_snapshot)
        self.assertTrue(hub.capabilities.last_snapshot)
        self.assertTrue(hub.channel2.capabilities.manual_snapshot)
        self.assertFalse(hub.capabilities.ring_image_capture)
        self.assertEqual(hub.confirmed_media_channels, frozenset())
        self.assertEqual(hub.manual_snapshot.diagnostics['media_sessions_started'], 0)
        disabled = await self.hub(experimental_video=False)
        self.assertFalse(disabled.capabilities.manual_snapshot)
        self.assertIsNone(disabled.manual_snapshot)
        self.read.assert_not_awaited()
        self.connect.assert_not_awaited()
        self.resolve.assert_not_awaited()

    async def test_manual_ip_photo_uses_fresh_active_decoder_and_persists(self):
        hub = await self.hub()
        await hub.acquire('viewer')
        peer = self.peers[0]
        before = list(peer.commands)
        result = await hub.manual_snapshot.capture()
        self.assertTrue(result['saved'])
        self.assertEqual((result['channel'], result['source']), (1, 'active_stream_channel_1'))
        saved = Path(self.temp.name) / result['filename']
        self.assertEqual(saved.read_bytes(), storage.validate_jpeg(hub.manual_snapshot.jpeg))
        self.assertIn('_channel_1/', result['filename'])
        self.assertTrue(result['media_content_id'].startswith('media-source://welcomeeye_local/local/'))
        self.assertEqual(peer.commands, before)
        self.assertEqual(hub.consumers, {'viewer'})
        self.assertTrue(hub.connected)
        self.assertEqual(self.events[0][0], 'welcomeeye_local.snapshot_saved')
        self.assertEqual(self.events[0][1]['channel'], 1)
        self.assertEqual(self.read.await_count, 1)
        self.assertEqual(self.connect.await_count, 1)
        self.legacy_connect.assert_not_awaited()
        self.resolve.assert_not_awaited()
        self.assertEqual(hub.channel_diagnostics()['channels']['1']['snapshot']['successes'], 1)
        self.assertNotIn(PASSWORD, repr(hub.channel_diagnostics()))
        self.assertNotIn(STREAM_KEY, repr(hub.channel_diagnostics()))
        await hub.release('viewer')
        self.assertEqual(peer.teardowns, 1)
        self.assertTrue(saved.exists())

    async def test_legacy_discovery_photo_keeps_existing_qv_transport(self):
        hub = await self.hub(r002_connection_mode='discovery')
        await hub.acquire('viewer')
        result = await hub.manual_snapshot.capture()
        self.assertTrue(result['saved'])
        self.assertEqual(self.resolve.await_count, 1)
        self.assertEqual(self.legacy_connect.await_count, 1)
        self.connect.assert_not_awaited()
        self.assertEqual(self.read.await_count, 1)
        self.assertEqual(hub.live.session._transport, 'r002_tcp')
        self.assertEqual(hub.variant, cap.DeviceVariant.R002)
        self.assertEqual(hub.channel_diagnostics()['channel_detection']['observed_channels'], [])
        await hub.release('viewer')

    async def test_closed_or_other_channel_never_starts_a_session(self):
        hub = await self.hub(second_channel_enabled=True)
        for target in (hub, hub.channel2):
            with self.assertRaises(Exception):
                await target.manual_snapshot.capture()
            self.assertEqual(target.manual_snapshot.diagnostics['last_error_reason'], 'active_video_required')
        self.read.assert_not_awaited()
        self.connect.assert_not_awaited()
        await hub.acquire('viewer')
        with self.assertRaises(Exception):
            await hub.channel2.manual_snapshot.capture()
        self.assertTrue(hub.connected)
        self.assertIsNone(hub.channel2.manual_snapshot.jpeg)
        self.assertEqual(self.read.await_count, 1)
        self.assertEqual(self.connect.await_count, 1)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    async def test_second_channel_owns_its_photo_and_storage(self):
        hub = await self.hub(second_channel_enabled=True)
        await hub.acquire('viewer')
        first = await hub.manual_snapshot.capture()
        await hub.release('viewer')
        await hub.channel2.acquire('viewer')
        second = await hub.channel2.manual_snapshot.capture()
        self.assertEqual((first['channel'], second['channel']), (1, 2))
        self.assertIn('_channel_2/', second['filename'])
        self.assertNotEqual(first['filename'], second['filename'])
        self.assertNotEqual(hub.manual_snapshot.storage.device_slug,
                            hub.channel2.manual_snapshot.storage.device_slug)
        self.assertEqual([event[1]['channel'] for event in self.events], [1, 2])
        self.assertEqual(hub.channel2.live.observation['selected_channel'], 2)
        self.assertEqual(self.connect.await_count, 2)
        await hub.channel2.release('viewer')

    async def test_timeout_and_cancellation_keep_previous_photo_without_reacquisition(self):
        hub = await self.hub()
        await hub.acquire('viewer')
        result = await hub.manual_snapshot.capture()
        saved = hub.manual_snapshot.jpeg
        peer = self.peers[0]
        await self.pause(peer)
        before = list(peer.commands)
        with patch.object(live, 'SNAPSHOT_TIMEOUT', .02):
            with self.assertRaises(Exception):
                await hub.manual_snapshot.capture()
        self.assertEqual(hub.manual_snapshot.diagnostics['last_error_reason'], 'fresh_image_timeout')
        task = asyncio.create_task(hub.manual_snapshot.capture())
        await self.wait_for(lambda: bool(hub.live._image_waiters))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(hub.live._image_waiters)
        self.assertEqual(hub.manual_snapshot.jpeg, saved)
        self.assertEqual(hub.manual_snapshot.filename, result['filename'])
        self.assertEqual(len(self.events), 1)
        self.assertEqual(len(list(Path(self.temp.name).rglob('*.jpg'))), 1)
        self.assertEqual(peer.commands, before)
        self.assertEqual(self.connect.await_count, 1)

    async def test_unload_during_pending_capture_closes_without_late_save(self):
        hub = await self.hub()
        await hub.acquire('viewer')
        await self.pause(self.peers[0])
        task = asyncio.create_task(hub.manual_snapshot.capture())
        await self.wait_for(lambda: bool(hub.live._image_waiters))
        await hub.stop()
        with self.assertRaises(Exception):
            await task
        self.assertFalse(hub.live._image_waiters)
        self.assertIsNone(hub.manual_snapshot.jpeg)
        self.assertFalse(self.events)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))
        self.assertEqual(self.peers[0].teardowns, 1)
        self.assertEqual(self.connect.await_count, 1)


if __name__ == '__main__':
    unittest.main()
