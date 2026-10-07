"""V1 cloud opt-in and HA hooks with no device, cloud, media or output I/O."""
import asyncio
import ast
import json
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock, patch

from load_integration import cap, ROOT
from test_connect3_config import flow
from test_hub_lifecycle import Hub, scope


class V1CloudConfigTests(unittest.IsolatedAsyncioTestCase):
    def configured(self, variant=cap.DeviceVariant.V1, **updates):
        instance = flow()
        entry = SimpleNamespace(entry_id='v1-entry', unique_id='PRIVATE_UID', title='My intercom',
            options={'ring_image_capture': False}, data={
                'host': '192.0.2.20', 'username': 'LOCAL_USER', 'password': 'LOCAL_PASSWORD',
                'protocol_family': cap.family_for(variant), 'device_variant': variant,
                **updates})
        instance._get_reconfigure_entry = lambda: entry
        return instance, entry

    async def test_enable_disable_in_place_changes_only_cloud_option(self):
        instance, entry = self.configured()
        before = dict(entry.data)
        form = await instance.async_step_reconfigure()
        self.assertEqual(form['step_id'], 'v1_cloud_reconfigure')
        self.assertEqual(set(form['data_schema']), {'v1_cloud_doorbell_enabled'})
        self.assertNotIn('LOCAL_', repr(form))
        for enabled in (True, False):
            result = await instance.async_step_v1_cloud_reconfigure({
                'v1_cloud_doorbell_enabled': enabled, 'password': 'INJECTED'})
            self.assertEqual(result['reason'], 'reconfigure_successful')
            self.assertEqual(entry.data, {**before, 'v1_cloud_doorbell_enabled': enabled})
            self.assertEqual(entry.unique_id, 'PRIVATE_UID')
            self.assertEqual(entry.title, 'My intercom')
            self.assertEqual(entry.options, {'ring_image_capture': False})
        instance.hass.async_add_executor_job.assert_not_called()

    async def test_missing_option_defaults_off_and_non_bool_rejected(self):
        instance, entry = self.configured()
        await instance.async_step_v1_cloud_reconfigure({})
        self.assertIs(entry.data['v1_cloud_doorbell_enabled'], False)
        for value in ('true', 1, None, [], {}):
            result = await instance.async_step_v1_cloud_reconfigure({'v1_cloud_doorbell_enabled': value})
            self.assertEqual(result['errors']['base'], 'invalid_v1_cloud_config')
            self.assertIs(entry.data['v1_cloud_doorbell_enabled'], False)

    async def test_direct_step_rejects_other_families(self):
        for variant in cap.DeviceVariant:
            if variant == cap.DeviceVariant.V1:
                continue
            instance, entry = self.configured(variant)
            before = dict(entry.data)
            result = await instance.async_step_v1_cloud_reconfigure({'v1_cloud_doorbell_enabled': True})
            self.assertEqual(result['type'], 'abort')
            self.assertEqual(entry.data, before)
            instance.hass.async_add_executor_job.assert_not_called()


