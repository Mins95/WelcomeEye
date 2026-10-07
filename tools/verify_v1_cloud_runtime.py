"""Real HA configuration, entity and unload APIs; synthetic cloud boundary only.

The V1 cloud transport has its own offline protocol/controller tests. This
checks its integration without opening a device, cloud or media connection.
Run in each supported Home Assistant Core image.
"""
import asyncio
import importlib
import json
import os
from pathlib import Path
import stat
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import HomeAssistant, callback
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


class SyntheticCloud:
    instances = []

    def __init__(self, hass, entry, on_ring, *, on_state=None):
        self.entry, self.on_ring, self.on_state = entry, on_ring, on_state
        self.connected = False
        self.start_count = self.close_count = self.cleanup_count = 0
        self.instances.append(self)

    async def start(self):
        self.start_count += 1

    async def cleanup_pending(self):
        self.cleanup_count += 1

    async def close(self):
        self.close_count += 1
        self.connected = False
        # Simulate a queued callback during cleanup: hub must already be stopped.
        self.on_ring()
        self.on_state()

    def diagnostics(self):
        return {'status': 'connected' if self.connected else 'waiting',
                'transport': 'fcm', 'scope': 'v1_doorbell_only'}


def private_free(value):
    rendered = json.dumps(value)
    for secret in ('PRIVATE_', '192.0.2.1', 'raw_payload', 'fcm_token'):
        assert secret not in rendered, secret


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        for requirement in manifest['requirements']:
            assert await asyncio.to_thread(install_package, requirement, **pip_kwargs(temporary))
        sys.path.insert(0, str(root))
        package = 'custom_components.welcomeeye_local'
        modules = {name: importlib.import_module(package + ('.' + name if name else ''))
                   for name in ('', 'hub', 'config_flow', 'capabilities', 'binary_sensor', 'diagnostics', 'v1_cloud')}
        integration, hubs, config = modules[''], modules['hub'], modules['config_flow']
        caps, sensors, diagnostics = modules['capabilities'], modules['binary_sensor'], modules['diagnostics']
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)
        entries, created = [], []

        def entry_for(variant=caps.DeviceVariant.V1, enabled=None):
            entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                title='Synthetic intercom', unique_id=f'PRIVATE_UID_{len(entries)}',
                data={'host': '192.0.2.1', 'username': 'PRIVATE_USER', 'password': 'PRIVATE_PASSWORD',
                      'protocol_family': caps.family_for(variant).value, 'device_variant': variant.value,
                      **({'v1_cloud_doorbell_enabled': enabled} if enabled is not None else {})},
                options={'ring_image_capture': False},
                source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
            hass.config_entries._entries[entry.entry_id] = entry
            entries.append(entry)
            return entry

        async def forward(entry, platforms):
            # Construct the real entities for every platform, without a frontend.
            for platform in platforms:
                module = importlib.import_module(f'{package}.{platform.value}')
                await module.async_setup_entry(hass, entry, created.extend)

        async def downloaded(entry):
            with patch.object(diagnostics, 'get_crc32c_diagnostics', return_value={'status': 'synthetic'}):
                result = await diagnostics.async_get_config_entry_diagnostics(hass, entry)
            private_free(result)
            return result

        def no_media(hub):
            assert hub.thread is hub.session is None and not hub.consumers
            assert hub.snapshot_requests == hub.snapshot_started_media == 0
            assert hub.control.session is None and hub.control.command_count == 0

        server = SimpleNamespace(sockets=[SimpleNamespace(getsockname=lambda: ('127.0.0.1', 12345))],
                                 close=Mock(), wait_closed=AsyncMock())
        with patch.object(hubs, 'V1CloudDoorbell', SyntheticCloud), \
                patch.object(integration, '_preload_dns_types', lambda: None), \
                patch.object(asyncio, 'start_server', AsyncMock(return_value=server)), \
                patch.object(asyncio, 'open_connection', side_effect=AssertionError('network forbidden')), \
                patch.object(hubs, 'Session', side_effect=AssertionError('device media forbidden')), \
                patch.object(hubs.RingListener, 'start', Mock()) as local_listener, \
                patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), \
                patch.object(hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
            try:
                # Exercise the real controller's actual HA Store, independently
                # of transport. Reading/saving state never creates a receiver.
                stored_entry = entry_for(enabled=True)
                stored_before = json.dumps([dict(stored_entry.data), dict(stored_entry.options)])
                actual_cloud = modules['v1_cloud'].V1CloudDoorbell
                real = actual_cloud(hass, stored_entry, Mock())
                restored = actual_cloud(hass, stored_entry, Mock())
                missing_entry = entry_for(enabled=False)
                missing = actual_cloud(hass, missing_entry, Mock())
                from homeassistant.helpers import aiohttp_client
                with patch.object(aiohttp_client, 'async_get_clientsession', return_value=object()), \
                        patch.object(actual_cloud, '_receiver_factory', side_effect=AssertionError('FCM forbidden')), \
                        patch.object(actual_cloud, '_post', side_effect=AssertionError('cloud HTTP forbidden')):
                    await real._load()
                    credentials = {'fcm': {'registration': {'token': 'PRIVATE_SYNTHETIC_TOKEN'}},
                                   'gcm': {'android_id': 'PRIVATE_ANDROID_ID', 'security_token': 'PRIVATE_SECURITY'}}
                    await real._credentials_changed(credentials)
                    path = Path(temporary) / '.storage' / f'welcomeeye_local.v1_cloud.{stored_entry.entry_id}'
                    saved = json.loads(await hass.async_add_executor_job(path.read_text))
                    assert saved['data']['uid'] == stored_entry.unique_id
                    assert saved['data']['credentials'] == credentials
                    if os.name == 'posix':
                        assert stat.S_IMODE((await hass.async_add_executor_job(path.stat)).st_mode) == 0o600
                    await restored._load()
                    assert restored._data == real._data
                    assert stored_before == json.dumps([dict(stored_entry.data), dict(stored_entry.options)])
                    stored_entry.runtime_data = hubs.WelcomeEyeHub(hass, stored_entry)
                    stored_entry.runtime_data.v1_cloud = real
                    private_free(await downloaded(stored_entry))
                    private_free(real.diagnostics())
                    # Cleanup-only for an entry with no previous state creates
                    # no private installation, file, subscription or enrollment.
                    await missing._load(create=False)
                    missing_path = Path(temporary) / '.storage' / f'welcomeeye_local.v1_cloud.{missing_entry.entry_id}'
                    assert missing._data is None
                    assert not await hass.async_add_executor_job(missing_path.exists)
                    await real.close()
                    await restored.close()
                    await missing.close()

                # Default-off V1 has no cloud controller and no ring entity.
                entry = entry_for()
                before_data, before_options = dict(entry.data), dict(entry.options)
                identity = entry.entry_id, entry.unique_id, entry.title
                assert await integration.async_setup_entry(hass, entry)
                assert not SyntheticCloud.instances and not entry.runtime_data.capabilities.cloud_ring
                assert not any(isinstance(entity, sensors.WelcomeEyeRing) for entity in created)
                local_listener.assert_not_called()
                no_media(entry.runtime_data)
                assert (await downloaded(entry))['v1_cloud_doorbell']['status'] == 'disabled'
                assert await integration.async_unload_entry(hass, entry)

                # Reconfiguration uses the real HA schema and preserves identity/local credentials.
                flow = config.WelcomeEyeConfigFlow()
                flow.hass, flow.context = hass, {'source': 'reconfigure', 'entry_id': entry.entry_id}
                form = await flow.async_step_reconfigure()
                assert form['step_id'] == 'v1_cloud_reconfigure'
                assert 'PRIVATE_' not in str(form)
                assert form['data_schema']({}) == {'v1_cloud_doorbell_enabled': False}
                supplied = form['data_schema']({'v1_cloud_doorbell_enabled': True})
                with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                    assert (await flow.async_step_reconfigure(supplied))['type'] == 'abort'
                    await hass.async_block_till_done()
                assert (entry.entry_id, entry.unique_id, entry.title) == identity
                assert dict(entry.options) == before_options
                assert dict(entry.data) == {**before_data, 'v1_cloud_doorbell_enabled': True}

                # Old capture registry entries must not turn on automatic photos for V1.
                ring_entry = registry.async_get_or_create('binary_sensor', 'welcomeeye_local',
                    f'{entry.unique_id}_ring', config_entry=entry)
                capture_entry = registry.async_get_or_create('image', 'welcomeeye_local',
                    f'{entry.unique_id}_last_ring', config_entry=entry)
                created.clear()
                assert await integration.async_setup_entry(hass, entry)
                hub, cloud = entry.runtime_data, SyntheticCloud.instances[-1]
                assert cloud.start_count == 1 and cloud.entry is entry
                ring = next(entity for entity in created if isinstance(entity, sensors.WelcomeEyeRing))
                assert registry.async_get(ring_entry.entity_id) is not None
                assert registry.async_get(capture_entry.entity_id) is None
                assert hub.capabilities.cloud_ring and not hub.capabilities.local_ring
                assert not hub.capabilities.ring_image_capture and not hub.capabilities.last_ring_image
                assert not ring.available
                ring.hass, ring.entity_id = hass, ring_entry.entity_id
                ring.async_write_ha_state = Mock()  # Entity callback without a frontend platform.
                await ring.async_added_to_hass()
                cloud.connected = True
                cloud.on_state()
                assert ring.available
                ring.async_write_ha_state.assert_called_once()
                events = []

                @callback
                def receive(event):
                    events.append(dict(event.data))

                remove = hass.bus.async_listen('welcomeeye_local.ring', receive)
                with patch.object(hub, 'acquire', AsyncMock()) as acquire, \
                        patch.object(hub.ring_image, 'request', Mock()) as photo, \
                        patch.object(hub.control, 'unlock', Mock()) as unlock:
                    cloud.on_ring()
                    await hass.async_block_till_done()
                    assert events == [{'entry_id': entry.entry_id, 'channel': 1,
                                       'ring_sequence': 1, 'source': 'cloud'}]
                    assert ring.is_on and hub.ring_count == 1
                    no_media(hub)
                    private_free(events)
                    private_free(ring.extra_state_attributes)
                    report = (await downloaded(entry))['v1_cloud_doorbell']
                    assert report['enabled'] and report['connected']
                    assert report['scope'] == 'v1_doorbell_only'
                    cloud.connected = False
                    cloud.on_state()
                    assert not ring.available  # A video connection cannot stand in for cloud.
                    hub.connected = True
                    assert not ring.available
                    hub.connected = False
                    assert await integration.async_unload_entry(hass, entry)
                    assert cloud.close_count == 1 and hub.stopped and not ring.available
                    cloud.on_ring()
                    cloud.on_state()
                    await hass.async_block_till_done()
                    assert len(events) == hub.ring_count == 1 and not hub.ringing
                    acquire.assert_not_awaited()
                    photo.assert_not_called()
                    unlock.assert_not_called()
                remove()
                await ring.async_will_remove_from_hass()
                await hub.stop()
                assert cloud.close_count == 1

                # Disabling in-place removes the ring entity. Only pending
                # subscription cleanup may run; it never starts a new receiver.
                with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                    await flow.async_step_reconfigure({'v1_cloud_doorbell_enabled': False})
                    await hass.async_block_till_done()
                created.clear()
                assert await integration.async_setup_entry(hass, entry)
                assert len(SyntheticCloud.instances) == 2
                cleanup = SyntheticCloud.instances[-1]
                assert cleanup.start_count == 0 and cleanup.cleanup_count == 1
                assert registry.async_get(ring_entry.entity_id) is None
                assert not any(isinstance(entity, sensors.WelcomeEyeRing) for entity in created)
                assert dict(entry.data) == {**before_data, 'v1_cloud_doorbell_enabled': False}
                assert dict(entry.options) == before_options
                assert await integration.async_unload_entry(hass, entry)

                # A stale/copied option cannot activate cloud on another device family.
                for variant in caps.DeviceVariant:
                    if variant == caps.DeviceVariant.V1:
                        continue
                    other = entry_for(variant, True)
                    assert await integration.async_setup_entry(hass, other)
                    assert not other.runtime_data.capabilities.cloud_ring
                    assert len(SyntheticCloud.instances) == 2
                    assert await integration.async_unload_entry(hass, other)
            finally:
                for configured in entries:
                    if hub := getattr(configured, 'runtime_data', None):
                        await hub.stop()
                await hass.async_stop(force=True)
    print('REAL_HA_V1_CLOUD_OPT_IN_ENTITY_PRIVACY_NO_MEDIA_AND_UNLOAD_OK')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
