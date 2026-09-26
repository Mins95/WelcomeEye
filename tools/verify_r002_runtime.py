"""Actual HA 2026.9 APIs, isolated container; all device networking mocked."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root/'custom_components/welcomeeye_local/manifest.json').read_text())
        assert await asyncio.to_thread(install_package, manifest['requirements'][0], **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        integration = importlib.import_module('custom_components.welcomeeye_local')
        capabilities = importlib.import_module('custom_components.welcomeeye_local.capabilities')
        sensors = importlib.import_module('custom_components.welcomeeye_local.sensor')
        services = importlib.import_module('custom_components.welcomeeye_local.services')
        config_flow = importlib.import_module('custom_components.welcomeeye_local.config_flow')
        diagnostics = importlib.import_module('custom_components.welcomeeye_local.diagnostics')
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='R002 synthetic fixture', unique_id='r002-fixture',
            data={'host': '192.0.2.1', 'protocol_family': 'r002_experimental',
                  'fingerprint': {'detected': True}}, options={}, source='user',
            subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        for domain, key in capabilities.ENTITY_CAPABILITIES:
            registry.async_get_or_create(domain, 'welcomeeye_local', f'{entry.unique_id}_{key}', config_entry=entry)
        other = registry.async_get_or_create('sensor', 'welcomeeye_local', 'unrelated_suffix', config_entry=entry)
        created = []
        async def forward(config_entry, platforms):
            for platform in platforms:
                module = importlib.import_module(f'custom_components.welcomeeye_local.{platform.value}')
                await module.async_setup_entry(hass, config_entry, created.extend)
        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), patch.object(
            integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy startup forbidden')), patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('setup network forbidden')):
            assert await integration.async_setup_entry(hass, entry)
        assert len(created) == 1 and isinstance(created[0], sensors.WelcomeEyeProtocolStatus)
        remaining = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert {item.unique_id for item in remaining} == {'r002-fixture_protocol_status', 'unrelated_suffix'}
        sensor = created[0]
        sensor.hass = hass
        sensor.entity_id = 'sensor.r002_fixture_protocol_status'
        assert sensor.entity_category.value == 'diagnostic'
        assert sensor.available
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {
            ('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor},
        }
        assert hass.services.supports_response('welcomeeye_local', 'r002_probe') is SupportsResponse.ONLY
        sensor.hub.probe = AsyncMock(return_value={'protocol': 'r002_8765', 'results': {}})
        response = await hass.services.async_call('welcomeeye_local', 'r002_probe',
            {'entity_id': sensor.entity_id, 'types': [14, 28]}, blocking=True, return_response=True)
        assert response[sensor.entity_id]['protocol'] == 'r002_8765'
        sensor.hub.probe.assert_awaited_once_with([14, 28])
        for invalid in ([505], [14.0], [True]):
            try:
                await hass.services.async_call('welcomeeye_local', 'r002_probe',
                    {'entity_id': sensor.entity_id, 'types': invalid}, blocking=True, return_response=True)
            except Exception:
                pass
            else:
                raise AssertionError('Invalid probe accepted')
        # Actual HA service dispatcher rejects an entity without the method.
        from homeassistant.components.sensor import SensorEntity
        legacy_sensor = SensorEntity()
        legacy_sensor.entity_id = 'sensor.legacy_fixture'
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES][('sensor', 'welcomeeye_local')][legacy_sensor.entity_id] = legacy_sensor
        try:
            await hass.services.async_call('welcomeeye_local', 'r002_probe',
                {'entity_id': legacy_sensor.entity_id}, blocking=True, return_response=True)
        except (HomeAssistantError, AttributeError):
            pass
        else:
            raise AssertionError('Non-R002 target accepted')
        denied_user = SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=Mock(return_value=False)))
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=denied_user))
        try:
            await hass.services.async_call('welcomeeye_local', 'r002_probe',
                {'entity_id': sensor.entity_id}, blocking=True, return_response=True, context=Context(user_id='denied'))
        except HomeAssistantError:
            pass
        else:
            raise AssertionError('Missing entity permission accepted')
        report = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
        assert report['r002']['detected'] is True and '192.0.2.1' not in json.dumps(report)
        assert 'media' not in report
        # Validate the actual ConfigFlow schema and explicit confirmation step.
        flow = config_flow.WelcomeEyeConfigFlow()
        flow.hass = hass
        flow.context = {'source': 'user'}
        with patch.object(config_flow, 'validate_connection', side_effect=config_flow.DiscoveryTimeout()), patch.object(
            config_flow, 'fingerprint', AsyncMock(return_value={'detected': True})):
            form = await flow.async_step_user({'host': '192.0.2.9', 'username': 'fixture', 'password': 'secret'})
        assert form['step_id'] == 'r002_confirm'
        assert form['data_schema']({}) == {'confirm': False}
        declined = await flow.async_step_r002_confirm({'confirm': False})
        assert declined['reason'] == 'experimental_declined'
        with patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
            assert await integration.async_unload_entry(hass, entry)
        assert not hass.services.has_service('welcomeeye_local', 'r002_probe')
        services.async_setup_r002_service(hass)
        assert hass.services.has_service('welcomeeye_local', 'r002_probe')
        # Actual registry V1 upgrade selection removes the three historical entries.
        v1_entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='V1 synthetic fixture', unique_id='v1-fixture', data={}, options={}, source='user',
            subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[v1_entry.entry_id] = v1_entry
        for domain, suffix in capabilities.ENTITY_CAPABILITIES:
            if suffix != 'protocol_status':
                registry.async_get_or_create(domain, 'welcomeeye_local', f'v1-fixture_{suffix}', config_entry=v1_entry)
        remove = list(capabilities.unsupported_entity_ids(
            er.async_entries_for_config_entry(registry, v1_entry.entry_id), v1_entry.entry_id,
            v1_entry.unique_id, capabilities.MATRIX[capabilities.DeviceVariant.V1]))
        assert len(remove) == 3
        for entity_id in remove:
            registry.async_remove(entity_id)
        assert len(er.async_entries_for_config_entry(registry, v1_entry.entry_id)) == 7
        await hass.async_stop(force=True)
        print('Actual HA: diagnostic-only setup, matrix, registry migrations, service response/permissions, flow and unload PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
