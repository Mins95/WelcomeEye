"""Real JPEG, filesystem and executor behavior without a device or HA server."""
import asyncio
from datetime import datetime, timezone
from io import BytesIO
import os
from pathlib import Path
import tempfile
import threading
from types import SimpleNamespace
import unittest
from unittest.mock import patch

from PIL import Image

from test_fresh_snapshot import storage_namespace as ns, until

Storage = ns['CaptureMediaStorage']


def jpeg_bytes():
    stream = BytesIO()
    Image.new('RGB', (3, 2), (23, 64, 97)).save(stream, format='JPEG')
    return stream.getvalue()


class MediaStorageTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name) / 'media-not-yet-created'
        self.config = SimpleNamespace(media_dirs={'local': str(self.root)}, time_zone='Europe/Paris')
        self.hub = SimpleNamespace(
            entry=SimpleNamespace(entry_id='entry-local-opaque', unique_id='UID-secret',
                                  title='192.168.2.4-secret-admin', data={'host': '192.168.2.4'}),
            hass=SimpleNamespace(config=self.config, async_add_executor_job=asyncio.to_thread),
        )
        self.storage = Storage(self.hub)
        self.jpeg = jpeg_bytes()
        self.stamp = datetime(2026, 9, 24, 22, 42, 31, tzinfo=timezone.utc)

    async def asyncTearDown(self):
        self.temp.cleanup()

    async def test_creates_directory_ha_timezone_uri_and_private_names(self):
        result = await self.storage.save(self.jpeg, self.stamp, 'ring', sequence=42)
        self.assertTrue(result['filename'].startswith('WelcomeEye/welcomeeye_'))
        self.assertTrue(result['filename'].endswith('/2026-09-25/2026-09-25_00-42-31_ring_42.jpg'))
        self.assertEqual((self.root / result['filename']).read_bytes(), self.jpeg)
        self.assertEqual(result['media_content_id'], 'media-source://media_source/local/' + result['filename'])
        for private in ('UID-secret', '192.168.2.4', 'secret-admin', str(self.root), 'entry-local-opaque'):
            self.assertNotIn(private, str(result))
        self.assertEqual(list(self.root.rglob('*.tmp')), [])

    async def test_custom_media_root_uses_matching_source_id(self):
        self.config.media_dirs = {'private_pictures': str(self.root)}
        result = await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertTrue(result['media_content_id'].startswith('media-source://media_source/private_pictures/'))
        self.assertTrue(result['filename'].endswith('_manual.jpg'))

    async def test_local_preferred_over_other_configured_root(self):
        self.config.media_dirs = {'archive': str(self.root.parent / 'unused'), 'local': str(self.root)}
        result = await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertTrue(result['media_content_id'].startswith('media-source://media_source/local/'))
        self.assertFalse((self.root.parent / 'unused').exists())

    async def test_no_media_config_reports_failure(self):
        self.config.media_dirs = {}
        with self.assertRaises(FileNotFoundError):
            await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertFalse(self.root.exists())

    async def test_collision_never_overwrites_including_parallel_saves(self):
        results = await asyncio.gather(*[
            self.storage.save(self.jpeg, self.stamp, 'manual') for _ in range(5)
        ])
        filenames = [item['filename'] for item in results]
        self.assertEqual(len(set(filenames)), 5)
        self.assertEqual(len(list(self.root.rglob('*.jpg'))), 5)
        for filename in filenames:
            self.assertEqual((self.root / filename).read_bytes(), self.jpeg)

    async def test_publishes_complete_file_atomically_off_event_loop(self):
        link = os.link
        main_thread = threading.get_ident()
        observations = []
        def observed_link(source, destination):
            observations.append(threading.get_ident())
            self.assertFalse(destination.exists())
            self.assertEqual(Path(source).read_bytes(), self.jpeg)
            link(source, destination)
            self.assertEqual(Path(destination).read_bytes(), self.jpeg)
        with patch.object(os, 'link', side_effect=observed_link):
            await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertEqual(len(observations), 1)
        self.assertNotEqual(observations[0], main_thread)

    async def test_write_or_link_failure_leaves_no_final_or_temp_file(self):
        for method in ('fsync', 'link'):
            with self.subTest(method=method), patch.object(os, method, side_effect=OSError('private path')):
                with self.assertRaises(OSError):
                    await self.storage.save(self.jpeg, self.stamp, 'manual')
            self.assertEqual(list(self.root.rglob('*.jpg')), [])
            self.assertEqual(list(self.root.rglob('*.tmp')), [])

    async def test_cancel_before_save_never_creates_folder(self):
        cancelled = threading.Event()
        cancelled.set()
        self.assertIsNone(await self.storage.save(self.jpeg, self.stamp, 'manual', cancelled=cancelled))
        self.assertFalse(self.root.exists())

    async def test_cancel_during_executor_drains_and_removes_file(self):
        entered, settle = threading.Event(), threading.Event()
        link = os.link
        def slow_link(source, destination):
            entered.set()
            settle.wait(2)
            link(source, destination)
        with patch.object(os, 'link', side_effect=slow_link):
            task = asyncio.create_task(self.storage.save(self.jpeg, self.stamp, 'manual'))
            await until(entered.is_set)
            task.cancel()
            await asyncio.sleep(.01)
            task.cancel()
            self.assertFalse(task.done())
            settle.set()
            with self.assertRaises(asyncio.CancelledError):
                await task
        self.assertEqual(list(self.root.rglob('*.jpg')), [])
        self.assertEqual(list(self.root.rglob('*.tmp')), [])

    async def test_cancel_after_executor_completed_removes_unpublished_result(self):
        # Simulate the executor winning its last token check before the caller
        # receives cancellation, so the coroutine must discard its result.
        ready, deliver = asyncio.Event(), asyncio.Event()
        real_save = self.storage._save
        async def completed_executor(*args):
            result = await real_save(*args)
            ready.set()
            await deliver.wait()
            return result
        self.storage._save = completed_executor
        task = asyncio.create_task(self.storage.save(self.jpeg, self.stamp, 'manual'))
        await ready.wait()
        self.assertEqual(len(list(self.root.rglob('*.jpg'))), 1)
        task.cancel()
        deliver.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(list(self.root.rglob('*.jpg')), [])

    async def test_discard_stale_own_file(self):
        result = await self.storage.save(self.jpeg, self.stamp, 'manual')
        await self.storage.discard(result)
        self.assertFalse((self.root / result['filename']).exists())

    async def test_invalid_jpeg_rejected_before_directory_creation(self):
        for data in (b'notjpeg', self.jpeg[:-4], bytearray(self.jpeg), b'\xff\xd8bogus\xff\xd9',
                     b'\xff\xd8' + b'x' * ns['MAX_JPEG_BYTES'] + b'\xff\xd9'):
            with self.subTest(size=len(data)), self.assertRaises((ValueError, OSError)):
                await self.storage.save(data, self.stamp, 'manual')
        self.assertFalse(self.root.exists())

    async def test_pixel_bound_is_enforced(self):
        with patch.dict(ns, MAX_JPEG_PIXELS=5), self.assertRaises(ValueError):
            await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertFalse(self.root.exists())

    async def test_existing_capture_directory_cannot_escape_root(self):
        self.root.mkdir()
        outside = self.root.parent / 'outside'
        outside.mkdir()
        try:
            (self.root / 'WelcomeEye').symlink_to(outside, target_is_directory=True)
        except OSError:
            self.skipTest('OS does not permit creating symlinks')
        with self.assertRaises(ValueError):
            await self.storage.save(self.jpeg, self.stamp, 'manual')
        self.assertEqual(list(outside.iterdir()), [])
