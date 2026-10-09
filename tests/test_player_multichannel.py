"""Owned WebSocket stops and registry-bound channel discovery; no device I/O."""
import ast
import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock
import uuid

from load_integration import cap, PACKAGE, ROOT
from test_transport_lifecycle import load_source


class HomeAssistantError(Exception):
    pass


def player(cameras, allowed=None):
    allowed = allowed if allowed is not None else set(cameras) | {'button.primary_strike', 'button.primary_gate',
        'button.renamed_secondary_strike', 'button.renamed_secondary_gate'}
    registry = Mock()
    identities = {('camera', 'welcomeeye_local', camera._attr_unique_id): entity_id
                  for entity_id, camera in cameras.items()}
    identities.update({('button', 'welcomeeye_local', 'primary_open_output_1'): 'button.primary_strike',
                       ('button', 'welcomeeye_local', 'primary_open_output_2'): 'button.primary_gate',
                       ('button', 'welcomeeye_local', 'primary_open_channel_2_output_1'): 'button.renamed_secondary_strike',
                       ('button', 'welcomeeye_local', 'primary_open_channel_2_output_2'): 'button.renamed_secondary_gate'})
    registry.async_get_entity_id.side_effect = lambda *args: identities.get(args)
    def find(hass, entity_id):
        if entity_id not in cameras:
            raise HomeAssistantError('Unavailable')
        return cameras[entity_id]
    vol = SimpleNamespace(Required=lambda name: name, All=lambda *args: str, Length=lambda **kw: str)
    ws = SimpleNamespace(websocket_command=lambda schema: lambda function: function,
                         async_response=lambda function: function)
    module = load_source('player', dict(__package__=PACKAGE, asyncio=asyncio, uuid=uuid,
        vol=vol, websocket_api=ws, cv=SimpleNamespace(entity_id=str),
        callback=lambda function: function, POLICY_READ='read', POLICY_CONTROL='control',
        er=SimpleNamespace(async_get=lambda hass: registry), get_camera_from_entity_id=find,
        HomeAssistantError=HomeAssistantError, DOMAIN='welcomeeye_local'))
    connection = SimpleNamespace(subscriptions={}, send_result=Mock(), send_error=Mock(),
        send_event=Mock(), user=SimpleNamespace(permissions=SimpleNamespace(
            check_entity=lambda entity_id, policy: entity_id in allowed)))
    return module, connection, registry


def cameras():
    entry = SimpleNamespace(domain='welcomeeye_local', entry_id='same-entry', unique_id='primary', data={})
    main = SimpleNamespace(entry=entry, capabilities=cap.connect3_capabilities(True, True),
                           supports_multichannel_player=True,
                           confirmed_media_channels=frozenset((1, 2)),
                           control=SimpleNamespace(target_enabled=lambda target: True))
    secondary = SimpleNamespace(entry=entry, parent=main, capabilities=cap.DeviceCapabilities(
        camera=True, live_media=True, downstream_audio=True))
    result = {}
    for number, hub, suffix, entity_id in ((1, main, 'camera', 'camera.renamed_primary'),
            (2, secondary, 'camera_channel_2', 'camera.arbitrary_secondary')):
        result[entity_id] = SimpleNamespace(hub=hub, _welcomeeye_channel=number, entity_id=entity_id,
            _attr_unique_id=f'primary_{suffix}', rtc=SimpleNamespace(offer=AsyncMock(), close=AsyncMock()),
            async_get_webrtc_client_configuration=lambda: SimpleNamespace(to_frontend_dict=lambda: {'configuration': {}}))
    return result


