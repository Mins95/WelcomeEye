"""Actual HA command preferences/metadata and honest no-I/O alarm preflight.

All entries and permissions are synthetic. No hub is started and no physical
command boundary is used. The integration's actual flow/entity/service APIs run.
"""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + '.' + name) for name in (
            'config_flow', 'button', 'hub', 'connect3.hub', 'r002.hub', 'player', 'sensor', 'services', 'client')}
        from homeassistant.components import sensor as ha_sensor
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        summary = []
        permission = SimpleNamespace(check_entity=Mock(return_value=True))
        user = SimpleNamespace(id='synthetic-admin', is_admin=True, permissions=permission)
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio, 'open_connection', side_effect=AssertionError('device TCP forbidden')))
            stack.enter_context(patch.object(modules['client'].Session, 'connect', side_effect=AssertionError('device LT forbidden')))
            stack.enter_context(patch.object(hass.config_entries, 'async_reload', AsyncMock()))
            for index, variant in enumerate(('connect_v1', 'connect2_r001', 'connect2_r002', 'connect3'), 1):
                data = dict(host=f'192.0.2.{index}', username='SYNTHETIC_USERNAME', password='SYNTHETIC_PASSWORD',
                    auth_code='SYNTHETIC_AUTH', opening_code='SYNTHETIC_OPENING', certificate_sha256='a'*64,
                    device_variant=variant, protocol_family=('connect3_qv_experimental' if variant == 'connect3'
                        else 'r002_experimental' if variant == 'connect2_r002' else 'legacy_owsp'),
                    experimental_video=True, experimental_outputs=True, second_channel_enabled=True,
                    channel2_strike_trial_enabled=True, channel2_gate_trial_enabled=True)
                entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                    title='Synthetic RC3 '+variant, unique_id='synthetic-rc3-'+variant, data=data,
                    options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
                hass.config_entries._entries[entry.entry_id] = entry
                flow = modules['config_flow'].WelcomeEyeConfigFlow()
                flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
                form = await flow.async_step_reconfigure()
                assert any(str(field) == 'opening_commands' for field in form['data_schema'].schema)
                before = deepcopy(dict(entry.data))
                result = await flow.async_step_reconfigure({'opening_commands': {
                    'strike_1_enabled': False, 'gate_1_enabled': True,
                    'strike_2_enabled': True, 'gate_2_enabled': False}})
                assert result['type'] == 'abort' and result['reason'] == 'reconfigure_successful', result
                for key, value in before.items():
                    assert entry.data[key] == value
                cls = modules['connect3.hub'].Connect3Hub if variant == 'connect3' else (
                    modules['r002.hub'].R002InvestigationHub if variant == 'connect2_r002'
                    else modules['hub'].WelcomeEyeHub)
                hub = cls(hass, entry)
                hub.stopped = False
                entry.runtime_data = hub
                created = []
                await modules['button'].async_setup_entry(hass, entry, created.extend)
                assert {item.extra_state_attributes['welcomeeye_output_target'] for item in created} == {'gate_1', 'strike_2'}
                for item in created:
                    item.hass = hass
                    registered = registry.async_get_or_create('button', 'welcomeeye_local', item.unique_id, config_entry=entry)
                    item.entity_id = registered.entity_id
                cameras = {}
                for channel, suffix in ((1, 'camera'), (2, 'camera_channel_2')):
                    registered = registry.async_get_or_create('camera', 'welcomeeye_local', f'{entry.unique_id}_{suffix}', config_entry=entry)
                    owner = hub if channel == 1 else hub.channel2
                    assert owner is not None
                    cameras[registered.entity_id] = SimpleNamespace(hub=owner, _welcomeeye_channel=channel,
                        entity_id=registered.entity_id, async_get_webrtc_client_configuration=lambda: SimpleNamespace(
                            to_frontend_dict=lambda: {}))
                results = []
                connection = SimpleNamespace(user=user, send_result=lambda _, value: results.append(value), send_error=Mock())
                with patch.object(modules['player'], 'camera_for', side_effect=lambda _, __, entity_id: cameras.get(entity_id)):
                    for entity_id in cameras:
                        modules['player'].player_config(hass, connection, {'id': 1, 'entity_id': entity_id})
                        await hass.async_block_till_done(wait_background_tasks=True)
                        assert set(results[-1]['outputs']) == {'gate_1', 'strike_2'}
                        assert (results[-1]['outputs']['gate_1']['channel'], results[-1]['outputs']['gate_1']['output']) == (1, 2)
                        assert (results[-1]['outputs']['strike_2']['channel'], results[-1]['outputs']['strike_2']['output']) == (2, 1)
                    permission.check_entity.side_effect = lambda entity_id, _: entity_id in cameras
                    modules['player'].player_config(hass, connection, {'id': 2, 'entity_id': next(iter(cameras))})
                    await hass.async_block_till_done(wait_background_tasks=True)
                    assert results[-1]['outputs'] == {}
                    permission.check_entity.side_effect = None
                identities = {item.unique_id: item.entity_id for item in created}
                hass.config_entries.async_update_entry(entry, data={**entry.data, 'second_channel_enabled': False})
                assert not hub.control.target_enabled('strike_2')
                assert not next(item for item in created if item.extra_state_attributes['welcomeeye_output_target'] == 'strike_2').available
                for unique_id, entity_id in identities.items():
                    assert registry.async_get_entity_id('button', 'welcomeeye_local', unique_id) == entity_id
                summary.append({'variant': variant, 'independent_commands': 'pass', 'same_targets_on_both_views': 'pass',
                    'disabled_targets_and_permissions': 'pass', 'registry_identity_preserved': 'pass'})

                if variant == 'connect3':
                    assert await ha_sensor.async_setup(hass, {})
                    component = hass.data[ha_sensor.DATA_COMPONENT]
                    sensor = modules['sensor'].WelcomeEyeConnect3Status(hub)
                    sensor.hass = hass
                    sensor.entity_id = registry.async_get_or_create('sensor', 'welcomeeye_local', sensor.unique_id, config_entry=entry).entity_id
                    sensor.async_write_ha_state = Mock()
                    component._platforms['sensor'].entities[sensor.entity_id] = sensor
                    component._entities[sensor.entity_id] = sensor
                    hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor}}
                    modules['services'].async_setup_connect3_services(hass)
                    async def listen(operation):
                        return await hass.services.async_call('welcomeeye_local', 'connect3_listen_doorbell',
                            {'entity_id': sensor.entity_id, 'operation': operation, 'duration': 90},
                            blocking=True, return_response=True, context=Context(user_id=user.id))
                    user.is_admin = False
                    try:
                        await listen('start')
                    except HomeAssistantError:
                        pass
                    else:
                        raise AssertionError('Autonomous diagnostic permission bypassed')
                    assert hub.autonomous_doorbell.start_attempts == 0
                    user.is_admin = True
                    for operation in ('start', 'status', 'stop'):
                        response = next(iter((await listen(operation)).values()))
                        assert response['status'] == 'unavailable'
                        assert response['last_error_reason'] == 'native_login_protocol_unestablished'
                        assert response['effective_duration_seconds'] == 0
                        assert response['connection_attempts'] == 0 and not response['credentials_sent']
                        assert not response['video_session_created'] and not response['listening']
                    assert hub.live.session_count == 0 and not hub.consumers
                    await hub.stop(reason='home_assistant_stop')
                    assert hub.autonomous_doorbell.diagnostics()['close_reason'] == 'home_assistant_stop'
                    summary.append({'autonomous_alarm': 'honest_unavailable_before_IO', 'actual_HA_service_permissions': 'pass'})
            await hass.async_stop(force=True)
        print(json.dumps({'validation': 'actual_HA_RC3_commands_and_alarm_preflight',
            'results': summary, 'device_calls': 0}, sort_keys=True))


if __name__ == '__main__':
    asyncio.run(main(Path(__file__).resolve().parents[1]))
