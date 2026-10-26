"""Real HA flows, channel entities and lifecycle with an entirely fake cloud."""
import asyncio
from contextlib import ExitStack
import importlib
from pathlib import Path
import socket
import sys
import tempfile
import time
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


class GatedReceiver:
    """Model MCS acceptance independently from successful token registration."""

    def __init__(self, owner, error_type, *, reject=False):
        self.owner, self.error_type, self.reject = owner, error_type, reject
        self.connected = self.closed = False
        self.entered, self.allow_login, self.ready, self.disconnect = (asyncio.Event() for _ in range(4))

    async def register(self):
        await self.owner._credentials_changed({'fcm': {'registration': {'token': 'PRIVATE_FCM'}}})
        return 'PRIVATE_FCM'

    async def run(self):
        self.entered.set()
        await self.allow_login.wait()
        if self.reject:
            raise self.error_type('login_rejected')
        self.connected = True
        self.owner._notify()
        self.ready.set()
        await self.disconnect.wait()
        self.connected = False
        self.owner._notify()
        raise self.error_type('server_closed')

    async def close(self):
        self.connected = False
        self.closed = True

    def diagnostics(self):
        return {'messages': 0, 'delivered': 0, 'malformed': 0,
                'connected': self.connected, 'private': 'PRIVATE_DIAGNOSTIC'}