class V1CloudHubTests(unittest.IsolatedAsyncioTestCase):
    def hub(self, variant=cap.DeviceVariant.V1, enabled=None):
        hub = Hub.__new__(Hub)
        hub.entry = SimpleNamespace(entry_id='v1-entry', unique_id='PRIVATE_UID', data={
            'protocol_family': cap.family_for(variant), 'device_variant': variant})
        if enabled is not None:
            hub.entry.data['v1_cloud_doorbell_enabled'] = enabled
        hub.device_model = 'WelcomeEye Connect V1'
        hub.hass = SimpleNamespace(bus=SimpleNamespace(async_fire=Mock()))
        hub.loop = asyncio.get_running_loop()
        hub.path = '/fixture/live.ts'
        hub.stopped = True
        hub._stop_task = None
        hub.v1_cloud = None
        hub._v1_cloud_uid = hub.entry.unique_id
        hub.ring_listener = SimpleNamespace(start=Mock(), close=Mock(), thread=None)
        hub.ringing = hub.ring_connected = False
        hub.ring_error = hub.ring_timer = None
        hub.ring_count = 0
        hub.ring_image = SimpleNamespace(request=Mock(), close=AsyncMock())
        hub.manual_snapshot = SimpleNamespace(close=AsyncMock())
        hub.control = SimpleNamespace(close=Mock(), unlock=Mock())
        hub._notify = Mock()
        hub.acquire = AsyncMock()
        hub._halt_media = AsyncMock()
        hub.ready = asyncio.Event()
        hub.image_event = asyncio.Event()
        hub.lock = asyncio.Lock()
        hub.handlers = set()
        hub.close_listeners = set()
        hub.consumers = set()
        return hub

    async def start(self, hub, cloud):
        server = SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ('127.0.0.1', 123))],
            close=Mock(), wait_closed=AsyncMock())
        with patch.dict(scope, V1CloudDoorbell=cloud, RING_HOLD_SECONDS=5):
            with patch.object(asyncio, 'start_server', AsyncMock(return_value=server)):
                await hub.start()

    async def test_no_cloud_constructor_without_strict_v1_opt_in(self):
        for variant in cap.DeviceVariant:
            for enabled in (None, False, 'true', 1, True):
                if variant == cap.DeviceVariant.V1 and type(enabled) is bool:
                    continue
                hub = self.hub(variant, enabled)
                factory = Mock(side_effect=AssertionError('Cloud must not start'))
                await self.start(hub, factory)
                factory.assert_not_called()
                self.assertFalse(hub.capabilities.cloud_ring)
                if variant != cap.DeviceVariant.R001:
                    hub.ring_listener.start.assert_not_called()

    async def test_explicit_disabled_v1_only_retries_pending_cleanup(self):
        hub = self.hub(enabled=False)
        cloud = SimpleNamespace(start=AsyncMock(), cleanup_pending=AsyncMock(), close=AsyncMock(),
            connected=False, diagnostics=lambda: {'status': 'disabled', 'subscription_status': 'cleanup_pending'})
        await self.start(hub, Mock(return_value=cloud))
        cloud.start.assert_not_awaited()
        cloud.cleanup_pending.assert_awaited_once()
        self.assertFalse(hub.capabilities.cloud_ring)
        self.assertEqual(hub.v1_cloud_diagnostics()['subscription_status'], 'cleanup_pending')
        hub._cloud_ring()
        hub.hass.bus.async_fire.assert_not_called()
        await hub.stop()
        cloud.close.assert_awaited_once()

    async def test_cloud_event_only_pulses_and_never_uses_media_photo_or_outputs(self):
        hub = self.hub(enabled=True)
        cloud = SimpleNamespace(start=AsyncMock(), close=AsyncMock(), connected=True,
            diagnostics=lambda: {'status': 'connected', 'received_count': 1})
        factory = Mock(return_value=cloud)
        await self.start(hub, factory)
        factory.assert_called_once_with(hub.hass, hub.entry, hub._cloud_ring, on_state=hub._cloud_state)
        hub.ring_listener.start.assert_not_called()
        self.assertFalse(hub.capabilities.local_ring)
        self.assertFalse(hub.capabilities.ring_image_capture)
        self.assertTrue(hub.capabilities.cloud_ring)
        with patch.dict(scope, RING_HOLD_SECONDS=5):
            hub._cloud_ring(2)
        hub.hass.bus.async_fire.assert_called_once_with('welcomeeye_local.ring', {
            'entry_id': 'v1-entry', 'channel': 2, 'ring_sequence': 1, 'source': 'cloud'})
        self.assertTrue(hub.ringing)
        hub.acquire.assert_not_awaited()
        hub.ring_image.request.assert_not_called()
        hub.control.unlock.assert_not_called()
        self.assertNotIn('PRIVATE_UID', json.dumps(hub.v1_cloud_diagnostics()))
        await hub.stop()
        cloud.close.assert_awaited_once()
        self.assertFalse(hub.ringing)
        hub._notify.reset_mock()
        hub._cloud_ring()
        hub._cloud_state()
        self.assertEqual(hub.ring_count, 1)
        hub._notify.assert_not_called()
        await hub.stop()
        cloud.close.assert_awaited_once()

    async def test_invalid_cloud_channels_do_not_emit_events(self):
        hub = self.hub(enabled=True)
        hub.stopped = False
        for channel in (None, False, True, 0, -1, 257, '2', 2.0):
            hub._cloud_ring(channel)
        hub.hass.bus.async_fire.assert_not_called()
        hub._notify.assert_not_called()
        self.assertEqual(hub.ring_count, 0)
        self.assertIsNone(hub.ring_timer)

    async def test_changed_uid_disabled_option_or_changed_variant_blocks_callback(self):
        for change in ('uid', 'option', 'variant'):
            hub = self.hub(enabled=True)
            hub.stopped = False
            if change == 'uid':
                hub.entry.unique_id = 'OTHER_UID'
            elif change == 'option':
                hub.entry.data['v1_cloud_doorbell_enabled'] = False
            else:
                hub.entry.data['device_variant'] = cap.DeviceVariant.R001
            hub._cloud_ring()
            hub._cloud_state()
            hub.hass.bus.async_fire.assert_not_called()
            hub._notify.assert_not_called()

    async def test_cloud_start_failure_keeps_local_hub_started(self):
        hub = self.hub(enabled=True)
        cloud = SimpleNamespace(start=AsyncMock(side_effect=RuntimeError('PRIVATE_RAW_ERROR')),
            close=AsyncMock(), connected=False, diagnostics=lambda: {'status': 'failed'})
        await self.start(hub, Mock(return_value=cloud))
        self.assertFalse(hub.stopped)
        self.assertTrue(hub.capabilities.live_media)
        report = hub.v1_cloud_diagnostics()
        self.assertEqual(report['startup_error_type'], 'RuntimeError')
        self.assertNotIn('PRIVATE_RAW_ERROR', json.dumps(report))
        await hub.stop()
        cloud.close.assert_awaited_once()

    async def test_local_shutdown_finishes_before_slow_cloud_unsubscription(self):
        hub = self.hub(enabled=True)
        entered, released = asyncio.Event(), asyncio.Event()

        async def cloud_close():
            self.assertTrue(hub.stopped)
            hub.control.close.assert_called()
            hub.ring_listener.close.assert_called_once()
            hub._halt_media.assert_awaited_once()
            hub.ring_image.close.assert_awaited_once()
            self.assertFalse(hub.ringing)
            self.assertIsNone(hub.server)
            entered.set()
            await released.wait()

        cloud = SimpleNamespace(start=AsyncMock(), close=AsyncMock(side_effect=cloud_close),
            connected=True, diagnostics=lambda: {'status': 'connected'})
        await self.start(hub, Mock(return_value=cloud))
        stop = asyncio.create_task(hub.stop())
        try:
            await asyncio.wait_for(entered.wait(), 1)
            hub._cloud_ring()
            hub.hass.bus.async_fire.assert_not_called()
        finally:
            released.set()
            await stop

    async def test_registry_preserves_cloud_ring_but_removes_capture_entities(self):
        hub = self.hub(enabled=True)
        entries = [SimpleNamespace(entity_id=f'{domain}.{suffix}', domain=domain,
            unique_id=f'PRIVATE_UID_{suffix}', config_entry_id='v1-entry', platform='welcomeeye_local')
            for domain, suffix in [('binary_sensor', 'ring'), ('switch', 'ring_image_capture'),
                                   ('image', 'last_ring')]]
        self.assertEqual(set(cap.unsupported_entity_ids(entries, 'v1-entry', 'PRIVATE_UID', hub.capabilities)),
                         {'switch.ring_image_capture', 'image.last_ring'})
        self.assertFalse(cap.MATRIX[cap.DeviceVariant.V1].cloud_ring)

    async def test_binary_sensor_cloud_availability_tracks_cloud_not_video(self):
        tree = ast.parse((ROOT / 'binary_sensor.py').read_text())
        cls = next(node for node in tree.body if isinstance(node, ast.ClassDef) and node.name == 'WelcomeEyeRing')
        methods = [node for node in cls.body if isinstance(node, ast.FunctionDef)
                   and node.name in ('available', 'is_on', 'extra_state_attributes')]
        body = ast.ClassDef(name='Sensor', bases=[], keywords=[], body=methods, decorator_list=[])
        namespace = {'RING_HOLD_SECONDS': 5}
        exec(compile(ast.fix_missing_locations(ast.Module(body=[body], type_ignores=[])), '<sensor>', 'exec'), namespace)
        sensor = namespace['Sensor']()
        sensor.hub = self.hub(enabled=True)
        sensor.hub.stopped = False
        sensor.hub.v1_cloud = SimpleNamespace(connected=True)
        self.assertTrue(sensor.available)
        self.assertEqual(sensor.extra_state_attributes, {'ring_hold_seconds': 5, 'listener_mode': 'v1_cloud'})
        sensor.hub.v1_cloud.connected = False
        self.assertFalse(sensor.available)
