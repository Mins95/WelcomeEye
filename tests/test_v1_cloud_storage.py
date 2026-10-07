"""Durable cloud identity/intent checks using temporary files and no network."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import unittest
from unittest.mock import AsyncMock

import test_v1_cloud as baseline

cloud = baseline.cloud


class StorageTests(unittest.IsolatedAsyncioTestCase):
    fixture = baseline.ControllerTests.fixture

    def path(self, controller):
        return Path(controller.hass.config.path('.storage', controller._storage_key))

    async def prepared(self):
        controller, receiver, calls, snapshots = self.fixture()
        await controller._load()
        await controller._save()
        return controller, receiver, calls, snapshots

    async def test_swallowed_write_error_blocks_credentials_and_vendor_enable(self):
        controller, receiver, calls, _ = await self.prepared()
        original = self.path(controller).read_bytes()
        controller._store.async_save = AsyncMock()  # HA can return normally without writing.
        with self.assertRaises(cloud.CloudStateError):
            await controller._credentials_changed({'fcm': {'registration': {'token': 'PRIVATE_TOKEN'}}})
        with self.assertRaises(cloud.CloudStateError):
            await controller._subscribe('PRIVATE_TOKEN')
        self.assertEqual(calls, [])
        self.assertFalse(controller._data['subscription_pending'])
        self.assertNotIn('subscription_token', controller._data)
        self.assertEqual(self.path(controller).read_bytes(), original)
        self.assertFalse(receiver.ready.is_set())

    async def test_no_file_is_not_treated_as_saved(self):
        controller, _, calls, _ = self.fixture()
        await controller._load()
        controller._store.async_save = AsyncMock()
        with self.assertRaises(cloud.CloudStateError) as raised:
            await controller._save()
        self.assertEqual(str(raised.exception), '')
        self.assertEqual(calls, [])
        controller._receiver_factory.assert_not_called()

    async def test_failed_cleanup_save_preserves_pending_token_and_next_save(self):
        controller, _, _, _ = await self.prepared()
        await controller._subscribe('PRIVATE_TOKEN')
        original = self.path(controller).read_bytes()
        original_save = controller._store.async_save
        controller._store.async_save = AsyncMock()
        controller._post.reset_mock()
        await controller._unsubscribe()
        controller._post.assert_awaited_once()
        self.assertEqual(controller._post.call_args.args[1]['dev_list'][0]['switch_state'], '0')
        self.assertTrue(controller._data['subscription_pending'])
        self.assertEqual(controller._data['subscription_token'], 'PRIVATE_TOKEN')
        self.assertEqual(self.path(controller).read_bytes(), original)
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')
        self.assertEqual(controller.diagnostics()['cleanup_error_type'], 'CloudStateError')
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))
        controller._store.async_save = original_save
        await controller._save_seen()
        stored = json.loads(self.path(controller).read_text())['data']
        self.assertTrue(stored['subscription_pending'])
        self.assertEqual(stored['subscription_token'], 'PRIVATE_TOKEN')

    async def test_cancelled_save_drains_write_and_verification_before_next_write(self):
        controller, _, _, snapshots = await self.prepared()
        original_save = controller._store.async_save
        entered, finish = asyncio.Event(), asyncio.Event()

        async def delayed(data):
            if data.get('generation') == 1:
                entered.set()
                await finish.wait()
            await original_save(data)

        controller._store.async_save = AsyncMock(side_effect=delayed)
        controller._data['generation'] = 1
        first = asyncio.create_task(controller._save())
        await entered.wait()
        first.cancel()
        controller._data['generation'] = 2
        second = asyncio.create_task(controller._save())
        await asyncio.sleep(0)
        first.cancel()
        await asyncio.sleep(0)
        self.assertTrue(controller._save_lock.locked())
        self.assertFalse(first.done())
        self.assertFalse(second.done())
        self.assertEqual(controller._store.async_save.await_count, 1)
        finish.set()
        with self.assertRaises(asyncio.CancelledError):
            await first
        await second
        self.assertFalse(controller._save_lock.locked())
        self.assertEqual([item['generation'] for item in snapshots[-2:]], [1, 2])
        self.assertEqual(json.loads(self.path(controller).read_text())['data']['generation'], 2)

    async def test_existing_identical_durable_state_is_sufficient(self):
        controller, _, _, _ = await self.prepared()
        controller._store.async_save = AsyncMock()
        await controller._save()

    async def test_wrong_envelope_or_corrupt_file_cannot_confirm_a_save(self):
        controller, _, _, _ = await self.prepared()
        expected = json.loads(self.path(controller).read_text())
        controller._store.async_save = AsyncMock()
        for key, replacement in (('version', 99), ('minor_version', 99), ('key', 'other'), ('data', {})):
            with self.subTest(key=key):
                saved = deepcopy(expected)
                saved[key] = replacement
                self.path(controller).write_text(json.dumps(saved))
                with self.assertRaises(cloud.CloudStateError):
                    await controller._save()
        self.path(controller).write_text('{PRIVATE_CORRUPT')
        with self.assertRaises(cloud.CloudStateError) as raised:
            await controller._save()
        self.assertEqual(str(raised.exception), '')


if __name__ == '__main__':
    unittest.main()