async def verify_real_receiver_lifecycle(hass, clouds, protocol, package):
    """Real HA storage/controller with every device/cloud boundary replaced."""
    fcm_error = importlib.import_module(package + '.v1_cloud_fcm').FCMError
    for number, family in enumerate(('connect3_qv_experimental', 'r002_experimental')):
        for case in ('accepted', 'rejected', 'stop_before_login'):
            entry = config_entries.ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                title='Synthetic receiver', unique_id=f'receiver-{number}-{case}', source='user', options={},
                discovery_keys={}, subentries_data=[], data={
                    'host': f'192.0.2.{number + 20}', 'protocol_family': family,
                    'qv_cloud_enabled': True, 'second_channel_enabled': True,
                    'qv_cloud_account': {'email': 'fixture@example.invalid', 'password': 'PRIVATE_ACCOUNT',
                        'installation_id': f'01234567-89ab-4cde-8fab-{number:012d}',
                        'device': {'uid': 'PRIVATE_UID'}}})
            hass.config_entries._entries[entry.entry_id] = entry
            rings, changes = Mock(), []
            controller = clouds.QVCloudDoorbell(hass, entry, rings)
            controller._on_state = lambda: changes.append(controller.diagnostics())
            receiver = GatedReceiver(controller, fcm_error, reject=case == 'rejected')
            device = protocol.QvCloudDevice('PRIVATE_UID', 'Synthetic panel', 'IDS94E6SW', (0, 1))
            states = {}
            client = SimpleNamespace(async_register=AsyncMock(), async_subscribe=AsyncMock(),
                async_query_subscription=AsyncMock(), async_close=AsyncMock())

            async def register(token):
                assert token == 'PRIVATE_FCM'
                assert controller._data['credentials']['fcm']['registration']['token'] == token

            async def subscribe(target, channel, enabled):
                assert target is device
                protocol.build_subscription_request('PRIVATE_SESSION', target, channel, enabled)
                states[channel] = enabled

            async def query(target, channel):
                assert target is device
                enabled = states.get(channel, False)
                events = protocol.CALL_EVENTS if enabled else ()
                return protocol.QvSubscriptionState(enabled, events,
                    sum(1 << (event - 1) for event in events), True)

            async def authorize(*, cleanup=False):
                controller._client, controller._device = client, device
                controller._allowed_channels = frozenset((1, 2))

            client.async_register.side_effect = register
            client.async_subscribe.side_effect = subscribe
            client.async_query_subscription.side_effect = query
            controller._authorize = AsyncMock(side_effect=authorize)
            controller._receiver_factory = Mock(return_value=receiver)
            with patch.object(clouds, 'MAX_CONNECTION_ATTEMPTS', 1):
                try:
                    await controller.start()
                    try:
                        await asyncio.wait_for(receiver.entered.wait(), 2)
                    except TimeoutError:
                        raise AssertionError(controller.diagnostics()) from None
                    before = controller.diagnostics()
                    assert before['subscription_status'] == 'confirmed'
                    assert before['status'] == 'connecting' and not before['connected']
                    assert before['reception_status'] == 'not_connected'
                    assert all(change['status'] != 'listening' for change in changes)
                    if case == 'stop_before_login':
                        await controller.stop()
                        assert receiver.closed and controller._task.done()
                        assert controller.diagnostics()['status'] == 'stopped'
                        rings.assert_not_called()
                        continue
                    receiver.allow_login.set()
                    if case == 'rejected':
                        await asyncio.wait_for(controller._task, 2)
                        rejected = controller.diagnostics()
                        assert rejected['status'] == 'failed_reload_required' and not rejected['connected']
                        assert rejected['last_transport_error'] == 'login_rejected'
                        assert all(change['status'] != 'listening' for change in changes)
                        rings.assert_not_called()
                        continue
                    await asyncio.wait_for(receiver.ready.wait(), 2)
                    connected = controller.diagnostics()
                    assert connected['status'] == 'listening' and connected['connected']
                    assert connected['reception_status'] == 'waiting_for_message'
                    await controller._message({'AlarmEvent': '19', 'DevUmid': 'OTHER_PRIVATE_UID',
                        'AlarmId': 'PRIVATE_ALARM', 'DevChNo': '0'}, 'PRIVATE_REJECTED', int(time.time() * 1000))
                    assert controller.diagnostics()['reception_status'] == 'message_rejected'
                    rings.assert_not_called()
                    await controller._message({'AlarmEvent': '19', 'DevUmid': 'PRIVATE_UID',
                        'AlarmId': 'PRIVATE_ALARM', 'DevChNo': '0'}, 'PRIVATE_ACCEPTED', int(time.time() * 1000))
                    accepted = controller.diagnostics()
                    assert accepted['reception_status'] == 'ring_received'
                    assert accepted['rings_received'] == 1 and accepted['delivery_observed']
                    rings.assert_called_once_with(1)
                    # The private replay record reached actual HA storage before emission.
                    assert controller._data['events']['seen']
                    assert all('PRIVATE_' not in str(change) and 'example.invalid' not in str(change)
                               for change in changes)
                    receiver.disconnect.set()
                    await asyncio.wait_for(controller._task, 2)
                    disconnected = controller.diagnostics()
                    assert disconnected['status'] == 'failed_reload_required' and not disconnected['connected']
                    assert disconnected['last_transport_error'] == 'server_closed'
                finally:
                    await controller.stop()
                    assert controller._receiver is None
                    if controller._receiver_factory.called:
                        assert receiver.closed
                    assert not controller._save_tasks


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
            stack.enter_context(patch.object(config, 'read_device', AsyncMock(return_value={
                'streamkey_received': True, 'authentication': 'cgi_accepted'})))
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
        # Keep the flow fixtures above, then exercise the production controller.
        with patch.object(asyncio, 'open_connection', AsyncMock(side_effect=AssertionError('Network forbidden'))), \
                patch.object(socket, 'create_connection', Mock(side_effect=AssertionError('Network forbidden'))), \
                patch('homeassistant.helpers.aiohttp_client.async_get_clientsession', return_value=object()):
            await verify_real_receiver_lifecycle(hass, clouds, protocol, package)
        await hass.async_stop(force=True)
    print('QV cloud actual HA: private transactional flow, both families, isolated channel pulses, real receiver readiness/rejection/disconnect, private storage, no media acquisition, disable and unload PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(90):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
