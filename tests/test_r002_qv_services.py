"""R002 explicit QV actions enforce administrator/control boundaries offline."""
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from test_transport_lifecycle import load_source


class HAError(Exception):
    pass


class Entity:
    def __init__(self, hub, key):
        self.hub = hub
        self.key = key


class Sensor:
    pass


services = load_source('services', {'HomeAssistantError': HAError, 'POLICY_CONTROL': 'control'})
sensors = load_source('sensor', {'HomeAssistantError': HAError, 'WelcomeEyeEntity': Entity,
    'SensorEntity': Sensor, 'EntityCategory': SimpleNamespace(DIAGNOSTIC='diagnostic')})


class R002ServicesTests(unittest.IsolatedAsyncioTestCase):
    def fixture(self, service='r002_check_access', **data):
        permissions = SimpleNamespace(check_entity=Mock(return_value=True))
        user = SimpleNamespace(is_admin=True, permissions=permissions)
        auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        entity = SimpleNamespace(entity_id='sensor.synthetic_r002', hass=SimpleNamespace(auth=auth),
            hub=SimpleNamespace(capabilities=SimpleNamespace(r002_qv_read=True, connect3_read=False)),
            async_r002_qv_read=AsyncMock(return_value={'status': 'ok'}),
            async_r002_observe_doorbell=AsyncMock(return_value={'status': 'observing'}),
            async_connect3_read=AsyncMock(side_effect=AssertionError('Connect 3 route forbidden')))
        call = SimpleNamespace(service=service, context=SimpleNamespace(user_id='synthetic_admin'), data=data)
        return entity, call, user

    async def test_access_and_certificate_forward_only_explicit_details(self):
        for name, operation, details in (('r002_check_access', 'access', False),
                ('r002_check_qv_certificate', 'certificate', False),
                ('r002_check_qv_certificate', 'certificate', True)):
            with self.subTest(name=name, details=details):
                entity, call, user = self.fixture(name, **({'include_details': True} if details else {}))
                result = await services._async_r002_qv_read(entity, call)
                self.assertEqual(result, {'status': 'ok'})
                user.permissions.check_entity.assert_called_once_with(entity.entity_id, 'control')
                entity.async_r002_qv_read.assert_awaited_once_with(operation, include_details=details)
                entity.async_r002_observe_doorbell.assert_not_called()
                entity.async_connect3_read.assert_not_called()

    async def test_each_action_requires_identified_admin_with_target_control(self):
        for name in ('r002_check_access', 'r002_check_qv_certificate', 'r002_observe_doorbell'):
            for admin, permission, user_id in ((False, True, 'user'), (True, False, 'user'),
                                             (True, True, None), (True, True, 'deleted_user')):
                with self.subTest(name=name, admin=admin, permission=permission, user_id=user_id):
                    entity, call, user = self.fixture(name, include_details=True, operation='start', duration=90)
                    user.is_admin = admin
                    user.permissions.check_entity.return_value = permission
                    call.context.user_id = user_id
                    if user_id == 'deleted_user':
                        entity.hass.auth.async_get_user.return_value = None
                    with self.assertRaises(HAError):
                        await services._async_r002_qv_read(entity, call)
                    entity.async_r002_qv_read.assert_not_called()
                    entity.async_r002_observe_doorbell.assert_not_called()
                    entity.async_connect3_read.assert_not_called()

    async def test_connect3_and_legacy_targets_rejected_before_permission_or_io(self):
        for capabilities in (None, SimpleNamespace(connect3_read=True),
                SimpleNamespace(r002_qv_read=False, connect3_read=True),
                SimpleNamespace(r002_qv_read=False, connect3_read=False)):
            entity, call, _ = self.fixture()
            entity.hub.capabilities = capabilities
            with self.assertRaises(HAError):
                await services._async_r002_qv_read(entity, call)
            entity.hass.auth.async_get_user.assert_not_called()
            entity.async_r002_qv_read.assert_not_called()
            entity.async_connect3_read.assert_not_called()
        entity, call, _ = self.fixture('connect3_check_access')
        with self.assertRaises(HAError):
            await services._async_connect3_read(entity, call)
        entity.async_connect3_read.assert_not_called()

    async def test_doorbell_action_uses_existing_reader_and_duration(self):
        entity, call, _ = self.fixture('r002_observe_doorbell', operation='start', duration=30)
        result = await services._async_r002_qv_read(entity, call)
        self.assertEqual(result, {'status': 'observing'})
        entity.async_r002_observe_doorbell.assert_awaited_once_with('start', 30)
        entity.async_r002_qv_read.assert_not_called()

    async def test_protocol_sensor_remains_same_entity_and_delegates_without_private_state(self):
        hub = SimpleNamespace(capabilities=SimpleNamespace(r002_qv_read=True),
            execute=AsyncMock(return_value={'certificate_sha256': 'PRIVATE_PIN'}),
            doorbell=SimpleNamespace(execute=Mock(return_value={'control_messages': 1})),
            protocol_family=SimpleNamespace(value='r002_experimental'), detection_confidence='compatible',
            last_probe_at=None, last_probe_status='not_run', last_error_type=None)
        sensor = sensors.WelcomeEyeProtocolStatus(hub)
        self.assertEqual(sensor.key, 'protocol_status')
        result = await sensor.async_r002_qv_read('certificate', include_details=True)
        self.assertEqual(result, {'certificate_sha256': 'PRIVATE_PIN'})
        hub.execute.assert_awaited_once_with('certificate', include_details=True)
        result = await sensor.async_r002_observe_doorbell('mark', 90)
        self.assertEqual(result, {'control_messages': 1})
        hub.doorbell.execute.assert_called_once_with('mark', 90)
        self.assertNotIn('PRIVATE_PIN', repr(sensor.extra_state_attributes))
        self.assertNotIn('certificate_sha256', sensor.extra_state_attributes)

    async def test_sensor_failures_never_expose_network_exception_text(self):
        hub = SimpleNamespace(capabilities=SimpleNamespace(r002_qv_read=True),
            execute=AsyncMock(side_effect=ValueError('PRIVATE_PASSWORD')),
            doorbell=SimpleNamespace(execute=Mock(side_effect=RuntimeError('PRIVATE_PACKET'))))
        sensor = sensors.WelcomeEyeProtocolStatus(hub)
        for method, args in ((sensor.async_r002_qv_read, ('access',)),
                             (sensor.async_r002_observe_doorbell, ('start', 90))):
            with self.assertRaises(HAError) as error:
                await method(*args)
            self.assertNotIn('PRIVATE', str(error.exception))
            self.assertTrue(error.exception.__suppress_context__)
        hub.capabilities.r002_qv_read = False
        hub.execute.reset_mock()
        hub.doorbell.execute.reset_mock()
        with self.assertRaises(HAError):
            await sensor.async_r002_qv_read('access')
        with self.assertRaises(HAError):
            await sensor.async_r002_observe_doorbell('start')
        hub.execute.assert_not_called()
        hub.doorbell.execute.assert_not_called()
