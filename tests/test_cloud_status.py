"""Cloud diagnostic entities: offline HA boundary, real entity/controller code."""
import ast
import asyncio
import json
from types import SimpleNamespace
import unittest
from unittest.mock import Mock

from load_integration import ROOT, load
import test_qv_cloud as qv
import test_v1_cloud as v1

status = load('cloud_status')
caps = load('capabilities')


class Entity:
    """Only the HA lifecycle boundary is replaced in offline tests."""
    async def async_added_to_hass(self):
        self.removers = []

    def async_on_remove(self, callback):
        self.removers.append(callback)


def module(name, **namespace):
    tree = ast.parse((ROOT / f'{name}.py').read_text(encoding='utf-8'))
    tree.body = [node for node in tree.body if not isinstance(node, (ast.Import, ast.ImportFrom))]
    exec(compile(tree, str(ROOT / f'{name}.py'), 'exec'), namespace)
    return SimpleNamespace(**namespace)


base = module('entity', Entity=Entity, DeviceInfo=dict, DOMAIN='welcomeeye_local').WelcomeEyeEntity
common = dict(WelcomeEyeEntity=base, EntityCategory=SimpleNamespace(DIAGNOSTIC='diagnostic'),
              supports_cloud_status=status.supports_cloud_status)
binary = module('binary_sensor', **common, BinarySensorEntity=Entity,
                BinarySensorDeviceClass=SimpleNamespace(CONNECTIVITY='connectivity'),
                RING_HOLD_SECONDS=5, cloud_connected=status.cloud_connected)
sensor = module('sensor', **common, SensorEntity=Entity,
                SensorDeviceClass=SimpleNamespace(ENUM='enum'), HomeAssistantError=RuntimeError,
                SUBSCRIPTION_STATES=status.SUBSCRIPTION_STATES,
                cloud_subscription_status=status.cloud_subscription_status)


def hub(variant, *, enabled=True):
    v1_model = variant == caps.DeviceVariant.V1
    listeners = set()

    def subscribe(callback):
        listeners.add(callback)
        return lambda: listeners.discard(callback)

    entry = SimpleNamespace(unique_id=f'fixture-{variant}', title='Fixture', data={
        'v1_cloud_doorbell_enabled' if v1_model else 'qv_cloud_enabled': enabled})
    cloud = SimpleNamespace(connected=False, subscription_status='not_started',
                            diagnostics=Mock(side_effect=AssertionError('No diagnostic payload export')))
    result = SimpleNamespace(variant=variant, entry=entry, stopped=False, connected=True,
        device_model='Fixture', capabilities=caps.MATRIX[variant], secondary_channel_enabled=True,
        subscribe=subscribe, listeners=listeners, notify=lambda: [cb() for cb in tuple(listeners)])
    setattr(result, 'v1_cloud' if v1_model else 'qv_cloud', cloud)
    return result, cloud


