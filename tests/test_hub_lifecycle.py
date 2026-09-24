"""Real hub lifecycle methods with no device, HA installation or network I/O."""
import ast
import asyncio
import importlib.util
from pathlib import Path
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

ROOT = Path(__file__).parents[1] / 'custom_components/welcomeeye_local'
spec = importlib.util.spec_from_file_location('lifecycle_snapshot', ROOT / 'snapshot.py')
snapshot = importlib.util.module_from_spec(spec)
spec.loader.exec_module(snapshot)
tree = ast.parse((ROOT / 'hub.py').read_text(encoding='utf-8'))
tree.body = [n for n in tree.body if not isinstance(n, ast.ImportFrom)]
scope = {'_finish_task': snapshot._finish_task, 'DOMAIN': 'welcomeeye_local'}
exec(compile(tree, str(ROOT / 'hub.py'), 'exec'), scope)
Hub = scope['WelcomeEyeHub']


class HubLifecycleTests(unittest.IsolatedAsyncioTestCase):
    async def test_cleanup_failure_during_cancellation_preserves_cancel(self):
        entered, finish = asyncio.Event(), asyncio.Event()
        async def cleanup():
            entered.set()
            await finish.wait()
            raise RuntimeError('teardown failed')
        owned = asyncio.create_task(cleanup())
        task = asyncio.create_task(snapshot._finish_task(owned, cancel_on_cancel=False))
        await entered.wait()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(owned.done())
        self.assertIsInstance(owned.exception(), RuntimeError)

    def hub(self, model='WelcomeEye Connect 2'):
        hub = Hub.__new__(Hub)
        hub.device_model = model
        hub.ring_listener = SimpleNamespace(start=Mock(), close=Mock())
        hub.path = '/private/live.ts'
        hub._stop_task = None
        hub.stopped = False
        hub.lock = asyncio.Lock()
        hub.consumers = set()
        hub._notify = Mock()
        return hub

    async def test_v1_start_does_not_open_ring_listener(self):
        for model in ('WelcomeEye Connect V1', 'WelcomeEye Connect 2'):
            with self.subTest(model=model):
                hub = self.hub(model)
                server = SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ('127.0.0.1', 123))])
                with patch.object(asyncio, 'start_server', AsyncMock(return_value=server)):
                    await hub.start()
                self.assertFalse(hub.stopped)
                self.assertEqual(hub.ring_listener.start.call_count, int(model != 'WelcomeEye Connect V1'))

    async def test_repeated_stop_cancellation_drains_one_cleanup(self):
        hub = self.hub()
        entered, finish = asyncio.Event(), asyncio.Event()
        async def cleanup():
            entered.set()
            await finish.wait()
        hub._stop = AsyncMock(side_effect=cleanup)
        task = asyncio.create_task(hub.stop())
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        await hub.stop()
        hub._stop.assert_awaited_once()

    async def test_cancelled_release_keeps_lock_until_worker_joined(self):
        hub = self.hub()
        hub.consumers.add('snapshot')
        entered, finish = asyncio.Event(), asyncio.Event()
        async def halt():
            entered.set()
            await finish.wait()
        hub._halt_media = AsyncMock(side_effect=halt)
        task = asyncio.create_task(hub.release('snapshot'))
        await entered.wait()
        task.cancel()
        await asyncio.sleep(0)
        task.cancel()
        await asyncio.sleep(0)
        self.assertTrue(hub.lock.locked())
        self.assertFalse(task.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertFalse(hub.lock.locked())
        self.assertFalse(hub.consumers)
        await hub.release('snapshot')
        hub._halt_media.assert_awaited_once()

    async def test_snapshot_release_preserves_existing_viewer(self):
        hub = self.hub()
        hub.consumers.update(('snapshot', 'viewer', 'microphone'))
        hub._halt_media = AsyncMock()
        await hub.release('snapshot')
        self.assertEqual(hub.consumers, {'viewer', 'microphone'})
        hub._halt_media.assert_not_awaited()

    async def test_late_state_after_shutdown_ignored(self):
        hub = self.hub()
        hub.stopped = True
        hub.ring_connected = False
        hub.ring_error = None
        hub._ring_state(True, 'error')
        self.assertFalse(hub.ring_connected)
        self.assertIsNone(hub.ring_error)
        hub._notify.assert_not_called()

    async def test_newly_identified_v1_disables_only_ring(self):
        hub = self.hub('WelcomeEye')
        hub.device_model_confidence = 'unknown'
        hub.entry = SimpleNamespace(data={}, title='WelcomeEye', unique_id='fixture', entry_id='fixture')
        hub.hass = SimpleNamespace(config_entries=SimpleNamespace(async_update_entry=Mock()))
        hub.ring_image = SimpleNamespace(set_enabled=Mock())
        hub.ring_timer = Mock()
        timer = hub.ring_timer
        hub.session = Mock()
        registry = SimpleNamespace(async_get_device_by_identifier=Mock(return_value=None))
        scope['dr'] = SimpleNamespace(async_get=lambda hass: registry)
        hub._observe_device_model(SimpleNamespace(width=352, height=288), 97)
        self.assertFalse(hub.local_ring_supported)
        hub.ring_listener.close.assert_called_once()
        hub.ring_image.set_enabled.assert_called_once_with(False)
        timer.cancel.assert_called_once()
        hub.session.close.assert_not_called()


if __name__ == '__main__':
    unittest.main()
