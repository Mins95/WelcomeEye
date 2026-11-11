"""Real QV framing, fresh JPEG storage and channel isolation on synthetic peers."""
import asyncio
from io import BytesIO
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import patch
from PIL import Image

import test_connect3_channel2 as fixture
from test_connect3_media_protocol import frame_bytes, media_response
from test_media_storage import jpeg_bytes


class SnapshotTests(unittest.IsolatedAsyncioTestCase):
    wait_for = fixture.Channel2Tests.wait_for

    async def asyncSetUp(self):
        await fixture.Channel2Tests.asyncSetUp(self)
        self.temp = tempfile.TemporaryDirectory()
        self.events = []
        self.hub.hass = self.proxy.hass = SimpleNamespace(
            config=SimpleNamespace(media_dirs={'local': self.temp.name}, time_zone='UTC'),
            async_add_executor_job=asyncio.to_thread,
            bus=SimpleNamespace(async_fire=lambda *args: self.events.append(args)))
        self.hub.entry.entry_id = 'synthetic-ha-entry'
        self.jpeg = jpeg_bytes()
        self.video_decoder.feed.return_value = ([SimpleNamespace(pts=1)], self.jpeg)
        self.video_now = 100.0
        clock = patch.object(fixture.live, 'time', SimpleNamespace(monotonic=lambda: self.video_now))
        clock.start()
        self.patchers.append(clock)

    async def asyncTearDown(self):
        try:
            await fixture.Channel2Tests.asyncTearDown(self)
        finally:
            self.temp.cleanup()

    async def snapshot(self, target):
        await target.acquire('viewer')
        before = list(self.writers[-1].writes)
        task = asyncio.create_task(target.manual_snapshot.capture())
        await self.wait_for(lambda: bool(target.live._image_waiters))
        self.assertFalse(task.done())
        self.video_now += 3  # A newly decoded image after startup, never the first cached JPEG.
        self.readers[-1].feed_data(b''.join(media_response(frame_bytes())))
        result = await task
        self.assertEqual(self.writers[-1].writes, before)
        self.assertEqual(target.consumers, {'viewer'})
        self.assertTrue(target.connected)
        self.assertEqual((Path(self.temp.name) / result['filename']).read_bytes(), self.jpeg)
        return result

    async def feed_image(self, jpeg, *, elapsed=0):
        self.video_now += elapsed
        target = self.hub if self.hub.connected else self.proxy
        before = target.live.observation['decoded_frames']
        self.video_decoder.feed.return_value = ([SimpleNamespace(pts=1)], jpeg)
        self.readers[-1].feed_data(b''.join(media_response(frame_bytes())))
        await self.wait_for(lambda: target.live.observation['decoded_frames'] > before)

    async def test_each_channel_waits_for_post_settling_frame_and_resets_after_reopen(self):
        stream = BytesIO()
        Image.new('RGB', (3, 2), (90, 90, 90)).save(stream, format='JPEG')
        early = stream.getvalue()
        for target in (self.hub, self.proxy):
            with self.subTest(channel=target.channel):
                self.video_decoder.feed.return_value = ([SimpleNamespace(pts=1)], early)
                await target.acquire('viewer')
                self.assertTrue(target.connected)  # Video is immediately usable.
                self.assertIsNone(target.image)  # HomeKit cache cannot expose the gray first frame.
                before = list(self.writers[-1].writes)
                task = asyncio.create_task(target.manual_snapshot.capture(save_to_media=False))
                await self.wait_for(lambda: bool(target.live._image_waiters))
                await self.feed_image(early, elapsed=2.9)
                self.assertFalse(task.done())
                self.assertIsNone(target.image)
                self.video_now += .2
                await asyncio.sleep(0)
                self.assertFalse(task.done())  # Time alone cannot reuse an early cached frame.
                await self.feed_image(self.jpeg)
                await task
                self.assertEqual(target.manual_snapshot.jpeg, self.jpeg)
                self.assertEqual(target.image, self.jpeg)
                self.assertEqual(self.writers[-1].writes, before)

                # A later request still requires a fresh frame, without another warmup.
                task = asyncio.create_task(target.manual_snapshot.capture(save_to_media=False))
                await self.wait_for(lambda: bool(target.live._image_waiters))
                await self.feed_image(self.jpeg, elapsed=.1)
                await task
                self.assertEqual(target.manual_snapshot.jpeg, self.jpeg)
                await target.release('viewer')
                self.video_now += 10
                await target.acquire('viewer')
                self.assertIsNone(target.image)  # A new session has its own startup window.
                await target.release('viewer')

    async def test_fresh_photos_use_each_own_session_and_private_files_without_network_writes(self):
        main = await self.snapshot(self.hub)
        await self.hub.release('viewer')
        second = await self.snapshot(self.proxy)
        self.assertEqual([main['channel'], second['channel']], [1, 2])
        self.assertNotEqual(main['filename'], second['filename'])
        self.assertIn('_channel_1/', main['filename'])
        self.assertIn('_channel_2/', second['filename'])
        self.assertEqual([event[1]['channel'] for event in self.events], [1, 2])
        self.assertEqual(self.hub.manual_snapshot.jpeg, self.jpeg)
        self.assertEqual(self.proxy.manual_snapshot.jpeg, self.jpeg)
        self.assertEqual(len(self.writers), 2)

    async def test_closed_channel_refuses_without_cgi_or_media_acquisition(self):
        for target in (self.hub, self.proxy):
            with self.assertRaises(Exception):
                await target.manual_snapshot.capture()
            self.assertEqual(target.manual_snapshot.diagnostics['last_error_reason'], 'active_video_required')
        self.assertFalse(self.writers)
        fixture.live.read_stream_material.assert_not_called()
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))

    async def test_other_channel_cannot_capture_active_image(self):
        await self.hub.acquire('viewer')
        with self.assertRaises(Exception):
            await self.proxy.manual_snapshot.capture()
        self.assertIsNone(self.proxy.manual_snapshot.jpeg)
        self.assertTrue(self.hub.connected)
        self.assertEqual(len(self.writers), 1)

    async def test_timeout_cancel_and_close_never_return_old_cache_or_start_another_session(self):
        await self.proxy.acquire('viewer')
        with patch.object(fixture.live, 'SNAPSHOT_TIMEOUT', .01):
            with self.assertRaises(Exception):
                await self.proxy.manual_snapshot.capture()
        self.assertEqual(self.proxy.manual_snapshot.diagnostics['last_error_reason'], 'fresh_image_timeout')
        self.assertFalse(self.proxy.live._image_waiters)
        task = asyncio.create_task(self.proxy.manual_snapshot.capture())
        await self.wait_for(lambda: bool(self.proxy.live._image_waiters))
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.proxy.live._image_waiters)
        task = asyncio.create_task(self.proxy.manual_snapshot.capture())
        await self.wait_for(lambda: bool(self.proxy.live._image_waiters))
        await self.proxy.release('viewer')
        with self.assertRaises(Exception):
            await task
        self.assertFalse(self.proxy.live._image_waiters)
        self.assertIsNone(self.proxy.manual_snapshot.jpeg)
        self.assertFalse(self.events)
        self.assertFalse(list(Path(self.temp.name).rglob('*.jpg')))
        self.assertEqual(len(self.writers), 1)
