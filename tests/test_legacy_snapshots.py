"""Secondary LT photos from synthetic worker callbacks, without network I/O."""
import asyncio
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import Mock, patch

from load_integration import load
from test_legacy_multichannel import Hub
from test_media_storage import jpeg_bytes

capture_module = load('legacy_snapshot')


class LegacySnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        entry = SimpleNamespace(entry_id='synthetic-entry', unique_id='synthetic-id', data={
            'host': '192.0.2.70', 'username': 'SYNTHETIC', 'password': 'SYNTHETIC',
            'device_variant': 'connect2_r001', 'channel': 16, 'second_channel_enabled': True})
        hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()),
            config=SimpleNamespace(media_dirs={'local': self.temp.name}, time_zone='UTC'),
            async_add_executor_job=asyncio.to_thread,
            bus=SimpleNamespace(async_fire=lambda *args: self.events.append(args)))
        self.hub = Hub(hass, entry)
        self.hub.stopped = False
        self.secondary = self.hub.channel2
        self.starts = 0
        self.jpeg = jpeg_bytes()

        def worker(generation, stop):
            self.starts += 1
            self.hub.session = SimpleNamespace(interrupt_read=Mock(), connection_stage='ready', closed=threading.Event())
            self.hub.current_profile = {'name': 'lt_second_panel', 'channel': 17, 'stream': 1, 'mode': 2}
            for callback, args in ((self.hub._state, (True,)), (self.hub._image, (self.jpeg,))):
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, callback, *args)
            stop.wait()
        self.hub._worker = worker

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertFalse(self.secondary.manual_snapshot._waiters)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)
        self.temp.cleanup()

    async def pending(self):
        task = asyncio.create_task(self.secondary.manual_snapshot.capture())
        async with asyncio.timeout(2):
            while not self.secondary.manual_snapshot._waiters:
                if task.done():
                    await task
                await asyncio.sleep(.001)
        return task

    async def test_next_jpeg_saved_only_from_active_second_worker(self):
        await self.secondary.acquire('viewer')
        task = await self.pending()
        self.assertFalse(task.done())
        worker = self.hub.thread
        self.hub._dispatch(self.hub.generation, self.hub._image, self.jpeg)
        result = await task
        self.assertEqual(result['channel'], 2)
        self.assertIn('_channel_2/', result['filename'])
        self.assertEqual((Path(self.temp.name) / result['filename']).read_bytes(), self.jpeg)
        self.assertEqual(self.events[0][1]['channel'], 2)
        self.assertIs(self.hub.thread, worker)
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.secondary.consumers, {'viewer'})

    async def test_closed_or_main_channel_never_opens_video(self):
        for active in (1, 2):
            self.hub._active_media_channel = active
            with self.assertRaises(Exception):
                await self.secondary.manual_snapshot.capture()
        self.assertEqual(self.starts, 0)
        self.assertFalse(self.events)

    async def test_timeout_cancel_and_session_close_never_return_cached_jpeg(self):
        await self.secondary.acquire('viewer')
        with patch.object(capture_module, 'SNAPSHOT_TIMEOUT', .01):
            with self.assertRaises(Exception):
                await self.secondary.manual_snapshot.capture()
        self.assertEqual(self.secondary.manual_snapshot.diagnostics['last_error_reason'], 'fresh_image_timeout')
        task = await self.pending()
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        task = await self.pending()
        await self.secondary.release('viewer')
        with self.assertRaises(Exception):
            await task
        self.assertIsNone(self.secondary.manual_snapshot.jpeg)
        self.assertFalse(self.events)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    async def test_changed_profile_or_replacement_session_cannot_supply_photo(self):
        await self.secondary.acquire('viewer')
        for change in ('endpoint', 'session', 'remote_close'):
            task = await self.pending()
            original_data, original_session = self.hub.entry.data, self.hub.session
            if change == 'endpoint':
                self.hub.entry.data = {**original_data, 'host': '192.0.2.71'}
            elif change == 'session':
                self.hub.session = SimpleNamespace(interrupt_read=Mock())
            else:
                self.hub.session.closed.set()
            self.hub._image(self.jpeg)
            with self.assertRaises(Exception):
                await task
            self.hub.entry.data, self.hub.session = original_data, original_session
            self.hub.session.closed.clear()
        self.assertFalse(self.events)
        self.assertIsNone(self.secondary.manual_snapshot.jpeg)

    async def test_old_worker_callback_does_not_publish_into_new_generation(self):
        await self.secondary.acquire('viewer')
        generation = self.hub.generation
        await self.secondary.release('viewer')
        await self.secondary.acquire('viewer')
        task = await self.pending()
        self.hub._dispatch(generation, self.hub._image, self.jpeg)
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        self.hub._dispatch(self.hub.generation, self.hub._image, self.jpeg)
        await task
        self.assertEqual(self.starts, 2)
