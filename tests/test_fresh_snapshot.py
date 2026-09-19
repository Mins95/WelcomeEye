"""Shared-worker snapshot behavior, independent of a physical WelcomeEye."""
import asyncio
import importlib.util
from pathlib import Path
import unittest

_spec = importlib.util.spec_from_file_location(
    "welcomeeye_snapshot_under_test",
    Path(__file__).parents[1] / "custom_components/welcomeeye_local/snapshot.py",
)
_module = importlib.util.module_from_spec(_spec)
_spec.loader.exec_module(_module)
capture_fresh_image = _module.capture_fresh_image


class FakeHub:
    def __init__(self, *, image=None, active=False, auto_publish=True):
        self.image = image
        self.image_generation = 0 if image is None else 1
        self.image_event = asyncio.Event()
        self.consumers = {"live"} if active else set()
        self.worker_count = 1 if active else 0
        self.auto_publish = auto_publish
        self.acquire_count = 0
        self.release_count = 0
        self.snapshot_requests = 0
        self.snapshot_successes = 0
        self.snapshot_timeouts = 0
        self.snapshot_errors = 0
        self.snapshot_started_media = 0
        self.snapshot_reused_media = 0
        self.snapshot_wait_elapsed_ms = 0
        self.snapshot_last_error_type = None

    async def acquire(self, consumer):
        self.acquire_count += 1
        self.consumers.add(consumer)
        if not self.worker_count:
            self.worker_count += 1
            if self.auto_publish:
                asyncio.get_running_loop().call_soon(self.publish, b"B")

    async def release(self, consumer, *, reason):
        self.release_count += 1
        self.consumers.discard(consumer)
        if not self.consumers:
            self.worker_count = 0

    def publish(self, image):
        self.image = image
        self.image_generation += 1
        self.image_event.set()

    async def wait_for_image(self, after_generation, timeout):
        deadline = asyncio.get_running_loop().time() + timeout
        while self.image_generation <= after_generation:
            self.image_event.clear()
            if self.image_generation > after_generation:
                break
            remaining = deadline - asyncio.get_running_loop().time()
            if remaining <= 0:
                return None
            try:
                await asyncio.wait_for(self.image_event.wait(), remaining)
            except asyncio.TimeoutError:
                return None
        return self.image


class FreshSnapshotTests(unittest.IsolatedAsyncioTestCase):
    async def test_idle_snapshot_starts_one_worker_and_returns_new_frame(self):
        hub = FakeHub(image=b"A")
        self.assertEqual(await capture_fresh_image(hub, timeout=1), b"B")
        self.assertEqual(hub.acquire_count, 1)
        self.assertEqual(hub.release_count, 1)
        self.assertEqual(hub.worker_count, 0)
        self.assertEqual(hub.snapshot_started_media, 1)

    async def test_active_media_is_reused_and_stays_open(self):
        hub = FakeHub(image=b"A", active=True)
        async def publish_later():
            await asyncio.sleep(0)
            hub.publish(b"B")
        asyncio.create_task(publish_later())
        self.assertEqual(await capture_fresh_image(hub, timeout=1), b"B")
        self.assertEqual(hub.worker_count, 1)
        self.assertEqual(hub.snapshot_reused_media, 1)
        self.assertIn("live", hub.consumers)

    async def test_two_snapshots_share_one_worker(self):
        hub = FakeHub(image=b"A", active=True)
        first = asyncio.create_task(capture_fresh_image(hub, timeout=1))
        second = asyncio.create_task(capture_fresh_image(hub, timeout=1))
        await asyncio.sleep(0)
        hub.publish(b"B")
        self.assertEqual(await asyncio.gather(first, second), [b"B", b"B"])
        self.assertEqual(hub.worker_count, 1)
        self.assertEqual(hub.acquire_count, 2)
        self.assertEqual(len(hub.consumers), 1)

    async def test_timeout_does_not_return_old_image_or_leave_consumer(self):
        hub = FakeHub(image=b"A", auto_publish=False)
        self.assertIsNone(await capture_fresh_image(hub, timeout=0.01))
        self.assertEqual(hub.snapshot_timeouts, 1)
        self.assertEqual(hub.release_count, 1)
        self.assertEqual(hub.image, b"A")
        self.assertFalse(hub.consumers)

    async def test_acquisition_error_returns_no_stale_image(self):
        hub = FakeHub(image=b"A")

        async def fail_acquire(consumer):
            raise ConnectionError("unavailable")

        hub.acquire = fail_acquire
        self.assertIsNone(await capture_fresh_image(hub, timeout=0.01))
        self.assertEqual(hub.snapshot_errors, 1)
        self.assertEqual(hub.snapshot_last_error_type, "ConnectionError")

    async def test_release_error_is_recorded_without_escaping(self):
        hub = FakeHub(image=b"A")

        async def fail_release(consumer, *, reason):
            raise RuntimeError("worker stop failed")

        hub.release = fail_release
        self.assertEqual(await capture_fresh_image(hub, timeout=1), b"B")
        self.assertEqual(hub.snapshot_errors, 1)
        self.assertEqual(hub.snapshot_last_error_type, "RuntimeError")

    async def test_cancellation_releases_lease(self):
        hub = FakeHub(image=b"A", active=True)
        task = asyncio.create_task(capture_fresh_image(hub, timeout=5))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertEqual(hub.release_count, 1)
        self.assertIn("live", hub.consumers)

    async def test_repeated_snapshot_requires_next_generation(self):
        hub = FakeHub(image=b"A", active=True)
        hub.publish(b"B")
        first = await capture_fresh_image(hub, timeout=0.01)
        self.assertIsNone(first)
        task = asyncio.create_task(capture_fresh_image(hub, timeout=1))
        await asyncio.sleep(0)
        hub.publish(b"C")
        self.assertEqual(await task, b"C")


if __name__ == "__main__":
    unittest.main()
