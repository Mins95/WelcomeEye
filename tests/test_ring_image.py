"""Ring capture timing and cancellation using real hub shared media leases."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_fresh_snapshot import Hub, ring_namespace as ns, namespace as hub_ns, until
from load_integration import cap

Capture = ns['RingImageCapture']


class FakeClock:
    def __init__(self):
        self.now = 0.0
        self.handles = []

    def call_later(self, delay, callback):
        handle = SimpleNamespace(at=self.now + delay, callback=callback, cancelled=False)
        handle.cancel = lambda: setattr(handle, 'cancelled', True)
        self.handles.append(handle)
        return handle

    def advance(self, seconds):
        self.now += seconds
        for handle in self.handles:
            if not handle.cancelled and handle.at <= self.now:
                handle.cancel()
                handle.callback()

    @property
    def pending(self):
        return [h for h in self.handles if not h.cancelled]


class RingImageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.events = []
        async def executor(fn, *args):
            return fn(*args)
        self.hass = SimpleNamespace(
            bus=SimpleNamespace(async_fire=lambda name, payload: self.events.append((name, payload))),
            async_add_executor_job=executor,
        )
        self.hub = Hub(self.hass, SimpleNamespace(data={'detected_model': 'WelcomeEye Connect 2'}, options={}, entry_id='fixture'))
        self.hub.stopped = False
        self.capture = self.hub.ring_image
        self.capture.set_enabled(True)
        self.capture.entity_id = 'image.fixture_last_ring'
        self.clock = FakeClock()
        self.capture._clock = lambda: self.clock.now
        self.capture._call_later = self.clock.call_later
        self.starts = 0
        self.publish = True
        def device(generation, stop):
            self.starts += 1
            self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._state, True)
            if self.publish:
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._image, b'fresh')
            stop.wait()
        self.hub._worker = device
        self.jpeg_patch = patch.dict(ns, validate_jpeg=lambda data: data)
        self.jpeg_patch.start()
        self.capture.storage.save = AsyncMock(return_value={
            'filename': 'WelcomeEye/fixture/2026-09-24/image.jpg',
            'media_content_id': 'media-source://media_source/local/WelcomeEye/fixture/2026-09-24/image.jpg',
        })
        self.capture.storage.discard = AsyncMock()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.jpeg_patch.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)
        self.assertIsNone(self.capture._task)
        self.assertFalse(self.clock.pending)

    def ring(self):
        self.hub._ring(SimpleNamespace(channel=16))

    async def finish(self):
        await until(lambda: self.capture._task.done() or bool(self.clock.pending))
        if self.clock.pending:
            self.clock.advance(min(h.at for h in self.clock.pending) - self.clock.now)
        await self.capture._task

    async def deadline(self):
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(min(h.at for h in self.clock.pending) - self.clock.now)

    async def test_explicit_off_keeps_ring_detection_no_photo_task_after_reload(self):
        self.hub.entry.options['ring_image_capture'] = False
        self.capture.set_enabled(False)
        self.ring()
        self.assertTrue(self.hub.ringing)
        self.assertEqual([e[0] for e in self.events], ['welcomeeye_local.ring'])
        self.assertIsNone(self.capture._task)
        self.assertEqual(self.hub.snapshot_requests, 0)
        self.assertEqual(self.capture.diagnostics['ring_capture_requests'], 0)
        self.capture.storage.save.assert_not_awaited()
        other = Capture(self.hub)
        self.assertFalse(other.enabled)
        await other.close()

    async def test_absent_option_defaults_on_without_changing_entry_options(self):
        self.assertEqual(self.hub.entry.options, {})
        self.assertTrue(self.capture.enabled)
        self.assertTrue(self.capture.diagnostics['enabled'])
        self.ring()
        self.assertEqual([e[0] for e in self.events], ['welcomeeye_local.ring'])
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(3.999)
        await asyncio.sleep(0)
        self.assertEqual(self.hub.snapshot_requests, 0)
        self.clock.advance(.001)
        await self.capture._task
        self.assertEqual(self.hub.snapshot_requests, 1)
        self.assertEqual(self.capture.diagnostics['last_fallback_started_ms'], 4000)
        self.assertEqual(self.hub.entry.options, {})

    async def test_one_fresh_acquisition_saved_event(self):
        self.hub._image(b'old')
        self.ring()
        self.assertEqual([e[0] for e in self.events], ['welcomeeye_local.ring'])
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertEqual(self.capture.source, 'fresh_snapshot')
        self.assertEqual(self.starts, 1)
        self.assertFalse(self.hub.consumers)
        self.capture.storage.save.assert_awaited_once()
        self.assertEqual(self.events[-1][1]['media_content_id'], self.capture.media_content_id)
        self.assertEqual(self.capture.diagnostics['save_successes'], 1)

    async def test_unsupported_secondary_ring_supersedes_pending_main_without_media(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.capture.reject_unsupported_channel(2)
        await self.capture._task
        self.clock.advance(20)
        self.assertEqual(self.capture.sequence, 2)
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual(self.capture.diagnostics['last_error_reason'], 'unsupported_ring_channel')
        self.assertEqual(self.starts, 0)
        self.assertIsNone(self.capture.updated)
        self.assertIsNone(self.capture.jpeg)
        self.assertFalse(self.clock.pending)
        self.assertFalse(any(name.endswith('ring_image') for name, _ in self.events))

    async def test_unsupported_secondary_ring_discards_inflight_result_and_keeps_old_timestamp(self):
        self.ring()
        await self.finish()
        previous = (self.capture.jpeg, self.capture.updated, self.capture.image_sequence)
        entered, released = asyncio.Event(), asyncio.Event()
        async def pending(*args, **kwargs):
            entered.set()
            await released.wait()
            return b'wrong_source'
        with patch.dict(ns, capture_fresh_image=pending):
            self.ring()
            await self.deadline()
            await entered.wait()
            self.capture.reject_unsupported_channel(3)
            released.set()
            await self.capture._task
        self.assertEqual((self.capture.jpeg, self.capture.updated, self.capture.image_sequence), previous)
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual(self.capture.diagnostics['last_error_reason'], 'unsupported_ring_channel')
        self.assertEqual(len([name for name, _ in self.events if name.endswith('ring_image')]), 1)
        self.ring()
        await self.finish()
        self.assertEqual(self.capture.status, 'ready')
        self.assertIsNone(self.capture.diagnostics['last_error_reason'])

    async def test_live_and_microphone_remain(self):
        await self.hub.acquire('viewer')
        await self.hub.acquire('microphone')
        thread = self.hub.thread
        self.ring()
        await self.deadline()
        await until(lambda: len(self.hub.consumers) == 3)
        self.hub._image(b'after-ring')
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'after-ring')
        self.assertIs(self.hub.thread, thread)
        self.assertEqual(self.hub.consumers, {'viewer', 'microphone'})
        self.assertEqual(self.starts, 1)

    async def test_timeout_preserves_image_timestamp_and_releases(self):
        self.ring()
        await self.finish()
        timestamp = self.capture.updated
        self.publish = False
        with patch.dict(ns, CAPTURE_TIMEOUT=4.05):
            self.ring()
            await self.finish()
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual(self.capture.updated, timestamp)
        self.assertEqual(self.capture.image_sequence, 1)
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertEqual(len([e for e in self.events if e[0].endswith('ring_image')]), 1)
        self.assertFalse(self.hub.consumers)

    async def test_disable_timer_then_enable_does_not_replay_ring(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(1)
        self.capture.set_enabled(False)
        self.assertFalse(self.clock.pending)
        await self.capture._task
        self.capture.set_enabled(True)
        self.clock.advance(20)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertEqual(len(self.events), 1)

    async def test_disable_in_flight_drops_late_success_even_after_enable(self):
        entered, settle = asyncio.Event(), asyncio.Event()
        async def snapshot(*args, **kwargs):
            entered.set()
            await settle.wait()
            return b'late'
        with patch.dict(ns, capture_fresh_image=snapshot):
            self.ring()
            await self.deadline()
            await entered.wait()
            self.capture.set_enabled(False)
            self.capture.set_enabled(True)
            settle.set()
            await self.capture._task
        self.assertIsNone(self.capture.jpeg)
        self.capture.storage.save.assert_not_awaited()
        self.assertEqual(len(self.events), 1)

    async def test_disable_during_save_discards_unpublished_file(self):
        entered, settle = asyncio.Event(), asyncio.Event()
        result = {'filename': 'private.jpg', 'media_content_id': 'private'}
        async def save(*args, **kwargs):
            entered.set()
            await settle.wait()
            self.assertTrue(kwargs['cancelled'].is_set())
            return result
        self.capture.storage.save.side_effect = save
        self.ring()
        await self.deadline()
        await entered.wait()
        self.capture.set_enabled(False)
        settle.set()
        await self.capture._task
        self.capture.storage.discard.assert_awaited_once_with(result)
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(len(self.events), 1)

    async def test_disk_failure_preserves_memory_and_event(self):
        self.capture.storage.save.side_effect = PermissionError('secret-path-password')
        self.ring()
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertIsNotNone(self.capture.updated)
        self.assertEqual(self.capture.status, 'ready')
        self.assertIsNone(self.capture.media_content_id)
        self.assertEqual(self.capture.diagnostics['last_save_error_type'], 'PermissionError')
        self.assertNotIn('secret', str(self.capture.diagnostics))
        self.assertEqual(self.events[-1][0], 'welcomeeye_local.ring_image')

    async def test_failed_stale_file_cleanup_does_not_lose_newer_ring(self):
        entered, settle = asyncio.Event(), asyncio.Event()
        calls = 0
        result = {'filename': 'fixture.jpg', 'media_content_id': 'fixture'}
        async def save(*args, **kwargs):
            nonlocal calls
            calls += 1
            if calls == 1:
                entered.set()
                await settle.wait()
            return result
        self.capture.storage.save.side_effect = save
        self.capture.storage.discard.side_effect = PermissionError('private path')
        self.ring()
        await self.deadline()
        await entered.wait()
        self.ring()
        self.clock.advance(4)
        settle.set()
        await self.capture._task
        self.assertEqual(self.capture.image_sequence, 2)
        self.assertEqual(self.capture.diagnostics['save_failures'], 1)
        self.assertEqual([p['ring_sequence'] for e,p in self.events if e.endswith('ring_image')], [2])
        self.assertNotIn('private path', str(self.capture.diagnostics))

    async def test_cancelled_ring_task_does_not_abort_hub_stop(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.capture._task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await self.capture._task
        await self.hub.stop()
        self.assertIsNone(self.capture._task)

    async def test_invalid_jpeg_never_saved(self):
        with patch.dict(ns, validate_jpeg=Mock(side_effect=ValueError('payload'))):
            self.ring()
            await self.finish()
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(self.capture.status, 'failed')
        self.capture.storage.save.assert_not_awaited()

    async def test_unload_during_acquisition_drains_no_callback(self):
        self.publish = False
        self.ring()
        await self.deadline()
        await until(lambda: bool(self.hub.consumers))
        notifications = Mock()
        self.hub.listeners.add(notifications)
        await self.hub.stop()
        count = notifications.call_count
        await asyncio.sleep(.02)
        self.assertEqual(notifications.call_count, count)
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(len(self.events), 1)

    async def test_ring_only_worker_stops_after_first_failed_session(self):
        attempts = []
        class RefusedSession:
            connection_stage = 'failed_tcp_connect'
            def __init__(self, *args, **kwargs):
                attempts.append(1)
            def connect(self):
                raise ConnectionRefusedError
            def connection_diagnostics(self):
                return {}
            def interrupt_read(self):
                pass
        self.hub.entry.data.update(host='fixture', username='fixture', password='fixture')
        self.hub._worker = Hub._worker.__get__(self.hub)
        self.hub._profile_order = lambda: [('connect2', 16, 1, 2), ('unused', 1, 1, 2)]
        self.hub._finish_session = lambda *args: None
        self.hub.control.v1_media = SimpleNamespace(pending_snapshot=lambda *args: {})
        with patch.dict(hub_ns, Session=RefusedSession, exit_reason=lambda *args: 'connect_error',
                        V1VideoDiagnostics=Mock):
            self.ring()
            await self.finish()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.capture.status, 'failed')

    async def test_ring_lease_does_not_disable_viewer_retry_policy(self):
        self.publish = False
        with patch.dict(ns, CAPTURE_TIMEOUT=4.05):
            self.ring()
            await self.deadline()
            await until(lambda: bool(self.hub.consumers))
            self.assertFalse(self.hub.media_retry_allowed)
            await self.hub.acquire('viewer')
            self.assertTrue(self.hub.media_retry_allowed)
            await self.finish()
            self.assertEqual(self.hub.consumers, {'viewer'})

    async def test_no_media_before_four_seconds_exact_deadline(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(3.999)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertFalse(self.hub.consumers)
        self.clock.advance(.001)
        await self.capture._task
        self.assertEqual(self.hub.snapshot_requests, 1)
        self.assertEqual(self.capture.diagnostics['last_fallback_started_ms'], 4000)

    async def test_new_ring_replaces_timer_and_has_own_deadline(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(2)
        self.ring()
        self.ring()
        await until(lambda: any(h.at == 6 for h in self.clock.pending))
        self.clock.advance(3.999)
        await asyncio.sleep(0)
        self.assertEqual(self.hub.snapshot_requests, 0)
        self.clock.advance(.001)
        await self.capture._task
        self.assertEqual(self.hub.snapshot_requests, 1)
        self.assertEqual(self.capture.image_sequence, 3)
        self.assertEqual([p['ring_sequence'] for e,p in self.events if e.endswith('ring_image')], [3])

    async def test_stale_fallback_drains_before_latest_capture(self):
        entered, settle = asyncio.Event(), asyncio.Event()
        calls = []
        async def snapshot(*args, **kwargs):
            calls.append(self.clock.now)
            if len(calls) == 1:
                entered.set()
                await settle.wait()
                return b'old-ring'
            return b'new-ring'
        with patch.dict(ns, capture_fresh_image=snapshot):
            self.ring()
            await self.deadline()
            await entered.wait()
            self.clock.advance(1)
            self.ring()
            self.clock.advance(4)
            settle.set()
            await self.capture._task
        self.assertEqual(calls, [4, 9])
        self.assertEqual(self.capture.jpeg, b'new-ring')
        self.assertEqual([p['ring_sequence'] for e,p in self.events if e.endswith('ring_image')], [2])
        self.capture.storage.save.assert_awaited_once()

    async def test_unload_before_deadline_cancels_timer_no_orphan(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        await self.capture.close()
        self.clock.advance(30)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertFalse(self.clock.pending)
        self.assertEqual(len(self.events), 1)

    async def test_shutdown_before_deadline_no_callback_or_acquisition(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        await self.hub.stop()
        self.clock.advance(30)
        self.assertEqual(self.starts, 0)
        self.assertEqual(len(self.events), 1)

    async def test_expired_queued_ring_never_starts_media(self):
        self.ring()
        self.clock.advance(21)
        await self.capture._task
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual(self.hub.snapshot_requests, 0)


class V1CloudRingImageTests(RingImageTests):
    """Run the same timing, lease, failure and shutdown contract for cloud V1."""

    async def asyncSetUp(self):
        await super().asyncSetUp()
        self.hub.entry.data.update(device_variant=cap.DeviceVariant.V1,
                                   v1_cloud_doorbell_enabled=True)
        self.hub.entry.unique_id = self.hub._v1_cloud_uid = 'fixture-v1'
        self.hub.device_model = 'WelcomeEye Connect V1'
        self.assertFalse(self.hub.local_ring_supported)
        self.assertTrue(self.hub.capabilities.ring_image_capture)
        self.capture.set_enabled(True)

    def ring(self):
        self.hub._cloud_ring(1)

    async def test_disabling_cloud_during_timer_prevents_media(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.hub.entry.data['v1_cloud_doorbell_enabled'] = False
        await self.deadline()
        await self.capture._task
        self.assertEqual(self.starts, 0)
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(len(self.events), 1)

    async def test_local_observation_never_schedules_v1_capture(self):
        self.hub._ring(SimpleNamespace(channel=16))
        self.assertEqual(self.events, [])
        self.assertIsNone(self.capture._task)
        self.assertEqual(self.hub.snapshot_requests, 0)

    async def test_v1_format_observation_keeps_cloud_ring_and_scheduled_capture(self):
        self.hub.entry.title = 'WelcomeEye Connect V1'
        def update(entry, **changes):
            for key, value in changes.items():
                setattr(entry, key, value)
        self.hass.config_entries = SimpleNamespace(async_update_entry=update)
        registry = SimpleNamespace(async_get_device_by_identifier=Mock(return_value=None))
        self.hub.ring_listener.close = Mock()
        self.ring()
        ring_timer = self.hub.ring_timer
        with patch.dict(hub_ns, DOMAIN='welcomeeye_local', dr=SimpleNamespace(async_get=lambda hass: registry)):
            self.hub._observe_device_model(SimpleNamespace(width=352, height=288), 100)
        self.hub.ring_listener.close.assert_called_once()
        self.assertTrue(self.hub.ringing)
        self.assertIs(self.hub.ring_timer, ring_timer)
        self.assertTrue(self.capture.enabled)
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertEqual(self.capture.diagnostics['ring_capture_successes'], 1)


class RingCaptureDefaultTests(unittest.IsolatedAsyncioTestCase):
    async def test_missing_option_and_saved_boolean_preferences(self):
        for options, expected in (({}, True), ({'ring_image_capture': True}, True),
                                  ({'ring_image_capture': False}, False),
                                  ({'ring_image_capture': None}, False),
                                  ({'ring_image_capture': 1}, False)):
            with self.subTest(options=options):
                hub = SimpleNamespace(entry=SimpleNamespace(options=options),
                    loop=asyncio.get_running_loop(),
                    capabilities=cap.MATRIX[cap.DeviceVariant.R001])
                capture = Capture(hub)
                self.assertIs(capture.enabled, expected)
                self.assertEqual(capture.status, 'idle' if expected else 'disabled')
                self.assertEqual(capture.diagnostics['enabled'], expected)
                self.assertIsNone(capture._task)
                await capture.close()

    async def test_only_r001_can_default_on_or_restore_saved_on(self):
        for variant in cap.DeviceVariant:
            for options in ({}, {'ring_image_capture': True}):
                with self.subTest(variant=variant, options=options):
                    capabilities = cap.MATRIX[variant]
                    hub = SimpleNamespace(entry=SimpleNamespace(options=options),
                        loop=asyncio.get_running_loop(), capabilities=capabilities,
                        local_ring_supported=capabilities.local_ring)
                    capture = Capture(hub)
                    self.assertIs(capture.enabled, variant == cap.DeviceVariant.R001)
                    if variant != cap.DeviceVariant.R001:
                        capture.set_enabled(True)
                        capture.request(1, SimpleNamespace())
                        self.assertFalse(capture.enabled)
                        self.assertIsNone(capture._task)
                        self.assertEqual(capture.diagnostics['ring_capture_requests'], 0)
                    await capture.close()


if __name__ == '__main__':
    unittest.main()
