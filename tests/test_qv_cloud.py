"""Synthetic QV controller lifecycle/storage tests; no network or equipment."""
import asyncio
from copy import deepcopy
import json
from pathlib import Path
import sys
import tempfile
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import load
from test_qv_cloud_events import payload, UID

cloud = load('qv_cloud')
protocol = load('qv_cloud_protocol')
INSTALLATION = '01234567-89ab-4cde-8fab-0123456789ab'


class Receiver:
    def __init__(self, owner):
        self.owner = owner
        self.ready = asyncio.Event()
        self.connected = self.closed = False
        self.register_calls = 0

    async def register(self):
        self.register_calls += 1
        await self.owner._credentials_changed({'fcm': {'registration': {'token': 'PRIVATE_FCM'}}})
        return 'PRIVATE_FCM'

    async def run(self):
        self.connected = True
        self.ready.set()
        await asyncio.Event().wait()

    async def close(self):
        self.connected = False
        self.closed = True

    def diagnostics(self):
        return {'messages': 0, 'malformed': 0, 'private': 'PRIVATE_DIAGNOSTIC'}


class QVControllerTests(unittest.IsolatedAsyncioTestCase):
    @staticmethod
    def subscription_state(enabled=True, events=protocol.CALL_EVENTS, *, detail=True):
        events = tuple(events) if enabled else ()
        return protocol.QvSubscriptionState(
            enabled, events, sum(1 << (event - 1) for event in events), detail)

    def fixture(self, *, enabled=True, variant='connect3', second=False,
                authorized=(0, 1), model='PRIVATE_MODEL', shared=False):
        account = dict(email='PRIVATE_EMAIL@example.invalid', password='PRIVATE_PASSWORD',
                       installation_id=INSTALLATION, device={'uid': UID}, region_id=0)
        entry = SimpleNamespace(entry_id='synthetic-qv', unique_id='local-provisional-id',
            data={'device_variant': variant, 'qv_cloud_enabled': enabled,
                  'qv_cloud_account': account, 'second_channel_enabled': second})
        if variant == 'connect3':
            entry.data['protocol_family'] = 'connect3_qv_experimental'
        elif variant == 'connect2_r002':
            entry.data['protocol_family'] = 'r002_experimental'
        temporary = tempfile.TemporaryDirectory()
        self.addCleanup(temporary.cleanup)
        directory = Path(temporary.name)
        hass = SimpleNamespace(config=SimpleNamespace(path=lambda *parts: str(directory.joinpath(*parts))),
            async_add_executor_job=lambda func, *args: asyncio.to_thread(func, *args))
        controller = cloud.QVCloudDoorbell(hass, entry, Mock(), on_state=Mock())
        snapshots, calls = [], []
        path = directory / '.storage' / controller._storage_key

        async def save(data):
            snapshots.append(deepcopy(data))
            path.parent.mkdir(exist_ok=True)
            path.write_text(json.dumps({'version': cloud.STORAGE_VERSION, 'minor_version': 1,
                'key': controller._storage_key, 'data': data}), encoding='utf-8')
        async def read():
            return json.loads(path.read_text())['data'] if path.exists() else None
        store = SimpleNamespace(async_save=AsyncMock(side_effect=save),
                                async_load=AsyncMock(side_effect=read),
                                async_remove=AsyncMock())
        device = protocol.QvCloudDevice(UID, 'PRIVATE_NAME', model, authorized, shared=shared)
        client = SimpleNamespace(async_register=AsyncMock(), async_subscribe=AsyncMock(),
                                 async_query_subscription=AsyncMock(), async_close=AsyncMock(),
                                 subscription_states={})

        async def register(token):
            saved = json.loads(path.read_text())['data']
            self.assertEqual(saved['credentials']['fcm']['registration']['token'], token)
            calls.append(('register', token))
        client.async_register.side_effect = register

        async def subscribe(target, channel, enabled=True):
            self.assertEqual(target.uid, UID)
            protocol.build_subscription_request('SYNTHETIC_SESSION', target, channel, enabled)
            if enabled:
                saved = json.loads(path.read_text())['data']
                if channel is None:
                    self.assertIs(saved['device_subscription'], True)
                else:
                    self.assertIn(channel + 1, saved['subscriptions'])
            calls.append(('subscribe', channel, enabled))
            client.subscription_states[channel] = self.subscription_state(enabled)
        client.async_subscribe.side_effect = subscribe
        async def query(target, channel):
            self.assertEqual(target.uid, UID)
            protocol.build_subscription_query('SYNTHETIC_SESSION', target, channel)
            calls.append(('query', channel))
            return client.subscription_states.get(channel, self.subscription_state(False))
        client.async_query_subscription.side_effect = query

        async def authorize(*, cleanup=False):
            calls.append(('authorize',))
            controller._client = client
            controller._device = device
            controller._allowed_channels = frozenset(ch for ch in controller._configured_channels()
                                                     if ch - 1 in device.channels)
            if not cleanup and not controller._allowed_channels:
                raise protocol.QvCloudError('invalid_channel')
        controller._authorize = AsyncMock(side_effect=authorize)

        async def load_state(*, create=True):
            modules = {
                'homeassistant.helpers.storage': SimpleNamespace(Store=Mock(return_value=store)),
                'homeassistant.helpers.aiohttp_client': SimpleNamespace(
                    async_get_clientsession=Mock(return_value=Mock())),
            }
            with patch.dict(sys.modules, modules):
                await cloud.QVCloudDoorbell._load(controller, create=create)
        controller._load = AsyncMock(side_effect=load_state)
        receiver = Receiver(controller)
        controller._receiver_factory = Mock(return_value=receiver)
        return controller, receiver, client, calls, snapshots

    async def running(self, **kwargs):
        controller, receiver, client, calls, snapshots = self.fixture(**kwargs)
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.stop)
        return controller, receiver, client, calls, snapshots

    def sent(self):
        return int(cloud.time.time() * 1000)

    async def test_explicit_optin_and_qv_variants_only(self):
        for variant, enabled in [('connect3', False), ('connect3', 'true'),
                                 ('connect_v1', True), ('connect2_r001', True),
                                 ('legacy_unknown', True)]:
            controller, receiver, client, _, _ = self.fixture(variant=variant, enabled=enabled)
            await controller.start()
            controller._load.assert_not_called()
            controller._authorize.assert_not_called()
            self.assertEqual(receiver.register_calls, 0)
            client.async_register.assert_not_called()
            await controller.stop()

    async def test_independent_local_id_and_authorization_precede_registration(self):
        controller, receiver, client, calls, snapshots = await self.running()
        self.assertTrue(controller.connected)
        self.assertNotEqual(controller.entry.unique_id, UID)
        self.assertEqual(calls[0], ('authorize',))
        self.assertEqual(calls[1], ('register', 'PRIVATE_FCM'))
        self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                         [('subscribe', 0, True)])
        self.assertEqual(snapshots[-1]['subscriptions'], [1])
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'confirmed')
        readback = controller.diagnostics()['subscription_readback']
        self.assertEqual(set(readback), {'device', 'channel_1'})
        self.assertFalse(readback['device']['before']['enabled'])
        self.assertFalse(readback['channel_1']['before']['enabled'])
        self.assertTrue(readback['channel_1']['after']['enabled'])
        await controller.start()
        self.assertEqual(receiver.register_calls, 1)
        await controller.stop()
        self.assertTrue(receiver.closed)
        self.assertTrue(controller._task.done())
        self.assertFalse(controller.connected)
        client.async_close.assert_awaited()
        self.assertNotIn(('subscribe', 0, False), calls)

    async def test_channel2_requires_explicit_config_and_account_authorization(self):
        for second, authorized, expected in [(False, (0, 1), [0]),
                                            (True, (0,), [0]), (True, (0, 1), [0, 1])]:
            controller, _, _, calls, _ = await self.running(second=second, authorized=authorized)
            self.assertEqual([call[1] for call in calls if call[0] == 'subscribe'], expected)
            await controller._message(payload(DevChNo='1'), 'p2', self.sent())
            if second and 1 in authorized:
                controller._on_ring.assert_called_once_with(2)
            else:
                controller._on_ring.assert_not_called()
            await controller.stop()

    async def test_message_persisted_before_ring_and_replays_filtered(self):
        controller, _, _, _, snapshots = await self.running()
        observed = []
        controller._on_ring.side_effect = lambda channel: observed.append(
            (channel, len(snapshots[-1]['events']['seen'])))
        now = self.sent()
        await controller._message(payload(), 'PRIVATE_MCS', now)
        await controller._message(payload(), 'other-id', now + 1)
        await controller._message(payload(AlarmId='other'), 'PRIVATE_MCS', now + 1)
        self.assertEqual(observed, [(1, 2)])
        self.assertEqual(controller.diagnostics()['events']['duplicate'], 2)
        self.assertEqual(controller.diagnostics()['rings_received'], 1)
        for value in ('PRIVATE', 'local-provisional', INSTALLATION, 'auth_url', 'subscriptions'):
            self.assertNotIn(value, json.dumps(controller.diagnostics()))

    async def test_wrong_uid_indoor_generic_stale_and_missing_time_do_not_ring(self):
        controller, _, _, _, _ = await self.running()
        now = self.sent()
        for data, sent in [(payload(DevUmid='OTHER'), now), (payload(AlarmEvent='38'), now),
                           (payload(AlarmEvent='45'), now), (payload(AlarmEvent='2'), now),
                           ({'title': 'call'}, now), (payload(), now - 121000),
                           (payload(), 0), (payload(), now + 6000)]:
            await controller._message(data, 'id', sent)
        controller._on_ring.assert_not_called()

    async def test_disabled_configuration_and_reassignment_invalidate_callbacks(self):
        for change in ('disable', 'uid', 'account', 'channel'):
            controller, _, _, _, _ = await self.running(second=True)
            if change == 'disable':
                controller.entry.data['qv_cloud_enabled'] = False
            elif change == 'uid':
                controller.entry.unique_id = 'other-local-id'
            elif change == 'account':
                controller.entry.data['qv_cloud_account']['email'] = 'other@example.invalid'
            else:
                controller.entry.data['second_channel_enabled'] = False
            await controller._message(payload(DevChNo='1'), 'id', self.sent())
            controller._on_ring.assert_not_called()
            await controller.stop()

    async def test_failed_replay_save_never_emits(self):
        controller, _, _, _, _ = await self.running()
        controller._store.async_save = AsyncMock()  # A swallowed HA file write failure.
        await controller._message(payload(), 'id', self.sent())
        controller._on_ring.assert_not_called()
        self.assertEqual(controller.diagnostics()['last_error'], 'storage_failed')
        self.assertEqual(controller.diagnostics()['last_error_stage'], 'event_storage')

    async def test_stop_during_save_drains_write_but_does_not_emit(self):
        controller, _, _, _, _ = await self.running()
        entered, finish = asyncio.Event(), asyncio.Event()
        original = controller._store.async_save
        async def delayed(data):
            entered.set()
            await finish.wait()
            await original(data)
        controller._store.async_save = AsyncMock(side_effect=delayed)
        message = asyncio.create_task(controller._message(payload(), 'id', self.sent()))
        await entered.wait()
        stopping = asyncio.create_task(controller.stop())
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        finish.set()
        await message
        await stopping
        controller._on_ring.assert_not_called()
        self.assertFalse(controller._save_tasks)

    async def test_explicit_disable_cleans_saved_channels_without_fcm_enrollment(self):
        controller, receiver, client, calls, snapshots = await self.running(second=True)
        await controller.stop()
        controller.entry.data['qv_cloud_enabled'] = False
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertEqual(receiver.register_calls, 1)
        self.assertIn(('subscribe', 0, False), calls)
        self.assertIn(('subscribe', 1, False), calls)
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'disabled')

    async def test_cleanup_failure_retains_own_pending_intent(self):
        controller, _, client, _, snapshots = await self.running()
        await controller.stop()
        controller.entry.data['qv_cloud_enabled'] = False
        client.async_subscribe.side_effect = ValueError('PRIVATE_PROVIDER_BODY')
        await controller.cleanup_pending()
        await controller._task
        self.assertEqual(snapshots[-1]['subscriptions'], [1])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')
        self.assertNotIn('PRIVATE', json.dumps(controller.diagnostics()))

    async def test_disabled_never_enrolled_entry_makes_no_cloud_request(self):
        controller, receiver, client, _, _ = self.fixture(enabled=False)
        await controller.cleanup_pending()
        await controller._task
        controller._authorize.assert_not_called()
        client.async_register.assert_not_called()
        self.assertEqual(receiver.register_calls, 0)

    async def test_authorization_failure_prevents_receiver_and_registration(self):
        controller, receiver, client, _, _ = self.fixture()
        controller._authorize.side_effect = protocol.QvCloudError('unauthorized_device')
        with patch.object(cloud, 'RECONNECT_DELAYS', (0, 0)):
            await controller.start()
            await asyncio.wait_for(controller._task, 1)
        self.assertEqual(controller._authorize.await_count, cloud.MAX_CONNECTION_ATTEMPTS)
        controller._receiver_factory.assert_not_called()
        client.async_register.assert_not_called()
        self.assertEqual(receiver.register_calls, 0)
        self.assertEqual(controller.diagnostics()['status'], 'failed_reload_required')
        self.assertEqual(controller.diagnostics()['last_error'], 'unauthorized_device')

    async def test_failed_credentials_save_prevents_vendor_registration(self):
        controller, _, client, _, _ = self.fixture()
        await controller._load()
        controller._store.async_save = AsyncMock()
        controller._closed = False
        await controller._authorize()
        with self.assertRaises(cloud.QVCloudStateError):
            await controller._credentials_changed({'fcm': {'registration': {'token': 'NEW_PRIVATE'}}})
        client.async_register.assert_not_called()

    async def test_actual_state_loader_rejects_different_device_account_or_installation(self):
        controller, _, _, _, _ = self.fixture()
        await controller._load()
        data = deepcopy(controller._data)
        factory = Mock(return_value=SimpleNamespace(async_load=AsyncMock(return_value=data)))
        modules = {'homeassistant.helpers.storage': SimpleNamespace(Store=factory),
                   'homeassistant.helpers.aiohttp_client': SimpleNamespace(async_get_clientsession=Mock())}
        for change in ('device', 'email', 'installation'):
            changed = deepcopy(controller._account)
            if change == 'device':
                changed['device']['uid'] = 'OTHER_UID'
            elif change == 'email':
                changed['email'] = 'other@example.invalid'
            else:
                changed['installation_id'] = 'aaaaaaaa-bbbb-4ccc-8ddd-eeeeeeeeeeee'
            entry = SimpleNamespace(entry_id=controller.entry.entry_id, unique_id=controller.entry.unique_id,
                data={**controller.entry.data, 'qv_cloud_account': changed})
            other = cloud.QVCloudDoorbell(controller.hass, entry, Mock())
            with patch.dict(sys.modules, modules):
                with self.assertRaises(cloud.QVCloudStateError):
                    await other._load()

    async def test_actual_authorize_requires_one_exact_qv_membership(self):
        controller, _, _, _, _ = self.fixture()
        services = Mock()
        client = SimpleNamespace(async_login=AsyncMock(), async_list_devices=AsyncMock())
        api = SimpleNamespace(QvCloudClient=Mock(return_value=client))
        discovery = SimpleNamespace(resolve_services=AsyncMock(return_value=services))
        modules = {cloud.__package__ + '.qv_cloud_api': api,
                   cloud.__package__ + '.qv_cloud_discovery': discovery}
        right = protocol.QvCloudDevice(UID, '', '', (0, 1))
        for devices in ((), (protocol.QvCloudDevice('OTHER_UID', UID, '', (0,)),),
                        (protocol.QvCloudDevice(UID, '', '', (0,), cloud_type=3),),
                        (right, right)):
            client.async_list_devices.return_value = devices
            with patch.dict(sys.modules, modules):
                with self.assertRaisesRegex(protocol.QvCloudError, '^unauthorized_device$'):
                    await cloud.QVCloudDoorbell._authorize(controller)
        client.async_list_devices.return_value = (right,)
        with patch.dict(sys.modules, modules):
            await cloud.QVCloudDoorbell._authorize(controller)
        self.assertEqual(controller._allowed_channels, frozenset({1}))

    async def test_remove_cleans_own_subscriptions_before_removing_store(self):
        controller, _, _, calls, snapshots = await self.running()
        await controller.async_remove()
        self.assertIn(('subscribe', 0, False), calls)
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        controller._store.async_remove.assert_awaited_once()
        self.assertIsNone(controller._data)

    async def test_r002_profile_uses_its_own_client_namespace(self):
        controller, receiver, _, _, snapshots = await self.running(variant='connect2_r002')
        self.assertTrue(snapshots[-1]['client_id'].startswith('003-4058-'))
        self.assertTrue(controller.connected)
        await controller._message(payload(), 'r002-id', self.sent())
        controller._on_ring.assert_called_once_with(1)
        self.assertEqual(receiver.register_calls, 1)

    async def test_lost_subscription_reply_retains_intent_for_disable(self):
        controller, receiver, client, calls, snapshots = self.fixture()
        async def lost_reply(device, channel, enabled=True):
            calls.append(('subscribe', channel, enabled))
            if enabled:
                raise protocol.QvCloudError('request_timeout')
        client.async_subscribe.side_effect = lost_reply
        with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
            await controller.start()
            await asyncio.wait_for(controller._task, 2)
        self.assertEqual(snapshots[-1]['subscriptions'], [1])
        self.assertFalse(receiver.ready.is_set())
        self.assertFalse(controller.connected)
        controller.entry.data['qv_cloud_enabled'] = False
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 2)
        self.assertIn(('subscribe', 0, False), calls)
        self.assertEqual(snapshots[-1]['subscriptions'], [])

    async def test_disable_during_subscription_intent_save_blocks_request(self):
        controller, _, client, calls, _ = self.fixture(second=True)
        await controller._load()
        original = controller._store.async_save
        async def disable_before_request(data):
            await original(data)
            if 2 in data['subscriptions']:
                controller.entry.data['second_channel_enabled'] = False
        controller._store.async_save = AsyncMock(side_effect=disable_before_request)
        controller._closed = False
        with self.assertRaisesRegex(protocol.QvCloudError, '^invalid_channel$'):
            await controller._connect()
        self.assertIn(('subscribe', 0, True), calls)
        self.assertNotIn(('subscribe', 1, True), calls)
        self.assertEqual(controller._data['subscriptions'], [1, 2])
        await controller.stop()

    async def test_failed_cleanup_save_does_not_delete_private_recovery_state(self):
        controller, _, _, _, snapshots = await self.running()
        controller._store.async_save = AsyncMock()
        await controller.async_remove()
        self.assertEqual(snapshots[-1]['subscriptions'], [1])
        self.assertEqual(controller._data['subscriptions'], [1])
        controller._store.async_remove.assert_not_called()
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')

    async def test_owned_vdp_uses_one_device_subscription_and_verified_readback(self):
        for second in (False, True):
            with self.subTest(second=second):
                controller, receiver, _, calls, snapshots = await self.running(
                    model='VDP-IDS-SYNTHETIC', second=second)
                self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                                 [('subscribe', None, True)])
                self.assertEqual(snapshots[-1]['subscriptions'], [])
                self.assertIs(snapshots[-1]['device_subscription'], True)
                diag = controller.diagnostics()
                self.assertEqual(diag['subscription_scope'], 'device')
                self.assertEqual(diag['subscription_status'], 'confirmed')
                self.assertEqual(diag['subscription_readback']['device'], {
                    'before': {'status': 'observed', 'enabled': False,
                               'call_events': [], 'detail_present': True},
                    'after': {'status': 'observed', 'enabled': True,
                              'call_events': list(protocol.CALL_EVENTS), 'detail_present': True}})
                self.assertTrue(receiver.ready.is_set())
                self.assertFalse(diag['phone_coexistence_verified'])
                diag['subscription_readback']['device']['after']['enabled'] = False
                self.assertTrue(controller.diagnostics()['subscription_readback']['device']['after']['enabled'])
                await controller._message(payload(DevChNo='1'), 'second', self.sent())
                if second:
                    controller._on_ring.assert_called_once_with(2)
                else:
                    controller._on_ring.assert_not_called()
                await controller.stop()

    async def test_shared_vdp_subset_keeps_channel_scope_and_never_queries_global(self):
        controller, _, _, calls, snapshots = await self.running(
            model='VDP-IDS-SYNTHETIC', shared=True, authorized=(0,), second=True)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                         [('subscribe', 0, True)])
        self.assertNotIn(('query', None), calls)
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_scope_source'], 'shared_channel_grant')
        await controller._message(payload(DevChNo='1'), 'unauthorized-second', self.sent())
        controller._on_ring.assert_not_called()

    async def test_actual_loader_migrates_legacy_channels_before_device_subscription(self):
        controller, receiver, client, calls, snapshots = self.fixture(
            model='VDP-IDS-SYNTHETIC', second=True)
        await controller._load()
        legacy = deepcopy(controller._data)
        legacy.pop('device_subscription')
        legacy.update(subscriptions=[1, 2], credentials={
            'fcm': {'registration': {'token': 'PRIVATE_FCM'}}})
        await controller._store.async_save(legacy)
        client.subscription_states.update({
            0: self.subscription_state(events=(19,)),
            1: self.subscription_state(events=(19,))})
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.stop)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'], [
            ('subscribe', 0, False), ('subscribe', 1, False), ('subscribe', None, True)])
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        self.assertTrue(snapshots[-1]['device_subscription'])
        for key in ('binding', 'client_id', 'credentials'):
            self.assertEqual(snapshots[-1][key], legacy[key])
        self.assertTrue(any(row['subscriptions'] == [2] and not row['device_subscription']
                            for row in snapshots if 'device_subscription' in row))

    async def test_actual_loader_rejects_invalid_device_intent_before_cloud(self):
        for value in (None, 0, 1, 'true', [], {}):
            with self.subTest(value=value):
                controller, receiver, client, _, _ = self.fixture()
                await controller._load()
                invalid = {**controller._data, 'device_subscription': value}
                await controller._store.async_save(invalid)
                with self.assertRaises(cloud.QVCloudStateError):
                    await controller._load()
                controller._authorize.assert_not_called()
                self.assertEqual(receiver.register_calls, 0)
                client.async_register.assert_not_called()

    async def test_cleanup_only_drains_mixed_legacy_and_device_intents_without_enrollment(self):
        controller, receiver, client, calls, snapshots = self.fixture(
            enabled=False, model='VDP-IDS-SYNTHETIC')
        await controller._load()
        pending = {**controller._data, 'subscriptions': [1, 2], 'device_subscription': True,
                   'credentials': {'fcm': {'registration': {'token': 'PRIVATE_FCM'}}}}
        await controller._store.async_save(pending)
        client.subscription_states.update({
            channel: self.subscription_state() for channel in (None, 0, 1)})
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'], [
            ('subscribe', 0, False), ('subscribe', 1, False), ('subscribe', None, False)])
        self.assertEqual(receiver.register_calls, 0)
        self.assertFalse(receiver.ready.is_set())
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'disabled')
        client.async_register.assert_awaited_once_with('PRIVATE_FCM')

    async def test_cleanup_only_loads_legacy_file_without_inventing_device_intent(self):
        controller, receiver, client, calls, snapshots = self.fixture(
            enabled=False, model='IDS')
        await controller._load()
        legacy = deepcopy(controller._data)
        legacy.pop('device_subscription')
        legacy.update(subscriptions=[1, 2], credentials={
            'fcm': {'registration': {'token': 'PRIVATE_FCM'}}})
        await controller._store.async_save(legacy)
        client.subscription_states.update({ch: self.subscription_state() for ch in (0, 1)})
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                         [('subscribe', 0, False), ('subscribe', 1, False)])
        self.assertEqual(receiver.register_calls, 0)
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'disabled')

    async def test_device_lost_ack_retains_intent_and_cleanup_verifies_disable(self):
        controller, receiver, client, calls, snapshots = self.fixture(model='IDS')
        original = client.async_subscribe.side_effect
        async def lost_reply(device, channel, enabled=True):
            await original(device, channel, enabled)
            if enabled:
                raise protocol.QvCloudError('request_timeout')
        client.async_subscribe.side_effect = lost_reply
        with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
            await controller.start()
            await asyncio.wait_for(controller._task, 1)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertTrue(client.subscription_states[None].enabled)
        self.assertFalse(receiver.ready.is_set())
        controller.entry.data['qv_cloud_enabled'] = False
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertIn(('subscribe', None, False), calls)
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_readback']['device']['after_disable']['enabled'],
                         False)

    async def test_device_readback_mismatch_never_starts_receiver_and_keeps_intent(self):
        for observed in (self.subscription_state(False), self.subscription_state(events=(19,)),
                         self.subscription_state(events=(20, 38, 39, 45, 46))):
            with self.subTest(observed=observed):
                controller, receiver, client, _, snapshots = self.fixture(model='IDS')
                original = client.async_query_subscription.side_effect
                async def mismatch(device, channel):
                    actual = await original(device, channel)
                    return observed if actual.enabled else actual
                client.async_query_subscription.side_effect = mismatch
                with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
                    await controller.start()
                    await asyncio.wait_for(controller._task, 1)
                self.assertFalse(receiver.ready.is_set())
                self.assertFalse(controller.connected)
                self.assertTrue(snapshots[-1]['device_subscription'])
                self.assertEqual(controller.diagnostics()['subscription_status'], 'mismatch')
                self.assertEqual(controller.diagnostics()['last_error'], 'subscription_mismatch')

    async def test_before_readback_failure_allows_verified_write_but_after_failure_blocks(self):
        for failing_phase in ('before', 'after'):
            with self.subTest(phase=failing_phase):
                controller, receiver, client, _, snapshots = self.fixture(model='IDS')
                original = client.async_query_subscription.side_effect
                async def query(device, channel):
                    actual = await original(device, channel)
                    if actual.enabled == (failing_phase == 'after'):
                        raise protocol.QvCloudError('request_timeout')
                    return actual
                client.async_query_subscription.side_effect = query
                with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
                    await controller.start()
                    if failing_phase == 'before':
                        await asyncio.wait_for(receiver.ready.wait(), 1)
                        self.addAsyncCleanup(controller.stop)
                        self.assertEqual(controller.diagnostics()['subscription_status'], 'confirmed')
                    else:
                        await asyncio.wait_for(controller._task, 1)
                        self.assertFalse(receiver.ready.is_set())
                        self.assertEqual(controller.diagnostics()['subscription_status'], 'unverified')
                self.assertTrue(snapshots[-1]['device_subscription'])
                observed = controller.diagnostics()['subscription_readback']['device']
                self.assertEqual(observed[failing_phase]['status'], 'failed')
                self.assertEqual(observed[failing_phase]['error'], 'request_timeout')
                await controller.stop()

    async def test_stop_during_device_intent_save_drains_before_any_enable(self):
        controller, receiver, client, calls, snapshots = self.fixture(model='IDS')
        await controller._load()
        entered, release = asyncio.Event(), asyncio.Event()
        original = controller._store.async_save.side_effect
        async def delayed(data):
            if data['device_subscription']:
                entered.set()
                await release.wait()
            await original(data)
        controller._store.async_save.side_effect = delayed
        await controller.start()
        await asyncio.wait_for(entered.wait(), 1)
        stopping = asyncio.create_task(controller.stop())
        await asyncio.sleep(0)
        self.assertFalse(stopping.done())
        release.set()
        await asyncio.wait_for(stopping, 1)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertNotIn(('subscribe', None, True), calls)
        self.assertFalse(receiver.ready.is_set())
        self.assertFalse(controller._save_tasks)

    async def test_stop_during_postwrite_readback_preserves_cleanup_intent(self):
        controller, receiver, client, _, snapshots = self.fixture(model='IDS')
        entered = asyncio.Event()
        original = client.async_query_subscription.side_effect
        async def blocked(device, channel):
            actual = await original(device, channel)
            if actual.enabled:
                entered.set()
                await asyncio.Event().wait()
            return actual
        client.async_query_subscription.side_effect = blocked
        await controller.start()
        await asyncio.wait_for(entered.wait(), 1)
        await asyncio.wait_for(controller.stop(), 1)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertFalse(receiver.ready.is_set())
        self.assertFalse(controller.connected)
        client.async_query_subscription.side_effect = original
        controller.entry.data['qv_cloud_enabled'] = False
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertFalse(snapshots[-1]['device_subscription'])

    async def test_disable_during_postwrite_readback_cannot_confirm_or_start(self):
        controller, receiver, client, _, snapshots = self.fixture(model='IDS')
        original = client.async_query_subscription.side_effect
        async def disable(device, channel):
            actual = await original(device, channel)
            if actual.enabled:
                controller.entry.data['qv_cloud_enabled'] = False
            return actual
        client.async_query_subscription.side_effect = disable
        await controller.start()
        await asyncio.wait_for(controller._task, 1)
        self.assertFalse(receiver.ready.is_set())
        self.assertFalse(controller.connected)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertNotEqual(controller.diagnostics()['subscription_status'], 'confirmed')

    async def test_identity_change_during_before_query_preserves_legacy_cleanup_intent(self):
        for change in ('account', 'uid'):
            with self.subTest(change=change):
                controller, receiver, client, calls, snapshots = self.fixture(model='IDS')
                await controller._load()
                await controller._save(subscriptions={1, 2})
                client.subscription_states.update({ch: self.subscription_state() for ch in (0, 1)})
                original = client.async_query_subscription.side_effect
                async def reassign(device, channel):
                    observed = await original(device, channel)
                    if change == 'account':
                        controller.entry.data['qv_cloud_account']['email'] = 'other@example.invalid'
                    else:
                        controller.entry.unique_id = 'other-local-uid'
                    return observed
                client.async_query_subscription.side_effect = reassign
                await controller.start()
                await asyncio.wait_for(controller._task, 1)
                self.assertEqual([call for call in calls if call[0] == 'query'], [('query', None)])
                client.async_subscribe.assert_not_called()
                self.assertEqual(snapshots[-1]['subscriptions'], [1, 2])
                self.assertFalse(snapshots[-1]['device_subscription'])
                self.assertFalse(receiver.ready.is_set())
                self.assertFalse(controller.connected)

    async def test_device_cleanup_mismatch_or_failed_save_keeps_recovery_state(self):
        for failure in ('readback', 'save'):
            with self.subTest(failure=failure):
                controller, _, client, _, snapshots = await self.running(model='IDS')
                if failure == 'readback':
                    client.async_query_subscription.side_effect = None
                    client.async_query_subscription.return_value = self.subscription_state()
                else:
                    controller._store.async_save.side_effect = None
                await controller.async_remove()
                self.assertTrue(snapshots[-1]['device_subscription'])
                self.assertTrue(controller._data['device_subscription'])
                controller._store.async_remove.assert_not_called()
                self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')

    async def test_full_call_mask_does_not_turn_accepted_or_indoor_events_into_rings(self):
        controller, _, _, _, _ = await self.running(model='IDS', second=True)
        for event in ('20', '38', '39', '45', '46'):
            for channel in ('0', '1'):
                await controller._message(payload(AlarmEvent=event, DevChNo=channel),
                                          f'not-ring-{event}-{channel}', self.sent())
        controller._on_ring.assert_not_called()
        self.assertEqual(controller.diagnostics()['rings_received'], 0)
        await controller._message(payload(AlarmId='real-outdoor-call'), 'outdoor-19', self.sent())
        controller._on_ring.assert_called_once_with(1)

    async def test_unknown_owned_model_cleans_saved_device_scope_before_channel_fallback(self):
        controller, receiver, client, calls, snapshots = self.fixture()
        await controller._load()
        await controller._save(device_subscription=True)
        client.subscription_states[None] = self.subscription_state()
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.stop)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                         [('subscribe', None, False), ('subscribe', 0, True)])
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual(snapshots[-1]['subscriptions'], [1])
        self.assertEqual(controller.diagnostics()['subscription_scope'], 'channel')
        self.assertEqual(controller.diagnostics()['subscription_status'], 'confirmed')

    async def test_shared_rights_cannot_cleanup_global_intent_or_enable_fallback(self):
        controller, receiver, client, calls, snapshots = self.fixture(
            model='IDS', shared=True, authorized=(0,))
        await controller._load()
        await controller._save(device_subscription=True)
        client.subscription_states[None] = self.subscription_state()
        with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
            await controller.start()
            await asyncio.wait_for(controller._task, 1)
        self.assertFalse(receiver.ready.is_set())
        self.assertFalse(controller.connected)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertEqual([call for call in calls if call[0] == 'subscribe'], [])
        self.assertNotIn(('query', None), calls)
        controller.entry.data['qv_cloud_enabled'] = False
        await controller.cleanup_pending()
        await asyncio.wait_for(controller._task, 1)
        self.assertTrue(snapshots[-1]['device_subscription'])
        self.assertEqual(controller.diagnostics()['subscription_status'], 'cleanup_pending')

    async def test_lost_legacy_disable_ack_blocks_migration_and_retries_same_scope(self):
        controller, receiver, client, calls, snapshots = self.fixture(model='IDS')
        await controller._load()
        await controller._save(subscriptions={1, 2})
        client.subscription_states.update({ch: self.subscription_state() for ch in (0, 1)})
        original = client.async_subscribe.side_effect
        async def lost_disable(device, channel, enabled=True):
            await original(device, channel, enabled)
            if not enabled:
                raise protocol.QvCloudError('request_timeout')
        client.async_subscribe.side_effect = lost_disable
        with patch.object(cloud, 'MAX_CONNECTION_ATTEMPTS', 1):
            await controller.start()
            await asyncio.wait_for(controller._task, 1)
        self.assertEqual(snapshots[-1]['subscriptions'], [1, 2])
        self.assertFalse(snapshots[-1]['device_subscription'])
        self.assertEqual([call for call in calls if call[0] == 'subscribe'],
                         [('subscribe', 0, False)])
        self.assertFalse(receiver.ready.is_set())
        client.async_subscribe.side_effect = original
        await controller.start()
        await asyncio.wait_for(receiver.ready.wait(), 1)
        self.addAsyncCleanup(controller.stop)
        self.assertEqual([call for call in calls if call[0] == 'subscribe'], [
            ('subscribe', 0, False), ('subscribe', 0, False),
            ('subscribe', 1, False), ('subscribe', None, True)])
        self.assertEqual(snapshots[-1]['subscriptions'], [])
        self.assertTrue(snapshots[-1]['device_subscription'])


if __name__ == '__main__':
    unittest.main()
