"""Offline V1 manual service authorization, dispatch and real hub lifecycle."""
import asyncio
import sys
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import PACKAGE, cap
from test_hub_lifecycle import Hub
from test_transport_lifecycle import load_source


class HAError(Exception):
    pass


services = load_source('services', {'HomeAssistantError': HAError,
    'POLICY_CONTROL': 'control', 'ProtocolFamily': cap.ProtocolFamily,
    'DeviceVariant': cap.DeviceVariant})
services.__package__ = PACKAGE


class ServiceTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, operation='start', **fields):
        permissions = SimpleNamespace(check_entity=Mock(return_value=True))
        user = SimpleNamespace(is_admin=True, permissions=permissions)
        trial = SimpleNamespace(start=AsyncMock(return_value={'status': 'observing'}),
            stop=AsyncMock(return_value={'status': 'stopped'}),
            mark=Mock(return_value={'markers': [{'sequence': 1}]}),
            snapshot=Mock(return_value={'status': 'idle'}))
        factory = Mock(return_value=trial)
        hub = SimpleNamespace(protocol_family=cap.ProtocolFamily.LEGACY,
                              variant=cap.DeviceVariant.V1)
        entity = SimpleNamespace(entity_id='camera.synthetic_v1', hub=hub,
            hass=SimpleNamespace(auth=SimpleNamespace(async_get_user=AsyncMock(return_value=user))))
        call = SimpleNamespace(context=SimpleNamespace(user_id='synthetic-admin'),
            data={'operation': operation, 'profile': 'control', 'duration': 300,
                  'confirm': False, **fields})
        return entity, call, user, trial, factory

    def backend(self, factory):
        return patch.dict(sys.modules, {f'{PACKAGE}.v1_doorbell_trial':
            SimpleNamespace(V1DoorbellTrial=factory)})

    async def test_lazy_construction_and_explicit_start_parameters(self):
        entity, call, user, trial, factory = self.fixture('status')
        self.assertFalse(hasattr(entity.hub, '_v1_doorbell_trial'))
        with self.backend(factory):
            self.assertEqual(await services._async_v1_observe_doorbell(entity, call), {'status': 'idle'})
            factory.assert_called_once_with(entity.hub)
            trial.start.assert_not_called()
            for profile, duration, confirm in (('control', 300, False),
                    ('control', 300, True), ('long_connection', 30, True)):
                call.data.update(operation='start', profile=profile, duration=duration, confirm=confirm)
                self.assertEqual(await services._async_v1_observe_doorbell(entity, call), {'status': 'observing'})
                trial.start.assert_awaited_with(profile=profile, duration=duration, confirm=confirm)
        factory.assert_called_once()
        user.permissions.check_entity.assert_called_with(entity.entity_id, 'control')

    async def test_mark_status_and_stop_never_start_a_session(self):
        for operation, method in (('mark', 'mark'), ('status', 'snapshot'), ('stop', 'stop')):
            with self.subTest(operation=operation):
                entity, call, _, trial, factory = self.fixture(operation)
                with self.backend(factory):
                    await services._async_v1_observe_doorbell(entity, call)
                getattr(trial, method).assert_called_once_with()
                trial.start.assert_not_called()

    async def test_each_operation_requires_identified_admin_and_target_control(self):
        for operation in ('start', 'mark', 'status', 'stop'):
            for admin, control, user_id in ((False, True, 'user'), (True, False, 'user'),
                                           (True, True, None), (True, True, 'deleted')):
                with self.subTest(operation=operation, admin=admin, control=control, user_id=user_id):
                    entity, call, user, trial, factory = self.fixture(operation, confirm=True)
                    user.is_admin = admin
                    user.permissions.check_entity.return_value = control
                    call.context.user_id = user_id
                    if user_id == 'deleted':
                        entity.hass.auth.async_get_user.return_value = None
                    with self.backend(factory), self.assertRaises(HAError):
                        await services._async_v1_observe_doorbell(entity, call)
                    factory.assert_not_called()
                    self.assertFalse(hasattr(entity.hub, '_v1_doorbell_trial'))
                    for method in (trial.start, trial.stop, trial.mark, trial.snapshot):
                        method.assert_not_called()

    async def test_non_v1_and_conflicting_families_never_construct_trial(self):
        for family, variant in ((cap.ProtocolFamily.LEGACY, cap.DeviceVariant.R001),
                (cap.ProtocolFamily.LEGACY, cap.DeviceVariant.LEGACY_UNKNOWN),
                (cap.ProtocolFamily.R002, cap.DeviceVariant.R002),
                (cap.ProtocolFamily.CONNECT3, cap.DeviceVariant.CONNECT3),
                (cap.ProtocolFamily.R002, cap.DeviceVariant.V1)):
            with self.subTest(family=family, variant=variant):
                entity, call, _, _, factory = self.fixture(confirm=True)
                entity.hub.protocol_family, entity.hub.variant = family, variant
                with self.backend(factory), self.assertRaises(HAError):
                    await services._async_v1_observe_doorbell(entity, call)
                factory.assert_not_called()

    async def test_backend_failures_do_not_export_private_exception_text(self):
        for operation, method in (('start', 'start'), ('stop', 'stop'),
                                  ('mark', 'mark'), ('status', 'snapshot')):
            entity, call, _, trial, factory = self.fixture(operation, confirm=True)
            getattr(trial, method).side_effect = RuntimeError('PRIVATE_PASSWORD_ENDPOINT_PACKET')
            with self.backend(factory), self.assertRaises(HAError) as caught:
                await services._async_v1_observe_doorbell(entity, call)
            self.assertNotIn('PRIVATE', str(caught.exception))
            self.assertTrue(caught.exception.__suppress_context__)


