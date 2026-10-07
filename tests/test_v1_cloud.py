"""Synthetic LT/FCM controller lifecycle. No Google/provider/device traffic."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load

cloud = load('v1_cloud')


async def save_fixture_state(controller, data):
    path = Path(controller.hass.config.path('.storage', controller._storage_key))
    path.parent.mkdir(exist_ok=True)
    path.write_text(json.dumps({'version': cloud.STORAGE_VERSION, 'minor_version': 1,
                                'key': controller._storage_key, 'data': data}), encoding='utf-8')


class Receiver:
    def __init__(self, owner):
        self.owner = owner
        self.connected = False
        self.closed = False
        self.ready = asyncio.Event()

    async def register(self):
        await self.owner._credentials_changed({'fcm': {'registration': {'token': 'PRIVATE_TOKEN'}}})
        return 'PRIVATE_TOKEN'

    async def run(self):
        self.connected = True
        self.owner._notify()
        self.ready.set()
        await asyncio.Event().wait()

    async def close(self):
        self.connected = False
        self.closed = True


class ControllerTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, *, variant='connect_v1', enabled=True):
        entry = SimpleNamespace(entry_id='synthetic-entry', unique_id='PRIVATE_UID',
            data={'device_variant': variant, 'v1_cloud_doorbell_enabled': enabled})
        calls, snapshots = [], []
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda *parts: str(directory.joinpath(*parts))),
            async_add_executor_job=lambda func, *args: asyncio.to_thread(func, *args))
        controller = cloud.V1CloudDoorbell(hass, entry, Mock(), on_state=Mock())

        async def save(data):
            snapshots.append(deepcopy(data))
            await save_fixture_state(controller, data)
        store = SimpleNamespace(async_save=AsyncMock(side_effect=save))

        async def load_state():
            controller._store = store
            controller._data = {'uid': entry.unique_id, 'client_id':
                cloud.make_client_id('0123456789abcdef0123456789abcdef'),
                'credentials': None, 'subscription_pending': False, 'seen': []}

        async def post(url, payload):
            calls.append((url, deepcopy(payload)))
            if url == cloud.CHECK_URL:
                return {'re': '1', 'data': [{'app_token': 'PRIVATE_TOKEN'}]}
            return {'re': '1'}

        receiver = Receiver(controller)
        controller._load = AsyncMock(side_effect=load_state)
        controller._post = AsyncMock(side_effect=post)
        controller._receiver_factory = Mock(return_value=receiver)
        return controller, receiver, calls, snapshots

    async def running(self):
        controller, receiver, calls, snapshots = self.fixture()
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.close)
        return controller, receiver, calls, snapshots

    def message(self, uid='PRIVATE_UID', event=14, stamp='20260102123456'):
        return {'message_content': f'0|{uid}|0|{event}|{stamp}|[PRIVATE_NAME]'}

    async def test_disabled_non_v1_unknown_and_wrong_family_make_no_io(self):
        for variant, enabled in [('connect_v1', False), ('connect2_r001', True), ('connect2_r002', True),
                                 ('connect3', True), ('legacy_unknown', True), ('connect_v1', 'true')]:
            with self.subTest(variant=variant, enabled=enabled):
                controller, _, _, _ = self.fixture(variant=variant, enabled=enabled)
                await controller.start()
                controller._load.assert_not_called()
                controller._receiver_factory.assert_not_called()
                self.assertFalse(controller.connected)
                await controller.close()

    async def test_start_background_independent_identity_and_own_cleanup(self):
        controller, receiver, calls, snapshots = await self.running()
        self.assertTrue(controller.connected)
        self.assertEqual(len(calls), 2)
        request = calls[0][1]
        self.assertNotIn('password', json.dumps(request))
        self.assertTrue(any(item.get('credentials') for item in snapshots))
        self.assertTrue(snapshots[-1]['subscription_pending'])
        await controller.start()
        self.assertEqual(controller._receiver_factory.call_count, 1)
        await controller.close()
        self.assertTrue(receiver.closed)
        self.assertFalse(controller.connected)
        self.assertTrue(controller._task.done())
        self.assertEqual(calls[-1][1]['dev_list'][0]['switch_state'], '0')
        self.assertEqual(calls[-1][1]['account'], request['account'])
        self.assertEqual(calls[-1][1]['app_token_list'], request['app_token_list'])
        self.assertFalse(snapshots[-1]['subscription_pending'])

    async def test_only_correct_device_ring_fires_and_duplicates_are_persisted(self):
        controller, _, _, snapshots = await self.running()
        now = int(cloud.time.time() * 1000)
        controller._message(self.message(uid='OTHER'), 'p0', now)
        controller._message(self.message(event=26), 'p1', now)
        controller._message(self.message(), 'PRIVATE_PERSISTENT_ID', now)
        controller._message(self.message(), 'different-transport-id', now)
        controller._message(self.message(stamp='20260102123457'), 'PRIVATE_PERSISTENT_ID', now)
        controller._on_ring.assert_called_once_with(1)
        self.assertEqual(controller.diagnostics()['duplicate_messages'], 2)
        await controller._save_task
        self.assertEqual(len(snapshots[-1]['seen']), 2)
        self.assertTrue(all(len(value) == 64 for value in snapshots[-1]['seen']))
        diag = json.dumps(controller.diagnostics())
        for private in ('PRIVATE_UID', 'PRIVATE_TOKEN', 'PRIVATE_PERSISTENT_ID', 'PRIVATE_NAME',
                        'message_content', 'subscription_token', 'client_id'):
            self.assertNotIn(private, diag)

    async def test_late_unknown_and_future_transport_times_never_ring(self):
        controller, _, _, _ = await self.running()
        now = int(cloud.time.time() * 1000)
        for sent in (0, None, 'now', now - 121000, now + 31000):
            controller._message(self.message(), 'id', sent)
        self.assertEqual(controller.diagnostics()['stale_messages'], 5)
        controller._on_ring.assert_not_called()

    async def test_malformed_does_not_prevent_next_notification(self):
        controller, _, _, _ = await self.running()
        now = int(cloud.time.time() * 1000)
        controller._message({'message_content': 'invalid'}, 'bad', now)
        controller._message(self.message(), 'good', now)
        controller._on_ring.assert_called_once()

    async def test_no_callback_after_unload_disable_or_changed_uid(self):
        for change in ('stop', 'disable', 'uid'):
            with self.subTest(change=change):
                controller, receiver, _, _ = await self.running()
                if change == 'stop':
                    await controller.close()
                elif change == 'disable':
                    controller.entry.data['v1_cloud_doorbell_enabled'] = False
                else:
                    controller.entry.unique_id = 'OTHER'
                controller._message(self.message(), 'late', int(cloud.time.time() * 1000))
                controller._on_ring.assert_not_called()
                self.assertFalse(controller.connected)
                await controller.close()
                self.assertTrue(receiver.closed)

    async def test_registration_failure_is_finite_sanitized_and_nonblocking(self):
        controller, receiver, calls, _ = self.fixture()
        receiver.register = AsyncMock(side_effect=ValueError('PRIVATE_TOKEN PRIVATE_UID'))
        with patch.object(cloud, 'RECONNECT_DELAYS', (0, 0)):
            await controller.start()
            await asyncio.wait_for(controller._task, 2)
        self.assertEqual(receiver.register.await_count, 3)
        self.assertEqual(calls, [])
        diag = controller.diagnostics()
        self.assertEqual(diag['status'], 'failed_reload_required')
        self.assertEqual(diag['last_error_type'], 'ValueError')
        self.assertNotIn('PRIVATE', json.dumps(diag))
        await controller.close()

    async def test_provider_rejection_never_announces_connected(self):
        controller, receiver, _, _ = self.fixture()
        controller._post = AsyncMock(return_value={'re': '0'})
        with patch.object(cloud, 'RECONNECT_DELAYS', (0, 0)):
            await controller.start()
            await asyncio.wait_for(controller._task, 2)
        self.assertFalse(controller.connected)
        self.assertFalse(receiver.ready.is_set())
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')
        self.assertTrue(controller._data['subscription_pending'])
        await controller.close()

    async def test_cancellation_during_subscription_still_disables_own_intent(self):
        controller, receiver, _, snapshots = self.fixture()
        started = asyncio.Event()
        disabled = []
        async def post(url, data):
            if data.get('dev_list', [{}])[0].get('switch_state') == '0':
                disabled.append(data)
                return {'re': '1'}
            started.set()
            await asyncio.Event().wait()
        controller._post = AsyncMock(side_effect=post)
        await controller.start()
        await asyncio.wait_for(started.wait(), 1)
        await asyncio.wait_for(controller.close(), 1)
        self.assertEqual(len(disabled), 1)
        self.assertTrue(receiver.closed)
        self.assertFalse(snapshots[-1]['subscription_pending'])

    async def test_close_before_background_task_starts_and_repeat_close(self):
        controller, _, _, _ = self.fixture()
        await controller.start()
        await controller.close()
        await controller.close()
        self.assertTrue(controller._task.done())

    async def test_cancelled_close_drains_cleanup_before_propagating_cancel(self):
        controller, receiver, _, _ = await self.running()
        entered, finish = asyncio.Event(), asyncio.Event()
        async def disable(url, data):
            entered.set()
            await finish.wait()
            return {'re': '1'}
        controller._post = AsyncMock(side_effect=disable)
        closing = asyncio.create_task(controller.close())
        await entered.wait()
        closing.cancel()
        await asyncio.sleep(0)
        closing.cancel()
        await asyncio.sleep(0)
        self.assertFalse(closing.done())
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await closing
        self.assertTrue(controller._task.done())
        self.assertTrue(controller._close_task.done())
        self.assertTrue(receiver.closed)

    async def test_slow_storage_never_delays_ring_and_saves_newest_dedup_state(self):
        controller, _, _, snapshots = await self.running()
        started, finish = asyncio.Event(), asyncio.Event()
        original = controller._store.async_save
        async def delayed(data):
            started.set()
            await finish.wait()
            await original(data)
        controller._store.async_save = AsyncMock(side_effect=delayed)
        now = int(cloud.time.time() * 1000)
        controller._message(self.message(), 'first', now)
        controller._on_ring.assert_called_once()
        await started.wait()
        controller._message(self.message(stamp='20260102123556'), 'second', now)
        self.assertEqual(controller._on_ring.call_count, 2)
        finish.set()
        await controller._save_task
        self.assertEqual(len(snapshots[-1]['seen']), 4)


if __name__ == '__main__':
    unittest.main()
