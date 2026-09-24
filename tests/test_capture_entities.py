"""Manual capture, persistent switch and memory ImageEntity semantics."""
import asyncio
from datetime import datetime, timezone
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from test_fresh_snapshot import Hub, manual_namespace as ns, ring_namespace, namespace as hub_ns, capture_module, until
from test_media_storage import jpeg_bytes


class EntityBoundary:
    def __init__(self, hub, key):
        self.hub = hub
        self.entity_id = f'image.fixture_{key}'
        self.async_write_ha_state = Mock()
    async def async_added_to_hass(self):
        pass
    async def async_will_remove_from_hass(self):
        pass


class ImageBoundary:
    def __init__(self, hass):
        self.hass = hass


switch_ns = capture_module('switch.py', {
    'WelcomeEyeEntity': EntityBoundary, 'SwitchEntity': type('SwitchEntity', (), {}),
    'OPTION_RING_IMAGE_CAPTURE': 'ring_image_capture',
})
image_ns = capture_module('image.py', {'WelcomeEyeEntity': EntityBoundary, 'ImageEntity': ImageBoundary})


class ManualSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        self.hass = SimpleNamespace(
            config=SimpleNamespace(media_dirs={'local': self.temp.name}, time_zone='Europe/Paris'),
            async_add_executor_job=asyncio.to_thread,
            bus=SimpleNamespace(async_fire=lambda *args: self.events.append(args)),
        )
        self.entry = SimpleNamespace(data={}, options={'keep_other_option': True}, entry_id='fixture')
        self.hub = Hub(self.hass, self.entry)
        self.hub.stopped = False
        self.capture = self.hub.manual_snapshot
        self.capture.entity_id = 'image.fixture_last_snapshot'
        self.jpeg = jpeg_bytes()
        self.publish = True
        self.starts = 0
        def device(generation, stop):
            self.starts += 1
            self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._state, True)
            if self.publish:
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._image, self.jpeg)
            stop.wait()
        self.hub._worker = device

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)
        self.temp.cleanup()

    async def test_idle_snapshot_saved_distinct_from_ring(self):
        self.hub._image(b'old')
        self.hub.ring_image.jpeg = b'visitor'
        result = await self.capture.capture()
        self.assertEqual(self.capture.jpeg, self.jpeg)
        self.assertEqual(self.hub.ring_image.jpeg, b'visitor')
        self.assertTrue(result['saved'])
        self.assertEqual((Path(self.temp.name) / result['filename']).read_bytes(), self.jpeg)
        self.assertEqual(self.starts, 1)
        self.assertFalse(self.hub.consumers)
        self.assertEqual(self.events[-1][0], 'welcomeeye_local.snapshot_saved')

    async def test_live_with_audio_and_microphone_reuses_media_and_requires_new_frame(self):
        for consumer in ('viewer', 'audio', 'microphone'):
            await self.hub.acquire(consumer)
        thread = self.hub.thread
        task = asyncio.create_task(self.capture.capture(save_to_media=False))
        await until(lambda: len(self.hub.consumers) == 4)
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        self.hub._image(self.jpeg)
        result = await task
        self.assertFalse(result['saved'])
        self.assertIsNone(result['save_error'])
        self.assertEqual(self.hub.consumers, {'viewer', 'audio', 'microphone'})
        self.assertIs(thread, self.hub.thread)
        self.assertEqual(self.starts, 1)
        self.assertEqual(list(Path(self.temp.name).rglob('*.jpg')), [])

    async def test_disk_error_does_not_lose_capture(self):
        self.capture.storage.save = AsyncMock(side_effect=PermissionError('secret filesystem password'))
        result = await self.capture.capture()
        self.assertFalse(result['saved'])
        self.assertEqual(result['save_error'], 'PermissionError')
        self.assertIsNotNone(self.capture.updated)
        self.assertEqual(self.capture.jpeg, self.jpeg)
        self.assertEqual(self.capture.diagnostics['save_failures'], 1)
        self.assertNotIn('secret', str(result) + str(self.capture.diagnostics))
        self.assertEqual(self.events, [])

    async def test_failed_capture_keeps_last_image_and_timestamp(self):
        await self.capture.capture(save_to_media=False)
        updated = self.capture.updated
        with patch.dict(ns, capture_fresh_image=AsyncMock(return_value=None)):
            with self.assertRaises(ns['HomeAssistantError']):
                await self.capture.capture(save_to_media=False)
        self.assertEqual(self.capture.updated, updated)
        self.assertEqual(self.capture.jpeg, self.jpeg)
        self.assertEqual(self.capture.diagnostics['failures'], 1)

    async def test_parallel_manual_request_rejected_without_queue(self):
        self.publish = False
        task = asyncio.create_task(self.capture.capture(save_to_media=False))
        await until(lambda: bool(self.hub.consumers))
        with self.assertRaises(ns['HomeAssistantError']):
            await self.capture.capture(save_to_media=False)
        self.hub._image(self.jpeg)
        await task
        self.assertEqual(self.capture.diagnostics['requests'], 1)
        self.assertEqual(self.starts, 1)

    async def test_cancelled_manual_task_does_not_abort_later_hub_stop(self):
        self.publish = False
        task = asyncio.create_task(self.capture.capture())
        await until(lambda: bool(self.hub.consumers))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await self.hub.stop()
        self.assertIsNone(self.capture._task)
        self.assertIsNone(self.hub.thread)

    async def test_unload_suppresses_pending_capture_and_saves(self):
        self.publish = False
        task = asyncio.create_task(self.capture.capture())
        await until(lambda: self.hub.snapshot_started_media == 1)
        await self.hub.stop()
        with self.assertRaises(ns['HomeAssistantError']):
            await task
        self.assertIsNone(self.capture.jpeg)
        self.assertEqual(self.events, [])
        self.assertEqual(list(Path(self.temp.name).rglob('*.jpg')), [])

    async def test_switch_persists_on_off_and_reload_preserves_unrelated_options(self):
        def update(entry, *, options):
            entry.options = options
        self.hass.config_entries = SimpleNamespace(async_update_entry=Mock(side_effect=update))
        switch = switch_ns['WelcomeEyeRingCaptureSwitch'](self.hub)
        switch.hass = self.hass
        self.assertFalse(switch.is_on)
        await switch.async_added_to_hass()
        await switch.async_turn_on()
        self.assertTrue(switch.is_on)
        self.assertEqual(self.entry.options, {'keep_other_option': True, 'ring_image_capture': True})
        restored = ring_namespace['RingImageCapture'](self.hub)
        self.assertTrue(restored.enabled)
        await restored.close()
        await switch.async_will_remove_from_hass()
        self.assertIsNone(self.hub.ring_image_capture_entity_id)
        await switch.async_added_to_hass()
        self.assertTrue(switch.is_on)
        await switch.async_turn_off()
        restored = ring_namespace['RingImageCapture'](self.hub)
        self.assertFalse(restored.enabled)
        await restored.close()

    async def test_v1_switch_unavailable_and_persisted_on_ignored(self):
        self.entry.data['detected_model'] = self.hub.device_model = 'WelcomeEye Connect V1'
        self.entry.options['ring_image_capture'] = True
        restored = ring_namespace['RingImageCapture'](self.hub)
        self.assertFalse(restored.enabled)
        restored.set_enabled(True)
        self.assertFalse(restored.enabled)
        switch = switch_ns['WelcomeEyeRingCaptureSwitch'](self.hub)
        self.assertFalse(switch.available)
        await restored.close()

    async def test_observed_v1_cancels_ring_work_preserving_live_media(self):
        self.entry.title = 'WelcomeEye'
        self.entry.unique_id = 'fixture-private-id'
        def update(entry, **changes):
            for key, value in changes.items():
                setattr(entry, key, value)
        self.hass.config_entries = SimpleNamespace(async_update_entry=Mock(side_effect=update))
        self.hub.ring_listener.close = Mock()
        registry = SimpleNamespace(async_get_device_by_identifier=Mock(return_value=None))
        await self.hub.acquire('viewer')
        thread = self.hub.thread
        self.hub.ring_image.set_enabled(True)
        self.hub._ring(SimpleNamespace(channel=16))
        timer = self.hub.ring_timer
        with patch.dict(hub_ns, DOMAIN='welcomeeye_local', dr=SimpleNamespace(async_get=lambda hass: registry)):
            self.hub._observe_device_model(SimpleNamespace(width=352, height=288), 97)
        await self.hub.ring_image._task
        self.assertFalse(self.hub.local_ring_supported)
        self.assertFalse(self.hub.ring_image.enabled)
        self.assertFalse(self.hub.ringing)
        self.assertFalse(self.hub.ring_connected)
        self.assertTrue(timer.cancelled())
        self.hub.ring_listener.close.assert_called_once()
        self.assertEqual(self.entry.data['detected_model'], 'WelcomeEye Connect V1')
        self.assertEqual(self.hub.consumers, {'viewer'})
        self.assertIs(self.hub.thread, thread)
        self.assertEqual(self.hub.snapshot_requests, 0)

    async def test_image_entities_read_only_memory_distinct_timestamps(self):
        ring = image_ns['WelcomeEyeRingImage'](self.hass, self.hub)
        manual = image_ns['WelcomeEyeSnapshotImage'](self.hass, self.hub)
        await ring.async_added_to_hass()
        await manual.async_added_to_hass()
        self.assertIsNone(ring.image_last_updated)
        self.assertIsNone(await ring.async_image())
        await self.capture.capture(save_to_media=False)
        self.assertIsNone(ring.image_last_updated)
        stamp = manual.image_last_updated
        executor = self.hass.async_add_executor_job
        self.hass.async_add_executor_job = Mock(side_effect=AssertionError('Property/image reads must not perform IO'))
        try:
            self.assertEqual(await manual.async_image(), self.jpeg)
            self.assertEqual(manual.image_last_updated, stamp)
            self.assertEqual(manual.extra_state_attributes['capture_status'], 'ready')
            self.assertEqual(manual._attr_content_type, 'image/jpeg')
        finally:
            self.hass.async_add_executor_job = executor
        await manual.async_will_remove_from_hass()
        self.assertFalse(self.capture._closed)
        self.assertIsNone(self.capture.entity_id)
        self.assertEqual(await manual.async_image(), self.jpeg)
