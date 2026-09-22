"""Exercise the real coordinator and hub lease code, without physical devices."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_fresh_snapshot import Hub, ring_namespace as ns, namespace as hub_ns, until

Capture = ns['RingImageCapture']
NativeRingPhoto = ns['NativeRingPhoto']


class FakeClock:
    """Advance only the coordinator's clock/timers, not device timeout budgets."""

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
        self.hub = Hub(self.hass, SimpleNamespace(data={}, entry_id='fixture'))
        self.hub.stopped = False
        self.capture = self.hub.ring_image
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
        # JPEG boundary covered with real Pillow by the runtime validator.
        self.jpeg_patch = patch.dict(ns, validate_jpeg=lambda data: data, MEDIA_FALLBACK_ENABLED=True)
        self.jpeg_patch.start()

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

    async def test_native_available_no_media_immediate_ring(self):
        with patch.dict(ns, native_ring_photo=AsyncMock(return_value=NativeRingPhoto(1, b'native'))):
            self.ring()
            self.assertEqual([e[0] for e in self.events], ['welcomeeye_local.ring'])
            self.assertIsNone(self.capture.jpeg)
            await self.finish()
        self.assertEqual(self.starts, 0)
        self.assertEqual(self.capture.jpeg, b'native')
        self.assertEqual(self.capture.source, 'native')
        self.assertEqual(self.events[-1], ('welcomeeye_local.ring_image', {
            'entry_id': 'fixture', 'image_entity_id': 'image.fixture_last_ring',
            'ring_sequence': 1, 'source': 'native'}))

    async def test_unavailable_fallback_never_old_image(self):
        self.hub._image(b'old')
        self.ring()
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertEqual(self.capture.source, 'fresh_snapshot')
        self.assertEqual(self.starts, 1)
        self.assertFalse(self.hub.consumers)

    async def test_live_reuses_and_leaves_original_consumer(self):
        await self.hub.acquire('viewer')
        thread = self.hub.thread
        self.ring()
        await self.deadline()
        await until(lambda: len(self.hub.consumers) == 2)
        self.hub._image(b'after-ring')
        await self.finish()
        self.assertEqual(self.capture.jpeg, b'after-ring')
        self.assertIs(self.hub.thread, thread)
        self.assertEqual(self.hub.consumers, {'viewer'})
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
        self.assertIsNone(self.hub.thread)

    async def test_rapid_rings_discard_slow_older_result(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def native(hub, sequence, message):
            if sequence == 1:
                entered.set()
                await finish.wait()
            return NativeRingPhoto(sequence, str(sequence).encode())
        with patch.dict(ns, native_ring_photo=native):
            self.ring()
            await entered.wait()
            self.ring()
            self.ring()
            finish.set()
            await self.finish()
        self.assertEqual(self.capture.jpeg, b'3')
        self.assertEqual(self.capture.image_sequence, 3)
        self.assertEqual([p['ring_sequence'] for e, p in self.events if e.endswith('ring_image')], [3])
        self.assertEqual(self.capture.diagnostics['ring_capture_superseded'], 2)

    async def test_unload_during_acquisition_drains_no_callback(self):
        self.publish = False
        self.ring()
        await self.deadline()
        await until(lambda: bool(self.hub.consumers))
        notifications = Mock()
        self.hub.listeners.add(notifications)
        await self.hub.stop()
        after_stop = notifications.call_count
        await asyncio.sleep(.02)
        self.assertEqual(notifications.call_count, after_stop)
        self.assertIsNone(self.capture._task)
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(len(self.events), 1)

    async def test_bad_native_decode_falls_back(self):
        def validate(data):
            if data == b'bad':
                raise ValueError('secret must never appear')
            return data
        with patch.dict(ns, native_ring_photo=AsyncMock(return_value=NativeRingPhoto(1, b'bad')),
                        validate_jpeg=validate):
            self.ring()
            await self.finish()
        self.assertEqual(self.capture.jpeg, b'fresh')
        self.assertEqual(self.capture.diagnostics['native_error_type'], 'ValueError')
        self.assertNotIn('secret', str(self.capture.diagnostics))

    async def test_wrong_native_sequence_falls_back(self):
        with patch.dict(ns, native_ring_photo=AsyncMock(return_value=NativeRingPhoto(0, b'old'))):
            self.ring()
            await self.finish()
        self.assertEqual(self.capture.source, 'fresh_snapshot')

    async def test_native_timeout_leaves_budget_for_fallback(self):
        async def stalled(*args):
            await asyncio.Event().wait()
        with patch.dict(ns, native_ring_photo=stalled, NATIVE_TIMEOUT=.01):
            self.ring()
            await self.finish()
        self.assertEqual(self.capture.source, 'fresh_snapshot')
        self.assertEqual(self.capture.diagnostics['native_error_type'], 'TimeoutError')

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
        with patch.dict(hub_ns, Session=RefusedSession, exit_reason=lambda *args: 'connect_error'):
            self.ring()
            await self.finish()
        self.assertEqual(len(attempts), 1)
        self.assertEqual(self.capture.status, 'failed')
        self.assertIsNone(self.hub.thread)

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
            self.assertTrue(self.hub.media_retry_allowed)
            self.assertEqual(self.hub.consumers, {'viewer'})

    async def test_suspended_fallback_preserves_native_capture_no_media(self):
        with patch.dict(ns, MEDIA_FALLBACK_ENABLED=False):
            self.ring()
            await self.finish()
        self.assertEqual(self.starts, 0)
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual([e[0] for e in self.events], ['welcomeeye_local.ring'])

    async def test_no_media_before_four_seconds_exact_deadline(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(3.999)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(self.hub.snapshot_requests, 0)
        self.clock.advance(.001)
        await self.capture._task
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.hub.snapshot_requests, 1)
        self.assertEqual(self.capture.diagnostics['last_fallback_started_ms'], 4000)

    async def test_native_at_three_point_five_seconds_cancels_fallback(self):
        done = asyncio.Event()
        async def provider(*args):
            await done.wait()
            return NativeRingPhoto(1, b'native-late')
        with patch.dict(ns, native_ring_photo=provider):
            self.ring()
            await asyncio.sleep(0)
            self.clock.advance(3.5)
            done.set()
            await self.capture._task
        self.clock.advance(20)
        await asyncio.sleep(0)
        self.assertEqual(self.capture.jpeg, b'native-late')
        self.assertEqual(self.hub.snapshot_requests, 0)
        self.assertFalse(self.clock.pending)

    async def test_new_ring_replaces_timer_and_has_own_deadline(self):
        provider = AsyncMock(return_value=None)
        with patch.dict(ns, native_ring_photo=provider):
            self.ring()
            await until(lambda: bool(self.clock.pending))
            self.clock.advance(2)
            self.ring()
            await until(lambda: any(h.at == 6 for h in self.clock.pending))
            self.clock.advance(2)
            await asyncio.sleep(0)
            self.assertEqual(self.starts, 0)
            self.clock.advance(1.999)
            await asyncio.sleep(0)
            self.assertEqual(self.hub.snapshot_requests, 0)
            self.clock.advance(.001)
            await self.capture._task
        self.assertEqual(self.hub.snapshot_requests, 1)
        self.assertEqual(provider.await_count, 2)
        self.assertEqual(self.capture.image_sequence, 2)
        self.assertEqual([p['ring_sequence'] for e,p in self.events if e.endswith('ring_image')], [2])

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

    async def test_unload_before_deadline_cancels_timer_no_orphan(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        self.clock.advance(1)
        await self.capture.close()
        self.clock.advance(30)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertIsNone(self.capture._task)
        self.assertFalse(self.clock.pending)
        self.assertEqual(len(self.events), 1)

    async def test_shutdown_before_deadline_no_callback_or_acquisition(self):
        self.ring()
        await until(lambda: bool(self.clock.pending))
        await self.hub.stop()
        self.clock.advance(30)
        await asyncio.sleep(0)
        self.assertEqual(self.starts, 0)
        self.assertEqual(len(self.events), 1)
        self.assertIsNone(self.capture._task)

    async def test_expired_queued_ring_never_starts_media(self):
        self.ring()
        self.clock.advance(21)
        await self.capture._task
        self.assertEqual(self.capture.status, 'failed')
        self.assertEqual(self.hub.snapshot_requests, 0)


if __name__ == '__main__':
    unittest.main()
