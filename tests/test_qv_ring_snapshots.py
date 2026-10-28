"""QV ring photos with real framing/JPEG/files and simulated local peers only."""
import asyncio
from dataclasses import replace
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, patch

from load_integration import load
import test_connect3_channel2 as fixture
from test_connect3_media_protocol import frame_bytes, media_response
from test_media_storage import jpeg_bytes
from test_ring_image import FakeClock

ring = load('ring_image')
snapshots = load('connect3.ring_snapshot')
manual = load('manual_snapshot')


class QVRingSnapshotTests(unittest.IsolatedAsyncioTestCase):
    wait_for = fixture.Channel2Tests.wait_for
    commands = fixture.Channel2Tests.commands

    async def asyncSetUp(self):
        await fixture.Channel2Tests.asyncSetUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        self.hub.hass = self.proxy.hass = SimpleNamespace(
            config=SimpleNamespace(media_dirs={'local': self.temp.name}, time_zone='UTC'),
            async_add_executor_job=asyncio.to_thread,
            bus=SimpleNamespace(async_fire=lambda *args: self.events.append(args)))
        self.hub.entry.entry_id = 'SYNTHETIC_PRIVATE_ENTRY'
        self.hub.entry.options = {}
        self.hub.capabilities = replace(self.hub.capabilities, cloud_ring=True, ring_image_capture=True)
        self.hub.entry.data['qv_cloud_enabled'] = True
        self.jpeg = jpeg_bytes()
        self.video_decoder.feed.return_value = ([SimpleNamespace(pts=1)], self.jpeg)
        self.clock = FakeClock()
        self.captures = [snapshots.QVRingImageCapture(target) for target in (self.hub, self.proxy)]
        for capture in self.captures:
            capture._clock = lambda: self.clock.now
            capture._call_later = self.clock.call_later
            capture.entity_id = f'image.synthetic_channel_{capture.channel}_last_ring'

    async def asyncTearDown(self):
        try:
            for capture in self.captures:
                await capture.close()
            await fixture.Channel2Tests.asyncTearDown(self)
            self.assertFalse(self.clock.pending)
        finally:
            self.temp.cleanup()

    async def begin(self, capture, sequence=1):
        capture.set_enabled(True)
        capture.request(sequence, None)
        await self.wait_for(lambda: bool(self.clock.pending) or capture._task.done())
        self.assertFalse(capture._task.done())
        self.clock.advance(4)
        await self.wait_for(lambda: bool(capture.hub.live._image_waiters) or capture._task.done())

    def publish(self, target):
        index = next(index for index, writer in enumerate(self.writers)
                     if writer is target.live.session._writer)
        self.readers[index].feed_data(b''.join(media_response(frame_bytes())))

    async def photo(self, capture, sequence=1):
        await self.begin(capture, sequence)
        self.assertFalse(capture._task.done())
        self.publish(capture.hub)
        await capture._task
        self.assertEqual(capture.status, 'ready')

    def photo_events(self):
        return [value for name, value in self.events if name == 'welcomeeye_local.ring_image']

    async def test_defaults_off_and_explicit_option_survives_reconstruction(self):
        for capture in self.captures:
            self.assertFalse(capture.enabled)
            capture.request(1, None)
            self.assertIsNone(capture._task)
            self.assertEqual(capture.diagnostics['ring_capture_requests'], 0)
        self.assertEqual(self.hub.entry.options, {})
        self.assertEqual(self.writers, [])
        fixture.live.read_stream_material.assert_not_called()
        self.hub.entry.options['ring_image_capture'] = True
        enabled = snapshots.QVRingImageCapture(self.hub)
        self.assertTrue(enabled.enabled)
        await enabled.close()
        self.hub.entry.options['ring_image_capture'] = False
        disabled = snapshots.QVRingImageCapture(self.hub)
        self.assertFalse(disabled.enabled)
        await disabled.close()

    async def test_delay_then_one_fresh_session_and_channel_scoped_saved_event(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        capture.request(17, None)
        await self.wait_for(lambda: bool(self.clock.pending))
        self.clock.advance(3.999)
        await asyncio.sleep(0)
        self.assertEqual(self.writers, [])
        self.assertEqual(capture.status, 'pending')
        self.clock.advance(.001)
        await self.wait_for(lambda: bool(self.hub.live._image_waiters))
        self.assertFalse(capture._task.done())  # first cached JPEG is insufficient
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.publish(self.hub)
        await capture._task
        self.assertEqual(capture.jpeg, self.jpeg)
        self.assertEqual(capture.image_sequence, 17)
        self.assertEqual(capture.source, 'fresh_snapshot_channel_1')
        self.assertEqual(capture.diagnostics['last_fallback_started_ms'], 4000)
        self.assertEqual(capture.diagnostics['media_sessions_started'], 1)
        self.assertEqual(capture.diagnostics['media_sessions_reused'], 0)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1, 7])
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        self.assertIn('_channel_1/', capture.filename)
        self.assertEqual((Path(self.temp.name) / capture.filename).read_bytes(), self.jpeg)
        self.assertEqual(self.photo_events()[0]['channel'], 1)
        self.assertEqual(self.photo_events()[0]['source'], capture.source)
        self.assertNotIn('SYNTHETIC', json.dumps(capture.diagnostics))

    async def test_same_channel_reuses_viewer_and_stable_microphone_without_commands(self):
        await self.proxy.acquire('viewer')
        talk = self.proxy.talkback
        talk.owner, talk.active, talk._live_session = 'viewer', True, self.proxy.live.session
        before = list(self.writers[0].writes)
        capture = self.captures[1]
        try:
            await self.photo(capture)
            self.assertEqual(self.writers[0].writes, before)
            self.assertEqual(self.proxy.consumers, {'viewer'})
            self.assertTrue(self.proxy.connected)
            self.assertTrue(talk.active)
            self.assertEqual(talk.owner, 'viewer')
            self.assertEqual(capture.diagnostics['media_sessions_started'], 0)
            self.assertEqual(capture.diagnostics['media_sessions_reused'], 1)
            self.assertEqual(len(self.writers), 1)
        finally:
            talk.owner, talk.active, talk._live_session = None, False, None

    async def test_viewer_arriving_during_capture_keeps_the_shared_session(self):
        capture = self.captures[0]
        await self.begin(capture)
        self.assertEqual(len(self.hub.consumers), 1)
        lease = next(iter(self.hub.consumers))
        self.assertTrue(lease.single_session_attempt)
        await self.hub.acquire('late-viewer')
        self.publish(self.hub)
        await capture._task
        self.assertEqual(self.hub.consumers, {'late-viewer'})
        self.assertTrue(self.hub.connected)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])
        self.assertEqual(len(self.writers), 1)

    async def test_other_channel_busy_refuses_before_acquisition_and_keeps_viewer(self):
        await self.hub.acquire('viewer')
        before = list(self.writers[0].writes)
        capture = self.captures[1]
        await self.begin(capture)
        await capture._task
        self.assertEqual(capture.status, 'failed')
        self.assertEqual(capture.diagnostics['last_error_reason'], 'other_channel_busy')
        self.assertEqual(capture.diagnostics['media_sessions_started'], 0)
        self.assertEqual(self.hub.consumers, {'viewer'})
        self.assertEqual(self.writers[0].writes, before)
        self.assertEqual(len(self.writers), 1)
        self.assertFalse(self.photo_events())

    async def test_busy_controls_diagnostic_and_microphone_transition_never_start_media(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        for reason, begin, end in (
            ('control_busy', lambda: setattr(self.hub.control, '_busy', True),
             lambda: setattr(self.hub.control, '_busy', False)),
            ('microphone_busy', lambda: setattr(self.hub.talkback, 'owner', 'viewer'),
             lambda: setattr(self.hub.talkback, 'owner', None))):
            begin()
            try:
                with self.assertRaises(RuntimeError):
                    await capture._capture_image(timeout=.1)
                self.assertEqual(capture.diagnostics['last_error_reason'], reason)
            finally:
                end()
        for owner, attribute in ((self.hub, '_task'), (self.hub.autonomous_doorbell, 'task')):
            task = asyncio.create_task(asyncio.sleep(60))
            setattr(owner, attribute, task)
            try:
                with self.assertRaises(RuntimeError):
                    await capture._capture_image(timeout=.1)
                self.assertEqual(capture.diagnostics['last_error_reason'], 'diagnostic_busy')
                self.assertFalse(task.done())
            finally:
                task.cancel()
                await asyncio.gather(task, return_exceptions=True)
                setattr(owner, attribute, None)
        self.assertEqual(self.writers, [])
        fixture.live.read_stream_material.assert_not_called()

    async def test_talk_start_or_stop_lock_refuses_even_with_connected_live(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        await self.hub.acquire('viewer')
        talk = self.hub.talkback
        before = list(self.writers[0].writes)
        for lock in (talk._lock, talk._close_lock):
            async with lock:
                with self.assertRaises(RuntimeError):
                    await capture._capture_image(timeout=.1)
            self.assertEqual(capture.diagnostics['last_error_reason'], 'microphone_busy')
        self.assertEqual(self.hub.consumers, {'viewer'})
        self.assertEqual(self.writers[0].writes, before)

    async def test_timeout_keeps_previous_photo_and_timestamp_with_single_attempt(self):
        capture = self.captures[0]
        await self.photo(capture)
        old = (capture.jpeg, capture.updated, capture.image_sequence, capture.filename)
        with patch.object(fixture.live, 'SNAPSHOT_TIMEOUT', .02):
            await self.begin(capture, 2)
            await capture._task
        self.assertEqual(capture.status, 'failed')
        self.assertEqual((capture.jpeg, capture.updated, capture.image_sequence, capture.filename), old)
        self.assertEqual(capture.diagnostics['last_error_reason'], 'fresh_image_timeout')
        self.assertEqual(capture.diagnostics['media_sessions_started'], 2)
        self.assertEqual(len(self.writers), 2)
        self.assertTrue(all(writer.closed for writer in self.writers))
        self.assertEqual(len(self.photo_events()), 1)

    async def test_acquisition_timeout_and_failure_make_one_attempt_and_release(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        entered = asyncio.Event()

        async def blocked(*args, **kwargs):
            entered.set()
            await asyncio.Event().wait()

        with patch.object(fixture.live, 'read_stream_material', AsyncMock(side_effect=blocked)) as material:
            with self.assertRaises(TimeoutError):
                await capture._capture_image(timeout=.02)
            material.assert_awaited_once()
        self.assertTrue(entered.is_set())
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        with patch.object(fixture.live, 'read_stream_material',
                          AsyncMock(side_effect=RuntimeError('SYNTHETIC_PRIVATE_FAILURE'))) as material:
            with self.assertRaisesRegex(RuntimeError, '^Ring image capture failed$'):
                await capture._capture_image(timeout=.2)
            material.assert_awaited_once()
        self.assertEqual(capture.diagnostics['last_error_reason'], 'media_acquisition_failed')
        self.assertEqual(capture.diagnostics['media_sessions_started'], 2)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.live.task)
        self.assertNotIn('SYNTHETIC', json.dumps(capture.diagnostics))

    async def test_off_before_delay_and_during_capture_never_publish_late_photo(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        capture.request(1, None)
        await self.wait_for(lambda: bool(self.clock.pending))
        capture.set_enabled(False)
        await capture._task
        self.assertFalse(self.writers)
        capture.set_enabled(True)
        await self.begin(capture, 2)
        capture.set_enabled(False)
        capture.set_enabled(True)
        self.publish(self.hub)
        await capture._task
        self.assertIsNone(capture.jpeg)
        self.assertFalse(self.photo_events())
        self.assertFalse(self.hub.consumers)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    async def test_newer_same_channel_ring_supersedes_old_result_and_starts_only_next_attempt(self):
        capture = self.captures[0]
        await self.begin(capture, 1)
        capture.request(2, None)
        self.publish(self.hub)
        await self.wait_for(lambda: bool(self.clock.pending))
        self.assertFalse(self.photo_events())
        self.clock.advance(4)
        await self.wait_for(lambda: bool(self.hub.live._image_waiters))
        self.publish(self.hub)
        await capture._task
        self.assertEqual(capture.image_sequence, 2)
        self.assertEqual([value['ring_sequence'] for value in self.photo_events()], [2])
        self.assertEqual(len(self.writers), 2)
        self.assertEqual(capture.diagnostics['ring_capture_superseded'], 1)

    async def test_parallel_channels_refuse_conflict_without_cancelling_first_capture(self):
        first, second = self.captures
        await self.begin(first, 1)
        await self.begin(second, 2)
        await second._task
        self.assertEqual(second.diagnostics['last_error_reason'], 'other_channel_busy')
        self.assertTrue(self.hub.live._image_waiters)
        self.publish(self.hub)
        await first._task
        self.assertEqual(first.image_sequence, 1)
        self.assertIsNone(second.jpeg)
        self.assertEqual(len(self.writers), 1)

    async def test_channels_store_independent_photos_and_manual_capture_stays_existing_only(self):
        first, second = self.captures
        await self.photo(first, 11)
        old = (first.jpeg, first.updated, first.filename)
        await self.photo(second, 22)
        self.assertEqual((first.jpeg, first.updated, first.filename), old)
        self.assertNotEqual(first.filename, second.filename)
        self.assertIn('_channel_1/', first.filename)
        self.assertIn('_channel_2/', second.filename)
        self.assertEqual([value['channel'] for value in self.photo_events()], [1, 2])
        for capture in (first, second):
            self.assertEqual((Path(self.temp.name) / capture.filename).read_bytes(), self.jpeg)
        self.assertIsNone(self.hub.manual_snapshot.jpeg)
        self.assertIsNone(self.proxy.manual_snapshot.jpeg)
        with self.assertRaises(manual.HomeAssistantError):
            await self.hub.manual_snapshot.capture()
        self.assertEqual(self.hub.manual_snapshot.diagnostics['mode'], 'existing_live_only')
        self.assertEqual(len(self.writers), 2)

    async def test_repeated_cancellation_drains_own_release_and_preserves_viewer(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        await self.hub.acquire('viewer')
        entered, release = asyncio.Event(), asyncio.Event()
        actual_release = self.hub.release

        async def gated_release(owner, **kwargs):
            entered.set()
            await release.wait()
            await actual_release(owner, **kwargs)

        with patch.object(self.hub, 'release', gated_release):
            task = asyncio.create_task(capture._capture_image(timeout=1))
            await self.wait_for(lambda: bool(self.hub.live._image_waiters))
            task.cancel()
            await asyncio.wait_for(entered.wait(), 1)
            task.cancel()
            await asyncio.sleep(0)
            self.assertFalse(task.done())
            release.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(self.hub.consumers, {'viewer'})
        self.assertTrue(self.hub.connected)
        self.assertFalse(self.hub.live._image_waiters)
        self.assertEqual(self.commands(self.writers[0]), [0xa9, 1])

    async def test_close_and_media_unload_drain_without_file_or_event(self):
        capture = self.captures[0]
        await self.begin(capture)
        close = asyncio.create_task(capture.close())
        await asyncio.sleep(0)
        await self.hub.live.stop()
        await close
        self.assertIsNone(capture._task)
        self.assertIsNone(capture.jpeg)
        self.assertFalse(self.hub.consumers)
        self.assertFalse(self.hub.live._image_waiters)
        self.assertTrue(self.writers[0].closed)
        self.assertFalse(self.photo_events())
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    async def test_off_during_real_file_save_discards_unpublished_file(self):
        capture = self.captures[0]
        saved, finish = asyncio.Event(), asyncio.Event()
        original_save = capture.storage.save

        async def paused_save(*args, **kwargs):
            result = await original_save(*args, **kwargs)
            saved.set()
            await finish.wait()
            return result

        with patch.object(capture.storage, 'save', paused_save):
            await self.begin(capture)
            self.publish(self.hub)
            await asyncio.wait_for(saved.wait(), 1)
            self.assertEqual(len(list(Path(self.temp.name).rglob('*.jpg'))), 1)
            capture.set_enabled(False)
            finish.set()
            await capture._task
        self.assertIsNone(capture.jpeg)
        self.assertFalse(self.photo_events())
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))
        self.assertFalse(self.hub.consumers)

    async def test_replaced_session_never_publishes_image_from_another_session(self):
        capture = self.captures[0]
        capture.set_enabled(True)
        await self.hub.acquire('viewer')
        original = self.hub.live.session

        async def replaced():
            self.hub.live.session = SimpleNamespace(_close_task=None)
            return self.jpeg

        try:
            with patch.object(self.hub.live, 'capture_active_image', replaced):
                with self.assertRaises(RuntimeError):
                    await capture._capture_image(timeout=1)
            self.assertEqual(capture.diagnostics['last_error_reason'], 'session_changed')
            self.assertEqual(self.hub.consumers, {'viewer'})
            self.assertIsNone(capture.jpeg)
        finally:
            self.hub.live.session = original

    async def test_overall_deadline_and_invalid_jpeg_keep_last_saved_photo(self):
        capture = self.captures[0]
        await self.photo(capture)
        old = (capture.jpeg, capture.updated, capture.filename)
        await self.begin(capture, 2)
        self.clock.advance(17)
        self.publish(self.hub)
        await capture._task
        self.assertEqual(capture.status, 'failed')
        self.assertEqual((capture.jpeg, capture.updated, capture.filename), old)
        self.video_decoder.feed.return_value = ([SimpleNamespace(pts=1)], b'INVALID_JPEG')
        await self.begin(capture, 3)
        self.publish(self.hub)
        await capture._task
        self.assertEqual(capture.status, 'failed')
        self.assertEqual((capture.jpeg, capture.updated, capture.filename), old)
        self.assertEqual(len(self.photo_events()), 1)


if __name__ == '__main__':
    unittest.main()