class PlayerChannelsTests(unittest.IsolatedAsyncioTestCase):
    async def test_renamed_channels_resolved_by_registry_and_controls_stay_primary(self):
        devices = cameras()
        module, connection, _ = player(devices)
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.arbitrary_secondary'})
        config = connection.send_result.call_args.args[1]
        self.assertEqual(config['channels'], [
            {'channel': 1, 'entity_id': 'camera.renamed_primary', 'label': 'Entrée 1', 'confirmed': True},
            {'channel': 2, 'entity_id': 'camera.arbitrary_secondary', 'label': 'Entrée 2', 'confirmed': True}])
        self.assertEqual(config['primary_entity_id'], 'camera.renamed_primary')
        self.assertEqual(config['buttons'], {'strike': 'button.primary_strike', 'gate': 'button.primary_gate'})
        self.assertFalse(config['microphone_allowed'])
        self.assertTrue(config['stop_supported'])
        self.assertEqual(config['outputs'], {
            'strike_1': {'entity_id': 'button.primary_strike', 'channel': 1, 'output': 1, 'validation_status': 'existing'},
            'gate_1': {'entity_id': 'button.primary_gate', 'channel': 1, 'output': 2, 'validation_status': 'existing'}})
        for camera in devices.values():
            camera.rtc.offer.assert_not_called()

    async def test_secondary_outputs_require_each_explicit_trial_and_are_never_claimed_validated(self):
        devices = cameras()
        primary = devices['camera.renamed_primary'].hub
        primary.entry.data.update(channel2_strike_trial_enabled=True, channel2_gate_trial_enabled=False)
        module, connection, _ = player(devices)
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        result = connection.send_result.call_args.args[1]
        self.assertEqual(result['outputs']['strike_2'], {'entity_id': 'button.renamed_secondary_strike',
            'channel': 2, 'output': 1, 'validation_status': 'hardware_pending'})
        self.assertNotIn('gate_2', result['outputs'])
        self.assertEqual(result['buttons']['strike'], 'button.primary_strike')
        primary.entry.data['channel2_gate_trial_enabled'] = True
        await module.player_config(None, connection, {'id': 2, 'entity_id': 'camera.arbitrary_secondary'})
        self.assertEqual(connection.send_result.call_args.args[1]['outputs']['gate_2'],
            {'entity_id': 'button.renamed_secondary_gate', 'channel': 2, 'output': 2, 'validation_status': 'hardware_pending'})
        primary.control.target_enabled = lambda target: False
        await module.player_config(None, connection, {'id': 3, 'entity_id': 'camera.arbitrary_secondary'})
        self.assertEqual(set(connection.send_result.call_args.args[1]['outputs']), {'strike_1', 'gate_1'})

    async def test_secondary_output_mapping_obeys_camera_reads_control_and_each_family_policy(self):
        devices = cameras()
        primary = devices['camera.renamed_primary'].hub
        primary.entry.data.update(channel2_strike_trial_enabled=True, channel2_gate_trial_enabled=True)
        all_allowed = set(devices) | {'button.primary_strike', 'button.primary_gate',
            'button.renamed_secondary_strike', 'button.renamed_secondary_gate'}
        for denied in ('camera.renamed_primary', 'camera.arbitrary_secondary', 'button.renamed_secondary_strike'):
            with self.subTest(denied=denied):
                module, connection, _ = player(devices, all_allowed - {denied})
                selected = 'camera.arbitrary_secondary' if denied == 'camera.renamed_primary' else 'camera.renamed_primary'
                await module.player_config(None, connection, {'id': 1, 'entity_id': selected})
                self.assertNotIn('strike_2', connection.send_result.call_args.args[1]['outputs'])
        for caps in (cap.r002_capabilities(True, True), cap.DeviceCapabilities(camera=True, strike=True, gate=True)):
            primary.capabilities = caps
            module, connection, _ = player(devices)
            await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
            self.assertEqual(set(connection.send_result.call_args.args[1]['outputs']),
                             {'strike_1', 'gate_1', 'strike_2', 'gate_2'})
            primary.control.target_enabled = lambda target: False
            await module.player_config(None, connection, {'id': 2, 'entity_id': 'camera.renamed_primary'})
            self.assertEqual(set(connection.send_result.call_args.args[1]['outputs']), {'strike_1', 'gate_1'})
            primary.control.target_enabled = lambda target: True
        primary.capabilities = cap.connect3_capabilities(True, True)
        for value in (1, 'true', None):
            primary.entry.data.update(channel2_strike_trial_enabled=value, channel2_gate_trial_enabled=value)
            module, connection, _ = player(devices)
            await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
            self.assertEqual(set(connection.send_result.call_args.args[1]['outputs']), {'strike_1', 'gate_1'})

    async def test_permissions_filter_channels_and_buttons_without_cross_entry_match(self):
        devices = cameras()
        module, connection, _ = player(devices, {'camera.arbitrary_secondary'})
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.arbitrary_secondary'})
        config = connection.send_result.call_args.args[1]
        self.assertEqual([value['channel'] for value in config['channels']], [2])
        self.assertEqual(config['buttons'], {'strike': None, 'gate': None})
        self.assertIsNone(config['primary_entity_id'])
        module, connection, _ = player(devices)
        devices['camera.arbitrary_secondary'].hub = SimpleNamespace(
            entry=SimpleNamespace(entry_id='different-entry', domain='welcomeeye_local'), parent=devices['camera.renamed_primary'].hub)
        await module.player_config(None, connection, {'id': 2, 'entity_id': 'camera.renamed_primary'})
        self.assertEqual(len(connection.send_result.call_args.args[1]['channels']), 1)

    async def test_legacy_settings_retain_existing_control_contract(self):
        devices = cameras()
        devices.pop('camera.arbitrary_secondary')
        del devices['camera.renamed_primary']._welcomeeye_channel
        module, connection, _ = player(devices)
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        config = connection.send_result.call_args.args[1]
        self.assertEqual(config['channels'], [])
        self.assertTrue(config['microphone_allowed'])
        self.assertEqual(config['buttons']['strike'], 'button.primary_strike')

    async def test_r002_shared_camera_class_does_not_expose_connect3_channels(self):
        devices = cameras()
        devices.pop('camera.arbitrary_secondary')
        devices['camera.renamed_primary'].hub.capabilities = cap.r002_capabilities(True, True)
        devices['camera.renamed_primary'].hub.supports_multichannel_player = False
        module, connection, _ = player(devices)
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.renamed_primary'})
        config = connection.send_result.call_args.args[1]
        self.assertEqual(config['channels'], [])
        self.assertIsNone(config['buttons_channel'])
        self.assertTrue(config['microphone_allowed'])

    async def test_explicit_legacy_support_uses_same_registry_mapping_without_connect3(self):
        devices = cameras()
        parent = devices['camera.renamed_primary'].hub
        parent.capabilities = cap.DeviceCapabilities(camera=True, live_media=True,
            downstream_audio=True, talkback=True, strike=True, gate=True)
        parent.confirmed_media_channels = frozenset((1,))
        module, connection, _ = player(devices)
        await module.player_config(None, connection, {'id': 1, 'entity_id': 'camera.arbitrary_secondary'})
        config = connection.send_result.call_args.args[1]
        self.assertEqual([item['channel'] for item in config['channels']], [1, 2])
        self.assertEqual([item['confirmed'] for item in config['channels']], [True, False])
        self.assertEqual(config['primary_entity_id'], 'camera.renamed_primary')
        self.assertEqual(config['buttons']['strike'], 'button.primary_strike')
        self.assertFalse(config['microphone_allowed'])
        parent.supports_multichannel_player = 1
        await module.player_config(None, connection, {'id': 2, 'entity_id': 'camera.renamed_primary'})
        self.assertEqual(connection.send_result.call_args.args[1]['channels'], [])
        del parent.supports_multichannel_player
        await module.player_config(None, connection, {'id': 3, 'entity_id': 'camera.renamed_primary'})
        self.assertEqual(connection.send_result.call_args.args[1]['channels'], [])

    async def test_owned_stop_waits_for_media_cleanup_and_never_stops_other_socket(self):
        devices = cameras()
        module, connection, _ = player(devices)
        rtc = devices['camera.renamed_primary'].rtc
        entered, release, closing = asyncio.Event(), asyncio.Event(), asyncio.Event()
        async def offer(*args, **kwargs):
            entered.set()
            await asyncio.Future()
        async def close(*args):
            closing.set()
            await release.wait()
        rtc.offer.side_effect, rtc.close.side_effect = offer, close
        running = asyncio.create_task(module.player_offer(None, connection,
            {'id': 10, 'entity_id': 'camera.renamed_primary', 'offer': 'SYNTHETIC'}))
        await entered.wait()
        token = connection.send_event.call_args.args[1]['session_id']
        stop = {'id': 11, 'entity_id': 'camera.renamed_primary', 'session_id': token}
        _, stranger, _ = player(devices)
        await module.player_stop(None, stranger, stop)
        stranger.send_error.assert_called_once()
        rtc.close.assert_not_called()
        stopping = asyncio.create_task(module.player_stop(None, connection, stop))
        await closing.wait()
        self.assertFalse(stopping.done())
        self.assertEqual(connection.send_result.call_count, 1)
        release.set()
        await asyncio.gather(running, stopping)
        connection.send_result.assert_called_with(11, {'stopped': True})
        await module.player_stop(None, connection, {**stop, 'id': 12})
        connection.subscriptions[10]()
        await asyncio.sleep(0)
        self.assertEqual(rtc.close.await_count, 1)

    async def test_failed_cleanup_never_acknowledges_release(self):
        devices = cameras()
        module, connection, _ = player(devices)
        devices['camera.renamed_primary'].rtc.close.side_effect = RuntimeError('PRIVATE_NETWORK_ERROR')
        await module.player_offer(None, connection, {'id': 5, 'entity_id': 'camera.renamed_primary', 'offer': 'SYNTHETIC'})
        token = connection.send_event.call_args.args[1]['session_id']
        await module.player_stop(None, connection, {'id': 6, 'entity_id': 'camera.renamed_primary', 'session_id': token})
        connection.send_error.assert_called_with(6, 'cleanup_failed', 'Fermeture du lecteur non confirmée')
        self.assertNotIn('PRIVATE_NETWORK_ERROR', repr(connection.send_error.call_args))
        self.assertEqual(connection.send_result.call_count, 1)

    async def test_revoked_read_permission_cannot_stop_another_viewer(self):
        devices = cameras()
        allowed = {'camera.renamed_primary'}
        module, connection, _ = player(devices, allowed)
        await module.player_offer(None, connection, {'id': 5, 'entity_id': 'camera.renamed_primary', 'offer': 'SYNTHETIC'})
        token = connection.send_event.call_args.args[1]['session_id']
        allowed.clear()
        await module.player_stop(None, connection, {'id': 6, 'entity_id': 'camera.renamed_primary', 'session_id': token})
        devices['camera.renamed_primary'].rtc.close.assert_not_called()
        connection.send_error.assert_called_with(6, 'unauthorized', 'Caméra inaccessible')
        connection.subscriptions[5]()
        await asyncio.sleep(0)


