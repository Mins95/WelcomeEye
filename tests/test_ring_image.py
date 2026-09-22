"""Exercise the real coordinator and hub lease code, without physical devices."""
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_fresh_snapshot import Hub, ring_namespace as ns, until

Capture = ns['RingImageCapture']
NativeRingPhoto = ns['NativeRingPhoto']


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
        self.jpeg_patch = patch.dict(ns, validate_jpeg=lambda data: data)
        self.jpeg_patch.start()

    async def asyncTearDown(self):
        await self.hub.stop()
        self.jpeg_patch.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    def ring(self):
        self.hub._ring(SimpleNamespace(channel=16))

    async def finish(self):
        await self.capture._task

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
        with patch.dict(ns, CAPTURE_TIMEOUT=.05):
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


if __name__ == '__main__':
    unittest.main()
