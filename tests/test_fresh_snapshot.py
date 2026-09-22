"""Real hub lease/event/teardown code; replace only device worker and imports.

AST loading avoids importing HA/PyAV on dependency-free CI. No hub method is
copied or reimplemented: the class comes from the current production source.
The simulated device uses a real thread, stopped/joined by the real hub.
"""
import ast
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest

ROOT = Path(__file__).parents[1] / 'custom_components/welcomeeye_local'
spec = importlib.util.spec_from_file_location('snapshot_under_test', ROOT / 'snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)


class Boundary:
    def __init__(self, *args):
        self.thread = None

    def close(self):
        pass


tree = ast.parse((ROOT / 'hub.py').read_text(encoding='utf-8'))
tree.body = [n for n in tree.body if not (
    isinstance(n, ast.ImportFrom) and (n.level or n.module.startswith('homeassistant'))
)]
namespace = {'__name__': 'snapshot_hub_under_test', 'DeviceController': Boundary,
             'Talkback': Boundary, 'RingListener': Boundary,
             'new_lifecycle': dict, 'AuthenticationError': type('AuthenticationError', (Exception,), {})}
exec(compile(tree, str(ROOT / 'hub.py'), 'exec'), namespace)
Hub = namespace['WelcomeEyeHub']


async def until(predicate):
    async with asyncio.timeout(2):
        while not predicate():
            await asyncio.sleep(.001)


class FreshSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def asyncSetUp(self):
        self.hub = Hub(None, SimpleNamespace(data={}))
        self.hub.stopped = False
        self.hub._image(b'A')
        self.starts = 0
        self.announce = True
        self.publish = True

        def device(generation, stop):
            self.starts += 1
            if self.announce:
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._state, True)
            if self.publish:
                self.hub.loop.call_soon_threadsafe(self.hub._dispatch, generation, self.hub._image, b'B')
            stop.wait()
        self.hub._worker = device

    async def asyncTearDown(self):
        await self.hub.stop()
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def capture(self, **kwargs):
        return await snapshot.capture_fresh_image(self.hub, **kwargs)

    async def test_old_cache_idle_starts_and_joins_worker(self):
        self.assertEqual(await self.capture(), b'B')
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.hub.snapshot_started_media, 1)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_live_hls_webrtc_and_microphone_leases_remain(self):
        for lease in ('hls', 'webrtc', 'microphone'):
            await self.hub.acquire(lease)
        original = self.hub.thread
        task = asyncio.create_task(self.capture())
        await until(lambda: len(self.hub.consumers) == 4)
        self.hub._image(b'C')
        self.assertEqual(await task, b'C')
        self.assertIs(self.hub.thread, original)
        self.assertEqual(self.hub.consumers, {'hls', 'webrtc', 'microphone'})
        self.assertEqual(self.starts, 1)
        self.assertEqual(self.hub.snapshot_reused_media, 1)

    async def test_simultaneous_idle_requests_share_one_thread(self):
        self.publish = False
        tasks = [asyncio.create_task(self.capture()) for _ in range(2)]
        await until(lambda: len(self.hub.consumers) == 2 and self.hub.connected)
        self.hub._image(b'B')
        self.assertEqual(await asyncio.gather(*tasks), [b'B', b'B'])
        self.assertEqual(self.starts, 1)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_identical_jpeg_bytes_still_count_as_new_generation(self):
        self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: self.hub.connected)
        self.hub._image(b'A')
        self.assertEqual(await task, b'A')
        self.assertEqual(self.hub.image_generation, 2)

    async def test_timeout_does_not_return_cache(self):
        self.publish = False
        self.assertIsNone(await self.capture(timeout=.05))
        self.assertEqual(self.hub.snapshot_timeouts, 1)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_deadline_includes_acquisition(self):
        self.announce = self.publish = False
        self.assertIsNone(await asyncio.wait_for(self.capture(timeout=.05), 1))
        self.assertEqual(self.hub.snapshot_timeouts, 1)
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_cancel_while_acquiring_and_waiting(self):
        self.publish = False
        for announce in (False, True):
            self.announce = announce
            task = asyncio.create_task(self.capture())
            await until(lambda: bool(self.hub.consumers))
            if announce:
                await until(lambda: self.hub.connected)
            task.cancel()
            with self.assertRaises(asyncio.CancelledError):
                await task
            self.assertFalse(self.hub.consumers)
            self.assertIsNone(self.hub.thread)

    async def test_repeated_cancellation_during_cleanup_is_drained(self):
        self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: self.hub.connected)
        await self.hub.lock.acquire()
        task.cancel()
        await asyncio.sleep(.01)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        self.hub.lock.release()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_unload_wakes_pending_image(self):
        self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: self.hub.snapshot_started_media == 1)
        await self.hub.stop()
        self.assertIsNone(await asyncio.wait_for(task, 1))
        self.assertEqual(self.hub.snapshot_last_error_type, 'ConnectionError')

    async def test_cancellation_after_success_cannot_interrupt_release(self):
        self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: self.hub.snapshot_started_media == 1)
        await self.hub.lock.acquire()
        self.hub._image(b'B')
        await until(lambda: self.hub.snapshot_successes == 1)
        task.cancel()
        await asyncio.sleep(.01)
        self.assertFalse(task.done())
        self.hub.lock.release()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(self.hub.consumers)
        self.assertIsNone(self.hub.thread)

    async def test_unload_wakes_pending_acquisition(self):
        self.announce = self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: bool(self.hub.consumers))
        await self.hub.stop()
        self.assertIsNone(await asyncio.wait_for(task, 1))

    async def test_media_failure_wakes_waiter(self):
        self.publish = False
        task = asyncio.create_task(self.capture())
        await until(lambda: self.hub.snapshot_started_media == 1)
        self.hub._state(False, error=ConnectionError())
        self.assertIsNone(await asyncio.wait_for(task, 1))
        self.assertFalse(self.hub.consumers)

    async def test_repeated_requests_require_new_generation(self):
        self.assertEqual(await self.capture(), b'B')
        self.publish = False
        self.assertIsNone(await self.capture(timeout=.05))
        self.assertEqual(self.hub.snapshot_successes, 1)


if __name__ == '__main__':
    unittest.main()