class RTCCleanupTests(unittest.IsolatedAsyncioTestCase):
    async def test_explicit_stop_joins_cleanup_already_started_by_ice_callback(self):
        source = ast.parse((ROOT / 'rtc.py').read_text(encoding='utf-8'))
        original = next(item for item in source.body if isinstance(item, ast.ClassDef) and item.name == 'WebRTCManager')
        selected = [item for item in original.body if isinstance(item, (ast.FunctionDef, ast.AsyncFunctionDef))
                    and item.name in ('__init__', 'close')]
        original.body = selected
        namespace = {'asyncio': asyncio}
        exec(compile(ast.Module(body=[original], type_ignores=[]), 'rtc-cleanup-fixture', 'exec'), namespace)
        manager = namespace['WebRTCManager'].__new__(namespace['WebRTCManager'])
        manager._frame, manager.close_all = lambda *args: None, AsyncMock()
        manager.__init__(SimpleNamespace(frame_listeners=set(), close_listeners=set()))
        manager._diag, manager._cleanup_done = Mock(), Mock()
        entered, finish = asyncio.Event(), asyncio.Event()
        async def close_viewer(viewer, caller):
            entered.set()
            await finish.wait()
        manager._close_viewer = close_viewer
        viewer = object()
        manager.viewers['owned'] = viewer
        ice_close = asyncio.create_task(manager.close('owned', expected=viewer))
        await entered.wait()
        self.assertNotIn('owned', manager.viewers)
        acknowledged_stop = asyncio.create_task(manager.close('owned'))
        await asyncio.sleep(0)
        self.assertFalse(acknowledged_stop.done())
        finish.set()
        await asyncio.gather(ice_close, acknowledged_stop)


if __name__ == '__main__':
    unittest.main()
