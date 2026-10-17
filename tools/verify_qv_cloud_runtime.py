"""Real HA flows, channel entities and lifecycle with an entirely fake cloud."""
import asyncio
from contextlib import ExitStack
import importlib
from pathlib import Path
import socket
import sys
import tempfile
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView


class SyntheticCloud:
    instances = []

    def __init__(self, hass, entry, on_ring, *, on_state=None):
        self.on_ring, self.on_state = on_ring, on_state
        self.connected = False
        self.started = self.stopped = self.cleaned = False
        self.instances.append(self)

    async def start(self):
        self.started = self.connected = True

    async def stop(self):
        self.stopped, self.connected = True, False
        self.on_ring(1)  # Simulate a queued callback during unload.

    async def cleanup_pending(self):
        self.cleaned = True

    def diagnostics(self):
        return {'status': 'listening', 'transport': 'fcm'}


async def main(root):
    sys.path.insert(0, str(root))
    package = 'custom_components.welcomeeye_local'
    config = importlib.import_module(package + '.config_flow')
    clouds = importlib.import_module(package + '.qv_cloud')
    protocol = importlib.import_module(package + '.qv_cloud_protocol')
    trust = importlib.import_module(package + '.connect3.trust')
    hub_type = importlib.import_module(package + '.connect3.hub').Connect3Hub
    r002_type = importlib.import_module(package + '.r002.hub').R002InvestigationHub
    sensors = importlib.import_module(package + '.binary_sensor')
    with tempfile.TemporaryDirectory() as directory:
        hass = HomeAssistant(directory)
        hass.config_entries = config_entries.ConfigEntries(hass, {})
        await hass.config_entries.async_initialize()
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        with ExitStack() as stack:
            stack.enter_context(patch.object(asyncio, 'open_connection', AsyncMock(side_effect=AssertionError('Network forbidden'))))
            stack.enter_context(patch.object(socket, 'create_connection', Mock(side_effect=AssertionError('Network forbidden'))))
            stack.enter_context(patch.object(clouds, 'QVCloudDoorbell', SyntheticCloud))
            stack.enter_context(patch.object(hass.config_entries, 'async_reload', AsyncMock(return_value=True)))
            stack.enter_context(patch.object(hass.config_entries, 'async_setup', AsyncMock(return_value=True)))
            if hasattr(config_entries, '_support_single_config_entry_only'):
                stack.enter_context(patch.object(config_entries, '_support_single_config_entry_only', AsyncMock(return_value=False)))

            async def create_flow(handler, *, context=None, data=None):
                flow = config.WelcomeEyeConfigFlow()
                flow.init_step = context['source']
                return flow
            stack.enter_context(patch.object(hass.config_entries.flow, 'async_create_flow', side_effect=create_flow))
            endpoint = trust.EndpointTrust('pinned', 'a' * 64, validity_status='valid')
            stack.enter_context(patch.object(config, 'inspect_trust', AsyncMock(return_value=trust.TrustInspection(endpoint, endpoint))))
            authorize = stack.enter_context(patch.object(config.WelcomeEyeConfigFlow, '_qv_authorized_devices',
                AsyncMock(return_value=([protocol.QvCloudDevice('PRIVATE_UID', 'Synthetic panel', '', (0, 1))], 0))))
            for number, (family, cls) in enumerate((('connect3_qv_experimental', hub_type), ('r002_experimental', r002_type))):
                entry = config_entries.ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                    title='Synthetic', unique_id=f'provisional-{number}', source='user', options={},
                    discovery_keys={}, subentries_data=[],
                    data={'host': f'192.0.2.{number + 1}', 'protocol_family': family,
                          'auth_code': 'PRIVATE_LOCAL', 'certificate_sha256': 'a' * 64,
                          'media_certificate_sha256': 'a' * 64, 'second_channel_enabled': True})
                hass.config_entries._entries[entry.entry_id] = entry
                result = await hass.config_entries.flow.async_init('welcomeeye_local',
                    context={'source': 'reconfigure', 'entry_id': entry.entry_id})
                fields = FlowManagerResourceView(None)._prepare_result_json(result)['data_schema']
                assert any(item['name'] == 'qv_cloud_action' for item in fields)
                result = await hass.config_entries.flow.async_configure(result['flow_id'],
                    {'host': entry.data['host'], 'qv_cloud_action': 'configure', 'second_channel_enabled': True})
                assert result['step_id'] == 'qv_cloud_account', result
                assert 'PRIVATE_LOCAL' not in str(FlowManagerResourceView(None)._prepare_result_json(result))
                result = await hass.config_entries.flow.async_configure(result['flow_id'], {
                    'email': f'fixture{number}@example.invalid', 'password': 'PRIVATE_ACCOUNT', 'confirm_cloud': True})
                assert result['step_id'] == 'qv_cloud_device', result
                assert 'PRIVATE_ACCOUNT' not in str(FlowManagerResourceView(None)._prepare_result_json(result))
                assert not entry.data.get('qv_cloud_enabled')
                result = await hass.config_entries.flow.async_configure(result['flow_id'], {'device': '0', 'confirm_device': True})
                assert result['reason'] == 'reconfigure_successful', result
                assert entry.data['auth_code'] == 'PRIVATE_LOCAL'
                assert entry.unique_id == f'provisional-{number}'
                hub = cls(hass, entry)
                await hub.start()
                entities = []
                entry.runtime_data = hub
                await sensors.async_setup_entry(hass, entry, entities.extend)
                assert len(entities) == 2 and all(not entity.is_on for entity in entities)
                assert hub.qv_cloud.started
                hub.qv_cloud.on_ring(2)
                assert not entities[0].is_on and entities[1].is_on
                hub.qv_cloud.on_ring(1)
                assert all(entity.is_on for entity in entities)
                assert not hub.live.session_count and not hub.consumers
                diagnostic = str(hub.diagnostics())
                assert 'PRIVATE_' not in diagnostic and 'example.invalid' not in diagnostic
                await hub.stop()
                assert hub.qv_cloud.stopped and not hub.ringing and not hub._ring_timers
                # Leave the old entity identity while making its source unavailable.
                hass.config_entries.async_update_entry(entry, data={**entry.data, 'qv_cloud_enabled': False})
                disabled = cls(hass, entry)
                await disabled.start()
                assert disabled.qv_cloud.cleaned and not disabled.capabilities.cloud_ring
                await disabled.stop()
                # Isolate subsequent account selection fixture from this selected UID.
            assert authorize.await_count == 2
        await hass.async_stop(force=True)
    print('QV cloud actual HA: private transactional flow, both families, isolated channel pulses, no media acquisition, disable and unload PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(90):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
