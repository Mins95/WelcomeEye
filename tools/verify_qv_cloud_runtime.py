"""Real HA flows, channel entities and lifecycle with an entirely fake cloud."""
import asyncio
from contextlib import ExitStack
from copy import deepcopy
from dataclasses import asdict
import importlib
from io import BytesIO
import json
from pathlib import Path
import socket
import sys
import tempfile
import time
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant import config_entries
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr
from homeassistant.helpers import entity_registry as er
from homeassistant.helpers.data_entry_flow import FlowManagerResourceView
from PIL import Image


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


async def verify_qv_ring_photos(hass, clouds, protocol, package, hub_types):
    """Real hubs, event filtering, entities and private storage; synthetic video."""
    images = importlib.import_module(package + '.image')
    switches = importlib.import_module(package + '.switch')
    native = importlib.import_module(package + '.qv_cloud_android')
    ring_module = importlib.import_module(package + '.ring_image')
    fcm_error = importlib.import_module(package + '.v1_cloud_fcm').FCMError
    registry = er.async_get(hass)
    media_root = Path(hass.config.path('qv_photo_media'))
    hass.config.media_dirs = {'local': str(media_root)}
    await hass.config.async_set_time_zone('Europe/Paris')
    jpeg = {}
    for channel, color in ((1, 'red'), (2, 'blue')):
        stream = BytesIO()
        Image.new('RGB', (16, 16), color).save(stream, format='JPEG')
        jpeg[channel] = stream.getvalue()

    for number, (family, hub_type) in enumerate(hub_types):
        entry = config_entries.ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Synthetic ring photos', unique_id=f'ring-photos-{number}', source='user',
            options={'unrelated_preference': 'keep'}, discovery_keys={}, subentries_data=[], data={
                'host': f'192.0.2.{number + 40}', 'protocol_family': family,
                'auth_code': 'PRIVATE_LOCAL', 'certificate_sha256': 'a' * 64,
                'media_certificate_sha256': 'a' * 64, 'experimental_video': True,
                'second_channel_enabled': True, 'qv_cloud_enabled': True,
                'qv_cloud_account': {'email': 'photo-fixture@example.invalid',
                    'password': 'PRIVATE_ACCOUNT',
                    'installation_id': f'01234567-89ab-4cde-8fab-{number + 40:012d}',
                    'device': {'uid': 'PRIVATE_PHOTO_UID'}}})
        hass.config_entries._entries[entry.entry_id] = entry
        original_data = deepcopy(dict(entry.data))
        expected_credentials = client_id = None
        observed = []

        @callback
        def observe(event):
            if event.data.get('entry_id') == entry.entry_id:
                observed.append(event)

        removers = [hass.bus.async_listen(name, observe) for name in
                    ('welcomeeye_local.ring', 'welcomeeye_local.ring_image')]

        async def start_runtime():
            nonlocal expected_credentials, client_id
            hub = hub_type(hass, entry)
            assert hub.capabilities.ring_image_capture and hub.capabilities.last_ring_image
            assert hub.channel2.capabilities.last_ring_image
            entry.runtime_data = hub
            controller = clouds.QVCloudDoorbell(hass, entry, hub._cloud_ring, on_state=hub._notify)
            controller._started = time.time()
            await controller._load()
            if expected_credentials is None:
                profile = native.QVAndroidFCMReceiver(object(), Mock(), Mock(), variant=str(hub.variant))
                expected_credentials = {'transport': 'android', 'config': profile._config(),
                    'android': asdict(profile._android),
                    'gcm': {'android_id': '123456', 'security_token': '654321',
                        'app_id': profile._profile.sender_id, 'token': 'PRIVATE_PHOTO_TOKEN'},
                    'fcm': {'registration': {'token': 'PRIVATE_PHOTO_TOKEN'},
                        'installation': {'fid': 'c' + 'A' * 21, 'token': 'PRIVATE_AUTH',
                            'refresh_token': 'PRIVATE_REFRESH', 'expires_in': 604800,
                            'created_at': time.time()}}}
                profile._validate_credentials(expected_credentials)
                await controller._save(credentials=expected_credentials)
                client_id = controller._data['client_id']
            assert controller._data['credentials'] == expected_credentials
            assert controller._data['client_id'] == client_id
            receiver = GatedReceiver(controller, fcm_error)

            async def register_existing():
                assert controller._data['credentials'] == expected_credentials
                await controller._credentials_changed(expected_credentials)
                return expected_credentials['fcm']['registration']['token']

            receiver.register = AsyncMock(side_effect=register_existing)
            receiver.allow_login.set()
            device = protocol.QvCloudDevice('PRIVATE_PHOTO_UID', 'Fixture', 'IDS94E6SW', (0, 1))
            client = SimpleNamespace(async_register=AsyncMock(), async_subscribe=AsyncMock(),
                async_query_subscription=AsyncMock(return_value=protocol.QvSubscriptionState(
                    True, protocol.CALL_EVENTS, sum(1 << (event - 1) for event in protocol.CALL_EVENTS), True)),
                async_close=AsyncMock())

            async def authorize(*, cleanup=False):
                controller._client, controller._device = client, device
                controller._allowed_channels = frozenset((1, 2))

            controller._authorize = AsyncMock(side_effect=authorize)
            controller._receiver_factory = Mock(return_value=receiver)
            owners = (hub, hub.channel2)
            for owner in owners:
                owner.acquire = AsyncMock(side_effect=AssertionError('Physical media forbidden'))
                owner.ring_image._capture_image = AsyncMock(return_value=jpeg[owner.channel])
            with patch.object(clouds, 'QVCloudDoorbell', return_value=controller):
                await hub.start()
            await asyncio.wait_for(receiver.ready.wait(), 5)
            client.async_register.assert_awaited_once_with('PRIVATE_PHOTO_TOKEN')
            entities = []
            await images.async_setup_entry(hass, entry, entities.extend)
            await switches.async_setup_entry(hass, entry, entities.extend)
            switch = next(item for item in entities if isinstance(item, switches.WelcomeEyeRingCaptureSwitch))
            ring_images = {item.capture.channel: item for item in entities
                           if isinstance(item, images.WelcomeEyeRingImage)}
            assert set(ring_images) == {1, 2}
            assert sum(isinstance(item, switches.WelcomeEyeRingCaptureSwitch) for item in entities) == 1
            for item in entities:
                item.hass = hass
                domain = 'switch' if item is switch else 'image'
                item.entity_id = registry.async_get_or_create(domain, 'welcomeeye_local',
                    item.unique_id, config_entry=entry).entity_id
                # Actual HA entities and registry, without a frontend platform.
                item.async_write_ha_state = Mock()
                await item.async_added_to_hass()
            assert ring_images[1].unique_id == f'{entry.unique_id}_last_ring'
            assert ring_images[2].unique_id == f'{entry.unique_id}_last_ring_channel_2'
            assert ring_images[1].device_info == ring_images[2].device_info
            return hub, controller, owners, switch, ring_images, entities

        async def finish_runtime(runtime):
            hub, _, _, _, _, entities = runtime
            await hub.stop()
            for item in reversed(entities):
                await item.async_will_remove_from_hass()

        async def ring(controller, identifier, channel):
            await controller._message({'AlarmEvent': '19', 'DevUmid': 'PRIVATE_PHOTO_UID',
                'AlarmId': identifier, 'DevChNo': str(channel - 1)}, identifier,
                int(time.time() * 1000))
            await hass.async_block_till_done()

        async def check_saved(controller, enabled):
            await hass.config_entries._store.async_save(hass.config_entries._data_to_save())
            stored = json.loads(await hass.async_add_executor_job(
                Path(hass.config.path('.storage/core.config_entries')).read_text))
            selected = next(value for value in stored['data']['entries'] if value['entry_id'] == entry.entry_id)
            assert selected['options'] == {'unrelated_preference': 'keep', 'ring_image_capture': enabled}
            assert dict(entry.data) == original_data
            saved = await controller._store.async_load()
            assert saved['credentials'] == expected_credentials and saved['client_id'] == client_id

        runtime = None
        pending_release = asyncio.Event()
        try:
            with patch.object(ring_module, 'RING_IMAGE_FALLBACK_DELAY', 0.01):
                runtime = await start_runtime()
                hub, controller, owners, switch, ring_images, _ = runtime
                original_ids = {channel: item.entity_id for channel, item in ring_images.items()}
                assert not switch.is_on and all(not owner.ring_image.enabled for owner in owners)
                assert 'ring_image_capture' not in entry.options
                await ring(controller, 'PRIVATE_OFF_RING', 2)
                assert controller.diagnostics()['rings_received'] == 1
                assert [event.event_type for event in observed] == ['welcomeeye_local.ring']
                for owner in owners:
                    owner.acquire.assert_not_awaited()
                    owner.ring_image._capture_image.assert_not_awaited()
                    assert owner.ring_image._task is None

                await switch.async_turn_on()
                assert all(owner.ring_image.enabled for owner in owners)
                await check_saved(controller, True)
                for channel in (1, 2):
                    await ring(controller, f'PRIVATE_PHOTO_{channel}', channel)
                    capture = owners[channel - 1].ring_image
                    await asyncio.wait_for(capture._task, 5)
                    await hass.async_block_till_done()
                    assert await ring_images[channel].async_image() == jpeg[channel]
                    assert ring_images[channel].extra_state_attributes['channel'] == channel
                    assert capture.diagnostics['save_successes'] == 1
                    assert f'_channel_{channel}/' in capture.filename
                    path = media_root / capture.filename
                    assert path.resolve().is_relative_to(media_root.resolve())
                    assert await hass.async_add_executor_job(path.read_bytes) == jpeg[channel]
                    matching = [event for event in observed if event.data['ring_sequence'] == capture.image_sequence]
                    assert [event.event_type for event in matching] == [
                        'welcomeeye_local.ring', 'welcomeeye_local.ring_image']
                    assert all(event.data['channel'] == channel for event in matching)
                    if channel == 1:
                        assert owners[1].ring_image.jpeg is None
                assert owners[0].ring_image.filename != owners[1].ring_image.filename
                assert owners[0].ring_image.jpeg == jpeg[1]
                assert owners[1].ring_image.jpeg == jpeg[2]
                for owner in owners:
                    owner.ring_image._capture_image.assert_awaited_once()
                    owner.acquire.assert_not_awaited()
                before = len(observed)
                await ring(controller, 'PRIVATE_WRONG_CHANNEL', 3)
                assert len(observed) == before
                assert controller.diagnostics()['last_message_disposition'] == 'channel_not_enabled_or_authorized'

                await switch.async_turn_off()
                await check_saved(controller, False)
                await ring(controller, 'PRIVATE_OFF_AGAIN', 1)
                for owner in owners:
                    owner.ring_image._capture_image.assert_awaited_once()
                files_before = set(media_root.rglob('*.jpg'))
                await finish_runtime(runtime)
                runtime = await start_runtime()
                hub, controller, owners, switch, ring_images, _ = runtime
                assert {channel: item.entity_id for channel, item in ring_images.items()} == original_ids
                assert not switch.is_on and all(not owner.ring_image.enabled for owner in owners)
                await check_saved(controller, False)
                await switch.async_turn_on()
                await check_saved(controller, True)
                # Durable replay filtering survives reload, independently of photo state.
                before = len(observed)
                await ring(controller, 'PRIVATE_PHOTO_1', 1)
                assert len(observed) == before
                assert controller.diagnostics()['last_message_disposition'] == 'duplicate'

                entered = asyncio.Event()

                async def pending_image(*, timeout):
                    entered.set()
                    await pending_release.wait()
                    return jpeg[1]

                owners[0].ring_image._capture_image = AsyncMock(side_effect=pending_image)
                await ring(controller, 'PRIVATE_PENDING_STOP', 1)
                await asyncio.wait_for(entered.wait(), 5)
                photo_count = sum(event.event_type == 'welcomeeye_local.ring_image' for event in observed)
                stopping = asyncio.create_task(hub.stop())
                await asyncio.sleep(0)
                assert hub.stopped
                pending_release.set()
                await asyncio.wait_for(stopping, 5)
                await hass.async_block_till_done()
                assert set(media_root.rglob('*.jpg')) == files_before
                assert sum(event.event_type == 'welcomeeye_local.ring_image' for event in observed) == photo_count
                assert owners[0].ring_image.jpeg is None and owners[0].ring_image._task is None
                assert not hub.consumers and not hub.channel2.consumers
                await finish_runtime(runtime)
                runtime = await start_runtime()
                hub, controller, owners, switch, _, _ = runtime
                assert switch.is_on and all(owner.ring_image.enabled for owner in owners)
                await check_saved(controller, True)
                assert all(owner.ring_image._task is None for owner in owners)
                assert 'PRIVATE_' not in str(hub.diagnostics())
        finally:
            pending_release.set()
            if runtime is not None:
                await finish_runtime(runtime)
            for remove in removers:
                remove()
    print('QV ring photos actual HA: both families, registry, default OFF, two-channel private JPEGs, ring-before-image, reload, credentials and pending-stop PASS')


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
            await verify_qv_ring_photos(hass, clouds, protocol, package,
                (('connect3_qv_experimental', hub_type), ('r002_experimental', r002_type)))
        await hass.async_stop(force=True)
    print('QV cloud actual HA: private transactional flow, both families, isolated channel pulses, real receiver readiness/rejection/disconnect, private storage, no media acquisition, disable and unload PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(90):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