class HubGuardTests(unittest.IsolatedAsyncioTestCase):
    def hub(self):
        hub = Hub.__new__(Hub)
        hub.entry = SimpleNamespace(data={'device_variant': cap.DeviceVariant.V1.value})
        hub.device_model = 'WelcomeEye Connect V1'
        hub.lock = asyncio.Lock()
        hub.stopped = False
        hub.consumers = set()
        hub.thread = hub.session = None
        hub.media_framing_diagnostics = {}
        hub._v1_doorbell_trial = SimpleNamespace(active=True)
        hub.release = AsyncMock()
        hub._worker = Mock(side_effect=AssertionError('Media worker forbidden'))
        return hub

    async def test_active_trial_rejects_media_before_waiting_for_owned_lock(self):
        hub = self.hub()
        await hub.lock.acquire()
        try:
            with self.assertRaisesRegex(ConnectionError, 'observation is active'):
                await asyncio.wait_for(hub.acquire('viewer'), .1)
        finally:
            hub.lock.release()
        self.assertFalse(hub.consumers)
        self.assertIsNone(hub.thread)
        hub._worker.assert_not_called()
        hub.release.assert_not_called()

    async def test_trial_start_while_media_waits_is_rechecked_inside_lock(self):
        hub = self.hub()
        hub._v1_doorbell_trial.active = False
        await hub.lock.acquire()
        task = asyncio.create_task(hub.acquire('viewer'))
        await asyncio.sleep(0)
        self.assertFalse(task.done())
        hub._v1_doorbell_trial.active = True
        hub.lock.release()
        with self.assertRaisesRegex(ConnectionError, 'observation is active'):
            await task
        self.assertFalse(hub.consumers)
        self.assertIsNone(hub.thread)
        hub._worker.assert_not_called()
        hub.release.assert_not_called()

    async def test_direct_hub_unload_awaits_trial_before_other_cleanup(self):
        hub = self.hub()
        entered, finish = asyncio.Event(), asyncio.Event()
        order = []
        async def close_trial():
            entered.set()
            await finish.wait()
            order.append('trial_closed')
        hub._v1_doorbell_trial.close = AsyncMock(side_effect=close_trial)
        hub.ready, hub.image_event = asyncio.Event(), asyncio.Event()
        hub._stop_task = None
        hub.ring_timer = None
        hub.control = SimpleNamespace(close=Mock(side_effect=lambda: order.append('control_closed')))
        hub.ring_listener = SimpleNamespace(thread=None,
            close=Mock(side_effect=lambda: order.append('ring_closed')))
        hub.ring_image = SimpleNamespace(close=AsyncMock())
        hub.manual_snapshot = SimpleNamespace(close=AsyncMock())
        hub.close_listeners = hub.handlers = set()
        hub.server = None
        hub._halt_media = AsyncMock()
        task = asyncio.create_task(hub.stop())
        await entered.wait()
        self.assertTrue(hub.stopped)
        self.assertFalse(task.done())
        hub.ring_listener.close.assert_not_called()
        hub._halt_media.assert_not_called()
        finish.set()
        await task
        await hub.stop()
        hub._v1_doorbell_trial.close.assert_awaited_once()
        self.assertEqual(order[0], 'trial_closed')
        hub.ring_listener.close.assert_called_once()
        hub._halt_media.assert_awaited_once()
        self.assertFalse(hub.lock.locked())
        self.assertFalse(hub.consumers)


if __name__ == '__main__':
    unittest.main()