class CloudEntityTests(unittest.IsolatedAsyncioTestCase):
    async def test_platforms_add_one_pair_per_cloud_device_even_if_disabled(self):
        for variant in caps.DeviceVariant:
            for enabled in (True, False):
                with self.subTest(variant=variant, enabled=enabled):
                    device, _ = hub(variant, enabled=enabled)
                    entry = SimpleNamespace(runtime_data=device)
                    entities = []
                    await binary.async_setup_entry(None, entry, entities.extend)
                    await sensor.async_setup_entry(None, entry, entities.extend)
                    pair = [item for item in entities if isinstance(item,
                        (binary.WelcomeEyeCloudConnection, sensor.WelcomeEyeCloudSubscription))]
                    expected = variant in (caps.DeviceVariant.V1, caps.DeviceVariant.R002,
                                           caps.DeviceVariant.CONNECT3)
                    self.assertEqual(len(pair), 2 if expected else 0)
                    for item in pair:
                        self.assertEqual(item._attr_entity_category, 'diagnostic')
                        self.assertFalse(item._attr_should_poll)
                        self.assertTrue(item.available)
                        self.assertTrue(item._attr_unique_id.startswith(device.entry.unique_id + '_cloud_'))

    async def test_updates_disconnects_and_unload_are_independent_of_video_and_rings(self):
        for variant in (caps.DeviceVariant.V1, caps.DeviceVariant.R002, caps.DeviceVariant.CONNECT3):
            device, cloud = hub(variant)
            connection = binary.WelcomeEyeCloudConnection(device)
            subscription = sensor.WelcomeEyeCloudSubscription(device)
            writes = []
            connection.async_write_ha_state = lambda: writes.append(('connection', connection.is_on))
            subscription.async_write_ha_state = lambda: writes.append(('subscription', subscription.native_value))
            await connection.async_added_to_hass()
            await subscription.async_added_to_hass()
            self.assertFalse(connection.is_on)  # Video connected is unrelated.
            self.assertEqual(subscription.native_value, 'not_started')
            cloud.subscription_status = 'confirmed'
            device.notify()
            self.assertFalse(connection.is_on)  # Subscription is not an MCS login.
            self.assertEqual(subscription.native_value, 'confirmed')
            cloud.connected = True
            device.connected = False  # No video needed.
            device.notify()
            self.assertTrue(connection.is_on)
            cloud.connected = False
            device.notify()
            self.assertFalse(connection.is_on)
            self.assertTrue(connection.available)
            self.assertEqual(subscription.native_value, 'confirmed')
            self.assertIn(('connection', True), writes)
            self.assertIn(('connection', False), writes)
            self.assertIn(('subscription', 'confirmed'), writes)
            cloud.diagnostics.assert_not_called()
            device.stopped = True
            self.assertFalse(connection.available)
            self.assertFalse(subscription.available)
            for item in (connection, subscription):
                for remove in item.removers:
                    remove()
            self.assertFalse(device.listeners)
            before = len(writes)
            device.notify()
            self.assertEqual(len(writes), before)

    async def test_disabled_missing_provider_cleanup_unknown_and_recreation(self):
        for variant in (caps.DeviceVariant.V1, caps.DeviceVariant.R002, caps.DeviceVariant.CONNECT3):
            device, cloud = hub(variant, enabled=False)
            cloud.connected = True
            cloud.subscription_status = 'confirmed'
            connection = binary.WelcomeEyeCloudConnection(device)
            subscription = sensor.WelcomeEyeCloudSubscription(device)
            self.assertFalse(connection.is_on)
            self.assertEqual(subscription.native_value, 'disabled')
            cloud.subscription_status = 'cleanup_pending'
            self.assertEqual(subscription.native_value, 'cleanup_pending')
            device.entry.data = {key: True for key in device.entry.data}
            for value in status.SUBSCRIPTION_STATES:
                cloud.subscription_status = value
                self.assertEqual(subscription.native_value, value)
            for value in ('PRIVATE_TOKEN', {'secret': 'PRIVATE'}, None):
                cloud.subscription_status = value
                self.assertEqual(subscription.native_value, 'unknown')
            delattr(device, 'v1_cloud' if variant == caps.DeviceVariant.V1 else 'qv_cloud')
            self.assertFalse(connection.is_on)
            self.assertEqual(subscription.native_value, 'not_started')
            rebuilt, _ = hub(variant)
            self.assertEqual(connection._attr_unique_id, binary.WelcomeEyeCloudConnection(rebuilt)._attr_unique_id)
            self.assertEqual(subscription._attr_unique_id, sensor.WelcomeEyeCloudSubscription(rebuilt)._attr_unique_id)

    def test_all_entity_states_have_translations(self):
        for path in [ROOT / 'strings.json', *(ROOT / 'translations').glob('*.json')]:
            data = json.loads(path.read_text(encoding='utf-8'))['entity']
            self.assertTrue(data['binary_sensor']['cloud_connection']['name'])
            translated = data['sensor']['cloud_subscription']
            self.assertTrue(translated['name'])
            self.assertEqual(set(translated['state']), set(status.SUBSCRIPTION_STATES))
            self.assertTrue(all(translated['state'].values()))


class V1StatusTests(unittest.IsolatedAsyncioTestCase):
    fixture = v1.ControllerTests.fixture

    async def test_subscription_progress_is_pushed_and_does_not_connect_receiver(self):
        controller, receiver, _, _ = self.fixture()
        changes = []
        controller._on_state = lambda: changes.append((controller.subscription_status, controller.connected))
        await controller.start()
        self.addAsyncCleanup(controller.close)
        await asyncio.wait_for(receiver.ready.wait(), 2)
        self.assertEqual(controller.subscription_status, 'confirmed')
        for value in ('registering', 'accepted', 'confirmed'):
            self.assertIn((value, False), changes)
        self.assertIn(('confirmed', True), changes)
        controller._on_ring.assert_not_called()


class QVStatusTests(unittest.IsolatedAsyncioTestCase):
    fixture = qv.QVControllerTests.fixture
    subscription_state = staticmethod(qv.QVControllerTests.subscription_state)

    async def test_both_qv_models_push_subscription_progress_before_receiver_login(self):
        for variant in ('connect2_r002', 'connect3'):
            controller, receiver, _, _, _ = self.fixture(variant=variant)
            changes = []
            controller._on_state = lambda: changes.append((controller.subscription_status, controller.connected))
            await controller.start()
            self.addAsyncCleanup(controller.stop)
            await asyncio.wait_for(receiver.ready.wait(), 2)
            self.assertTrue(controller.connected)
            for value in ('not_started', 'registering', 'accepted', 'confirmed'):
                self.assertIn((value, False), changes)
            controller._on_ring.assert_not_called()
            await controller.stop()


if __name__ == '__main__':
    unittest.main()
