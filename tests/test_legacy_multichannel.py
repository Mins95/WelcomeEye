"""Synthetic LT worker and real hub lease routing; no physical device I/O."""
import ast
import asyncio
import json
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import CAP_IMPORTS, PACKAGE, load

snapshot = load('snapshot')
legacy = load('legacy_channel')
control = load('control')
talkback = load('talkback')
lifecycle = load('lifecycle')
client = load('client')


class Boundary:
    def __init__(self, *args):
        self.thread = None

    def close(self):
        pass


class CaptureBoundary:
    def __init__(self, *args):
        self.close = AsyncMock()
        self.request = Mock()
        self.reject_unsupported_channel = Mock()


root = Path(__file__).parents[1] / 'custom_components/welcomeeye_local'
tree = ast.parse((root / 'hub.py').read_text(encoding='utf-8'))
tree.body = [node for node in tree.body if not (isinstance(node, ast.ImportFrom)
    and (node.level or node.module.startswith('homeassistant')))]
namespace = {**CAP_IMPORTS, '__name__': PACKAGE + '.legacy_fixture', '__package__': PACKAGE,
    '_finish_task': snapshot._finish_task, 'DOMAIN': 'welcomeeye_local', 'RING_HOLD_SECONDS': 5.0,
    'DeviceController': control.DeviceController, 'Talkback': talkback.Talkback,
    'RingListener': Boundary, 'RingImageCapture': CaptureBoundary,
    'ManualSnapshotCapture': CaptureBoundary, 'new_lifecycle': lifecycle.new_lifecycle,
    'AuthenticationError': client.AuthenticationError}
exec(compile(tree, str(root / 'hub.py'), 'exec'), namespace)
Hub = namespace['WelcomeEyeHub']


class LegacyChannelTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.entry = SimpleNamespace(data={'host': '192.0.2.70', 'username': 'SYNTHETIC_USER',
            'password': 'SYNTHETIC_PASSWORD', 'device_variant': 'connect_v1',
            'channel': 16, 'second_channel_enabled': True})
        update = Mock(side_effect=lambda entry, *, data: setattr(entry, 'data', data))
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=update))
        self.hub = Hub(hass, self.entry)
        self.hub.stopped = False
        self.secondary = self.hub.channel2
        self.starts = []
        self.main_frames, self.second_frames = [], []
        self.frames_delivered = {1: asyncio.Event(), 2: asyncio.Event()}
        self.hub.frame_listeners.add(lambda kind, frame: self.main_frames.append((kind, frame)))
        self.secondary.frame_listeners.add(lambda kind, frame: self.second_frames.append((kind, frame)))
        self.hub.image = b'MAIN_CACHED'

        def synthetic_worker(generation, stop):
            channel = self.hub._active_media_channel
            self.starts.append(self.hub._profile_order())
            for callback, args in ((self.hub._state, (True,)),
                    (self.hub._image, (f'JPEG_{channel}'.encode(),)),
                    (self.hub._frame, ('video', f'VIDEO_{channel}')),
                    (self.hub._frame, ('audio', f'AUDIO_{channel}')),
                    (self.frames_delivered[channel].set, ())):
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, callback, *args)
            stop.wait()
        self.hub._worker = synthetic_worker

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertIsNone(self.hub.thread)
        self.assertFalse(self.hub.consumers)
        self.assertFalse(self.secondary.consumers)

    async def test_second_source_uses_single_worker_wire17_and_routes_video_audio_only_there(self):
        await self.secondary.acquire('second')
        await asyncio.wait_for(self.frames_delivered[2].wait(), 2)
        self.assertEqual(self.starts, [[('lt_second_panel', 17, 1, 2)]])
        self.assertIs(self.secondary.entry, self.entry)
        self.assertEqual(self.second_frames, [('video', 'VIDEO_2'), ('audio', 'AUDIO_2')])
        self.assertEqual(self.main_frames, [])
        self.assertEqual(self.hub.image, b'MAIN_CACHED')
        self.assertEqual(self.secondary.image, b'JPEG_2')
        self.assertTrue(self.secondary.connected)
        self.assertFalse(self.hub.connected)
        self.assertFalse(self.hub.media_retry_allowed)
        self.assertEqual(self.hub.confirmed_media_channels, frozenset({2}))
        await self.secondary.release('second')
        self.assertIsNone(self.hub.thread)
        self.assertEqual(self.hub._active_media_channel, 1)
        self.assertFalse(self.secondary.connected)
        self.assertFalse(self.secondary.capabilities.talkback)
        self.assertFalse(self.secondary.capabilities.strike)
        self.assertFalse(self.secondary.capabilities.gate)

    async def test_main_snapshot_microphone_and_outputs_cannot_use_second_session(self):
        await self.secondary.acquire('second')
        worker = self.hub.thread
        with self.assertRaises(legacy.ChannelBusyError):
            await self.hub.acquire('main')
        self.assertIsNone(await snapshot.capture_fresh_image(self.hub))
        self.assertEqual(self.hub.snapshot_last_error_type, 'ChannelBusyError')
        with self.assertRaisesRegex(control.ProtocolError, 'seconde platine'):
            self.hub.control.unlock(0)
        with self.assertRaisesRegex(talkback.ProtocolError, 'channel route'):
            await self.hub.talkback.start(object())
        self.assertIs(self.hub.thread, worker)
        self.assertEqual(len(self.starts), 1)
        self.assertEqual(self.hub.control.request_send_attempt_count, 0)
        self.assertEqual(self.hub.image, b'MAIN_CACHED')
        await self.secondary.release('second')

    async def test_existing_main_viewer_or_hls_control_and_talk_block_second_before_worker(self):
        await self.hub.acquire('main')
        with self.assertRaises(legacy.ChannelBusyError):
            await self.secondary.acquire('second')
        self.assertEqual(len(self.starts), 1)
        await self.hub.release('main')
        for field in ('queues', 'handlers'):
            getattr(self.hub, field).add('busy')
            with self.assertRaises(legacy.ChannelBusyError):
                await self.secondary.acquire('second')
            getattr(self.hub, field).clear()
        self.hub.control.lock.acquire()
        try:
            with self.assertRaises(legacy.ChannelBusyError):
                await self.secondary.acquire('second')
        finally:
            self.hub.control.lock.release()
        self.hub.talkback.owner = object()
        with self.assertRaises(legacy.ChannelBusyError):
            await self.secondary.acquire('second')
        self.hub.talkback.owner = None
        self.assertEqual(len(self.starts), 1)

    async def test_two_second_viewers_share_worker_and_main_can_resume_after_both_close(self):
        await asyncio.gather(self.secondary.acquire('one'), self.secondary.acquire('two'))
        await asyncio.wait_for(self.frames_delivered[2].wait(), 2)
        worker = self.hub.thread
        await self.secondary.release('one')
        self.assertIs(self.hub.thread, worker)
        self.assertTrue(worker.is_alive())
        await self.secondary.release('two')
        self.assertFalse(worker.is_alive())
        await self.hub.acquire('main')
        await asyncio.wait_for(self.frames_delivered[1].wait(), 2)
        self.assertEqual(self.main_frames, [('video', 'VIDEO_1'), ('audio', 'AUDIO_1')])
        self.assertEqual(self.hub.image, b'JPEG_1')
        self.assertEqual(self.secondary.image, b'JPEG_2')
        self.assertEqual(self.starts[-1], [('lt_apk_v1', 16, 1, 2)])
        self.assertTrue(self.hub.media_retry_allowed)
        self.assertEqual(self.hub.confirmed_media_channels, frozenset({1, 2}))
        await self.hub.release('main')

    async def test_stop_closes_child_viewers_and_does_not_leak_observation_binding(self):
        close = AsyncMock()
        self.secondary.close_listeners.add(close)
        await self.secondary.acquire('second')
        await self.hub.stop()
        close.assert_awaited_once()
        self.assertFalse(self.secondary.connected)
        self.assertFalse(self.secondary.consumers)
        with self.assertRaises(ConnectionError):
            await self.secondary.acquire('again')
        diag = json.dumps(self.hub.channel_diagnostics())
        for forbidden in ('SYNTHETIC', '192.0.2.70', legacy.profile_binding(self.entry.data)):
            self.assertNotIn(forbidden, diag)

    async def test_channel_option_defaults_off_and_supports_both_legacy_models(self):
        for variant in ('connect_v1', 'connect2_r001', 'legacy_unknown'):
            hub = Hub(None, SimpleNamespace(data={**self.entry.data, 'device_variant': variant}))
            self.assertTrue(hub.supports_multichannel_player)
            self.assertIsNotNone(hub.channel2)
            self.assertIsNone(hub.thread)
            await hub.stop()
        for value in (None, False, 1, 'true'):
            hub = Hub(None, SimpleNamespace(data={**self.entry.data, 'second_channel_enabled': value}))
            self.assertIsNone(hub.channel2)
            await hub.stop()

    async def test_secondary_ring_keeps_event_but_never_photographs_main(self):
        events = []
        self.hub.hass.bus = SimpleNamespace(async_fire=lambda name, payload: events.append(payload))
        self.entry.entry_id = 'synthetic'
        self.entry.unique_id = self.hub._v1_cloud_uid = 'synthetic_uid'
        for enabled in (False, True):
            self.entry.data.update(device_variant='connect_v1',
                v1_cloud_doorbell_enabled=True, second_channel_enabled=enabled)
            for channel in (1, 2):
                self.hub.ring_image.request.reset_mock()
                self.hub.ring_image.reject_unsupported_channel.reset_mock()
                self.hub._cloud_ring(channel)
                self.assertEqual(events[-1]['channel'], channel)
                self.assertEqual(self.hub.ring_image.reject_unsupported_channel.called,
                    enabled and channel == 2)
                self.assertEqual(self.hub.ring_image.request.called,
                    not (enabled and channel == 2))
            self.entry.data['device_variant'] = 'connect2_r001'
            for channel in (16, 2, 17):
                self.hub.ring_image.request.reset_mock()
                self.hub.ring_image.reject_unsupported_channel.reset_mock()
                self.hub._ring(SimpleNamespace(channel=channel))
                self.assertEqual(events[-1]['channel'], channel)
                self.assertEqual(self.hub.ring_image.reject_unsupported_channel.called,
                    enabled and channel in (2, 17))
                self.assertEqual(self.hub.ring_image.request.called,
                    not (enabled and channel in (2, 17)))
        self.assertIsNone(self.hub.thread)

    async def test_profile_change_invalidates_previous_observed_channel(self):
        await self.secondary.acquire('second')
        await asyncio.wait_for(self.frames_delivered[2].wait(), 2)
        await self.secondary.release('second')
        self.assertEqual(self.hub.confirmed_media_channels, frozenset({2}))
        self.entry.data = {**self.entry.data, 'host': '192.0.2.71'}
        self.assertEqual(self.hub.confirmed_media_channels, frozenset())

    async def test_cancel_pending_second_acquisition_joins_worker_and_allows_main(self):
        started = asyncio.Event()
        original = self.hub._worker
        def waiting_worker(generation, stop):
            self.hub.loop.call_soon_threadsafe(started.set)
            stop.wait()
        self.hub._worker = waiting_worker
        acquiring = asyncio.create_task(self.secondary.acquire('second'))
        await asyncio.wait_for(started.wait(), 2)
        acquiring.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await acquiring
        self.assertIsNone(self.hub.thread)
        self.assertFalse(self.hub.consumers)
        self.assertFalse(self.secondary.consumers)
        self.assertEqual(self.hub._active_media_channel, 1)
        self.hub._worker = original
        await self.hub.acquire('main')
        await self.hub.release('main')

    async def test_actual_worker_failed_channel17_never_tries_main_profile_or_reconnect(self):
        attempts = []
        class RefusedSession:
            connection_stage = 'failed_tcp_connect'
            def __init__(self, *args, **kwargs):
                attempts.append(kwargs)
            def connect(self):
                raise ConnectionRefusedError
            def connection_diagnostics(self):
                return {}
            def interrupt_read(self):
                pass
        self.hub._worker = Hub._worker.__get__(self.hub)
        self.hub._finish_session = lambda *args: None
        self.hub._observe_v1_media = lambda *args: None
        self.hub.control.v1_media.pending_snapshot = lambda *args: {}
        with patch.dict(namespace, Session=RefusedSession, exit_reason=lambda *args: 'connect_error'):
            with self.assertRaises(ConnectionRefusedError):
                await self.secondary.acquire('second')
        self.assertEqual(attempts, [{'channel': 17, 'stream': 1, 'mode': 2}])
        self.hub.session = None
        self.assertIsNone(self.hub.thread)
        self.assertEqual(self.hub._active_media_channel, 1)
        self.assertEqual(self.secondary.diagnostics()['last_error_type'], 'ConnectionRefusedError')


if __name__ == '__main__':
    unittest.main()
