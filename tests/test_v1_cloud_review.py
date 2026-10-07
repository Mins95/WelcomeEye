"""Independent regression checks for cloud token rotation, cleanup and HTTP bounds."""
import asyncio
from copy import deepcopy
import json
import sys
from types import ModuleType, SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
import test_v1_cloud as baseline

cloud = load('v1_cloud')
protocol = load('v1_cloud_protocol')


def credentials(token):
    return {'fcm': {'registration': {'token': token}}}


class Response:
    def __init__(self, *, status=200, chunks=(), block=False):
        self.status = status
        self.chunks = chunks
        self.block = block
        self.reads = 0
        self.exited = False
        self.content = self

    async def __aenter__(self):
        return self

    async def __aexit__(self, *_args):
        self.exited = True

    async def iter_chunked(self, size):
        if self.block:
            await asyncio.Event().wait()
        for chunk in self.chunks:
            self.reads += 1
            yield chunk


class ControllerReviewTests(unittest.IsolatedAsyncioTestCase):
    fixture = baseline.ControllerTests.fixture

    async def running(self):
        controller, receiver, calls, snapshots = self.fixture()
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.close)
        return controller, receiver, calls, snapshots

    async def test_rotation_disables_old_before_new_and_persists_before_new_post(self):
        controller, _, _, snapshots = await self.running()
        identity = controller._data['client_id']
        requests = []

        async def post(url, payload):
            self.assertFalse(controller.connected)
            requests.append((url, deepcopy(payload)))
            if url == cloud.CHECK_URL:
                return {'re': '1', 'data': [{'app_token': 'PRIVATE_NEW_TOKEN'}]}
            token = payload['app_token_list']['fcm']['app_token']
            if payload['dev_list'][0]['switch_state'] == '1':
                self.assertEqual(token, 'PRIVATE_NEW_TOKEN')
                self.assertEqual(snapshots[-1]['credentials'], credentials(token))
                self.assertEqual(snapshots[-1]['subscription_token'], token)
                self.assertTrue(snapshots[-1]['subscription_pending'])
            return {'re': '1'}

        controller._post = AsyncMock(side_effect=post)
        await controller._credentials_changed(credentials('PRIVATE_NEW_TOKEN'))
        self.assertEqual(len(requests), 3)
        old, new = requests[0][1], requests[1][1]
        self.assertEqual(old['app_token_list']['fcm']['app_token'], 'PRIVATE_TOKEN')
        self.assertEqual(old['dev_list'][0]['switch_state'], '0')
        self.assertEqual(new['dev_list'][0]['switch_state'], '1')
        self.assertEqual(old['account'], new['account'])
        self.assertEqual(controller._data['client_id'], identity)
        self.assertEqual(controller._data['subscription_token'], 'PRIVATE_NEW_TOKEN')
        self.assertTrue(controller.connected)
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))

    async def test_same_token_update_does_not_rewrite_provider_subscription(self):
        controller, _, _, _ = await self.running()
        controller._post.reset_mock()
        await controller._credentials_changed(credentials('PRIVATE_TOKEN'))
        controller._post.assert_not_called()
        self.assertTrue(controller.connected)

    async def test_rotation_cleanup_failure_keeps_old_intent_and_never_enables_new(self):
        controller, _, _, snapshots = await self.running()
        controller._post = AsyncMock(side_effect=OSError('PRIVATE_PROVIDER_BODY PRIVATE_TOKEN'))
        with self.assertRaises(cloud.CloudStateError):
            await controller._credentials_changed(credentials('PRIVATE_NEW_TOKEN'))
        self.assertFalse(controller.connected)
        self.assertTrue(controller._data['subscription_pending'])
        self.assertEqual(controller._data['subscription_token'], 'PRIVATE_TOKEN')
        self.assertEqual(snapshots[-1]['subscription_token'], 'PRIVATE_TOKEN')
        self.assertEqual(controller._post.await_count, 1)
        sent = controller._post.call_args.args[1]
        self.assertEqual(sent['dev_list'][0]['switch_state'], '0')
        self.assertEqual(sent['app_token_list']['fcm']['app_token'], 'PRIVATE_TOKEN')
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))
        controller._post = AsyncMock(return_value={'re': '1'})

    async def test_rotation_after_disable_never_enables_new_token(self):
        controller, _, _, snapshots = await self.running()
        controller.entry.data['v1_cloud_doorbell_enabled'] = False
        controller._post.reset_mock()
        await controller._credentials_changed(credentials('PRIVATE_NEW_TOKEN'))
        self.assertFalse(controller.connected)
        for call in controller._post.call_args_list:
            self.assertEqual(call.args[1]['dev_list'][0]['switch_state'], '0')
        self.assertFalse(controller._data['subscription_pending'])
        self.assertTrue(snapshots)

    def cleanup_fixture(self, stored):
        entry = SimpleNamespace(entry_id='synthetic-entry', unique_id='PRIVATE_UID',
                                data={'device_variant': 'connect_v1', 'v1_cloud_doorbell_enabled': False})
        controller = cloud.V1CloudDoorbell(None, entry, Mock(), on_state=Mock())
        controller._receiver_factory = Mock(side_effect=AssertionError('receiver must not start'))
        controller._post = AsyncMock(return_value={'re': '1'})
        store = SimpleNamespace(async_load=AsyncMock(return_value=deepcopy(stored)), async_save=AsyncMock())
        storage_module = ModuleType('homeassistant.helpers.storage')
        storage_module.Store = Mock(return_value=store)
        aiohttp_module = ModuleType('homeassistant.helpers.aiohttp_client')
        aiohttp_module.async_get_clientsession = Mock(return_value=Mock())
        modules = {'homeassistant.helpers.storage': storage_module,
                   'homeassistant.helpers.aiohttp_client': aiohttp_module}
        return controller, store, storage_module, modules

    def pending_state(self):
        return {'uid': 'PRIVATE_UID', 'client_id': protocol.make_client_id('SYNTHETIC_INSTALL'),
                'credentials': credentials('PRIVATE_TOKEN'), 'seen': [],
                'subscription_pending': True, 'subscription_token': 'PRIVATE_TOKEN'}

    async def test_disabled_reload_retries_only_existing_own_disable(self):
        controller, store, storage_module, modules = self.cleanup_fixture(self.pending_state())
        with patch.dict(sys.modules, modules):
            await controller.cleanup_pending()
            await asyncio.wait_for(controller._task, 1)
            await controller.close()
        controller._receiver_factory.assert_not_called()
        self.assertEqual(controller._post.await_count, 1)
        payload = controller._post.call_args.args[1]
        self.assertEqual(payload['dev_list'][0]['switch_state'], '0')
        self.assertEqual(payload['dev_list'][0]['gid'], 'PRIVATE_UID')
        self.assertEqual(payload['app_token_list']['fcm']['app_token'], 'PRIVATE_TOKEN')
        self.assertFalse(store.async_save.call_args.args[0]['subscription_pending'])
        self.assertTrue(storage_module.Store.call_args.kwargs['private'])
        self.assertFalse(controller.connected)

    async def test_cleanup_only_does_not_create_installation_when_state_absent(self):
        controller, store, _, modules = self.cleanup_fixture(None)
        with patch.dict(sys.modules, modules), patch.object(cloud.uuid, 'uuid4') as new_id:
            await controller.cleanup_pending()
            await asyncio.wait_for(controller._task, 1)
            await controller.close()
        new_id.assert_not_called()
        store.async_save.assert_not_called()
        controller._post.assert_not_called()
        controller._receiver_factory.assert_not_called()

    async def test_cleanup_only_failure_remains_observable_and_persists_intent(self):
        controller, store, _, modules = self.cleanup_fixture(self.pending_state())
        controller._post = AsyncMock(side_effect=TimeoutError('PRIVATE_PROVIDER_BODY'))
        with patch.dict(sys.modules, modules):
            await controller.cleanup_pending()
            await asyncio.wait_for(controller._task, 1)
            await controller.close()
        self.assertTrue(store.async_save.call_args.args[0]['subscription_pending'])
        self.assertEqual(store.async_save.call_args.args[0]['subscription_token'], 'PRIVATE_TOKEN')
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')
        self.assertEqual(controller.diagnostics()['cleanup_error_type'], 'TimeoutError')
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))
        controller._receiver_factory.assert_not_called()

    async def test_cleanup_only_rejects_state_owned_by_other_device(self):
        stored = self.pending_state()
        stored['uid'] = 'OTHER_PRIVATE_UID'
        controller, store, _, modules = self.cleanup_fixture(stored)
        with patch.dict(sys.modules, modules):
            await controller.cleanup_pending()
            await asyncio.wait_for(controller._task, 1)
            await controller.close()
        controller._post.assert_not_called()
        store.async_save.assert_not_called()
        self.assertEqual(controller.diagnostics()['cleanup_error_type'], 'CloudStateError')

    async def test_cleanup_only_not_selected_for_absent_option_or_non_v1(self):
        for data in [{'device_variant': 'connect_v1'},
                     {'device_variant': 'connect_v1', 'v1_cloud_doorbell_enabled': True},
                     {'device_variant': 'connect2_r001', 'v1_cloud_doorbell_enabled': False}]:
            controller, _, _, _ = self.cleanup_fixture(None)
            controller.entry.data = data
            controller._load = AsyncMock()
            await controller.cleanup_pending()
            controller._load.assert_not_called()
            self.assertIsNone(controller._task)
            await controller.close()

    async def post_fixture(self, response):
        controller, _, _, _ = self.fixture()
        session = SimpleNamespace(post=Mock(return_value=response))
        controller._session = session
        result = await cloud.V1CloudDoorbell._post(controller, cloud.SUBSCRIBE_URL,
                                                 {'synthetic': 'PRIVATE_TOKEN'})
        return result, session

    async def test_http_request_and_fragmented_response_use_expected_safe_settings(self):
        wire = protocol.encode_payload({'re': '1'}).encode('ascii')
        response = Response(chunks=[wire[:2], wire[2:7], wire[7:]])
        result, session = await self.post_fixture(response)
        self.assertEqual(result, {'re': '1'})
        options = session.post.call_args.kwargs
        self.assertIs(options['allow_redirects'], False)
        self.assertNotIn('ssl', options)
        self.assertEqual(options['headers']['Content-Type'], 'text/plain; charset=utf-8')
        self.assertNotIn('PRIVATE_TOKEN', options['data'])
        self.assertTrue(response.exited)

    async def test_http_status_error_does_not_read_or_surface_private_body(self):
        response = Response(status=403, chunks=[b'PRIVATE_PROVIDER_BODY'])
        with self.assertRaises(cloud.CloudStateError) as raised:
            await self.post_fixture(response)
        self.assertNotIn('PRIVATE', str(raised.exception))
        self.assertEqual(response.reads, 0)
        self.assertTrue(response.exited)

    async def test_http_size_limit_stops_reader_before_remaining_body(self):
        response = Response(chunks=[b'x' * 4096] * 18)
        with self.assertRaises(cloud.CloudStateError) as raised:
            await self.post_fixture(response)
        self.assertEqual(response.reads, 17)
        self.assertNotIn('PRIVATE', str(raised.exception))
        self.assertTrue(response.exited)

    async def test_http_timeout_and_cancel_close_response(self):
        response = Response(block=True)
        with patch.object(cloud, 'REQUEST_TIMEOUT', 0.01):
            with self.assertRaises(TimeoutError):
                await self.post_fixture(response)
        self.assertTrue(response.exited)
        response = Response(block=True)
        task = asyncio.create_task(self.post_fixture(response))
        await asyncio.sleep(0)
        task.cancel()
        with self.assertRaises(asyncio.CancelledError):
            await task
        self.assertTrue(response.exited)

    async def test_http_invalid_ciphertext_error_is_fixed(self):
        response = Response(chunks=[b'PRIVATE_PROVIDER_BODY!'])
        with self.assertRaises(protocol.CloudProtocolError) as raised:
            await self.post_fixture(response)
        self.assertEqual(str(raised.exception), 'invalid_encoding')
        self.assertTrue(response.exited)


if __name__ == '__main__':
    unittest.main()
