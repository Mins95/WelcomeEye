"""Actual HA 2026.7/2026.9 APIs. Synthetic fixtures; all device I/O mocked."""
import asyncio
import importlib
import json
from pathlib import Path
import sys
import tempfile
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant
from homeassistant.exceptions import ConfigEntryError, HomeAssistantError
from homeassistant.helpers import device_registry as dr, entity_registry as er
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES
from homeassistant.requirements import pip_kwargs
from homeassistant.util.package import install_package


async def main(root):
    with tempfile.TemporaryDirectory() as temporary:
        manifest = json.loads((root / 'custom_components/welcomeeye_local/manifest.json').read_text())
        assert await asyncio.to_thread(install_package, manifest['requirements'][0], **pip_kwargs(temporary))
        sys.path[:0] = [str(root), str(root / 'tests')]
        package = 'custom_components.welcomeeye_local'
        integration = importlib.import_module(package)
        caps = importlib.import_module(package + '.capabilities')
        config = importlib.import_module(package + '.config_flow')
        sensors = importlib.import_module(package + '.sensor')
        diagnostics = importlib.import_module(package + '.diagnostics')
        cgi = importlib.import_module(package + '.connect3.cgi')
        certificate = importlib.import_module(package + '.connect3.certificate')
        hub_module = importlib.import_module(package + '.connect3.hub')
        qv = importlib.import_module(package + '.r002.qv_discovery')
        from test_connect3_discovery import synthetic_packet
        from test_connect3_cgi import reply, page
        from test_r002_qv_discovery import FakeNetwork
        from test_connect3_certificate import writer

        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        if hasattr(dr, 'async_setup'):
            dr.async_setup(hass)
        await dr.async_load(hass)
        await er.async_load(hass)
        registry = er.async_get(hass)

        def entry_for(family='connect3_qv_experimental'):
            return ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
                title='Connect 3 synthetic fixture', unique_id='connect3-fixture-' + family,
                data={'host': '192.0.2.1', 'protocol_family': family}, options={}, source='user',
                subentries_data=None, discovery_keys=MappingProxyType({}))

        entry = entry_for()
        hass.config_entries._entries[entry.entry_id] = entry
        for domain, key in caps.ENTITY_CAPABILITIES:
            registry.async_get_or_create(domain, 'welcomeeye_local', f'{entry.unique_id}_{key}', config_entry=entry)
        created = []

        async def forward(config_entry, platforms):
            for platform in platforms:
                module = importlib.import_module(f'{package}.{platform.value}')
                await module.async_setup_entry(hass, config_entry, created.extend)

        with patch.object(hass.config_entries, 'async_forward_entry_setups', side_effect=forward), patch.object(
            integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy forbidden')), patch.object(
            integration, 'R002InvestigationHub', side_effect=AssertionError('R002 forbidden')), patch.object(
            asyncio, 'open_connection', side_effect=AssertionError('automatic TCP forbidden')), patch.object(
            qv, '_open_listener', side_effect=AssertionError('automatic UDP forbidden')):
            assert await integration.async_setup_entry(hass, entry)
            try:
                await integration.async_setup_entry(hass, entry_for('unknown_family'))
            except ConfigEntryError:
                pass
            else:
                raise AssertionError('Unknown family must fail before I/O')
        assert len(created) == 1 and isinstance(created[0], sensors.WelcomeEyeConnect3Status)
        remaining = er.async_entries_for_config_entry(registry, entry.entry_id)
        assert {e.unique_id for e in remaining} == {entry.unique_id + '_connect3_status'}
        sensor = created[0]
        sensor.hass = hass
        sensor.entity_id = 'sensor.connect3_fixture_status'
        assert sensor.available and sensor.native_value == 'declared'
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {('sensor', 'welcomeeye_local'): {sensor.entity_id: sensor}}
        user = SimpleNamespace(is_admin=False, permissions=SimpleNamespace(check_entity=Mock(return_value=True)))
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=user))
        actions = {'connect3_discover': {}, 'connect3_check_certificate': {}, 'connect3_check_access': {},
                   'connect3_list_records': {'start': '2026-10-01 00:00:00', 'end': '2026-10-01 01:00:00'}}

        async def action(name, data=None, context=None):
            return await hass.services.async_call('welcomeeye_local', name,
                {'entity_id': sensor.entity_id, **actions[name], **(data or {})},
                blocking=True, return_response=True, context=context or Context(user_id='fixture-admin'))

        for name in actions:
            for context in (Context(), Context(user_id='nonadmin')):
                try:
                    await action(name, context=context)
                except HomeAssistantError:
                    pass
                else:
                    raise AssertionError('Unprivileged Connect 3 call accepted')
        assert sensor.hub.runs == 0
        user.is_admin = True
        before = json.dumps([dict(entry.data), dict(entry.options)])
        packet = synthetic_packet()
        network = FakeNetwork([(packet, ('192.0.2.1', 5000), 5003)], module=qv)
        with patch.object(qv, '_open_listener', side_effect=network.open), patch.object(qv, 'TIMEOUT', .01):
            response = await action('connect3_discover', {'include_details': True})
        assert response[sensor.entity_id]['decoded_records'] == 1
        assert response[sensor.entity_id]['records'][0]['firmware'] == 'SYNTHETIC'
        assert len(network.sent) == 1 and all(p.closed.done() for _, p in network.endpoints)
        persisted = json.dumps([await diagnostics.async_get_config_entry_diagnostics(hass, entry), sensor.extra_state_attributes])
        for secret in ('192.0.2.1', 'SYNTHETIC', packet.hex(), 'response_hex', 'records'):
            if secret == 'records':
                assert '"records"' not in persisted
            else:
                assert secret not in persisted, secret
        assert before == json.dumps([dict(entry.data), dict(entry.options)])
        # Certificate inspection sends no HTTP/authentication and never changes
        # the entry. Its optional fingerprint stays out of diagnostics/state.
        stream = writer()
        with patch.object(certificate.asyncio, 'open_connection', AsyncMock(return_value=(Mock(), stream))):
            response = await action('connect3_check_certificate', {'include_details': True})
        observed_pin = response[sensor.entity_id]['certificate_sha256']
        assert response[sensor.entity_id]['tls_handshake_ok']
        assert not response[sensor.entity_id]['certificate_trust_authenticated']
        stream.write.assert_not_called()
        stream.close.assert_called_once()
        persisted = json.dumps([await diagnostics.async_get_config_entry_diagnostics(hass, entry), sensor.extra_state_attributes])
        assert observed_pin not in persisted
        assert before == json.dumps([dict(entry.data), dict(entry.options)])
        user.permissions.check_entity.return_value = False
        try:
            await action('connect3_check_certificate', {'include_details': True})
        except HomeAssistantError:
            pass
        else:
            raise AssertionError('Entity-control permission bypassed')
        user.permissions.check_entity.return_value = True
        with patch.object(hub_module, 'read_device', AsyncMock(side_effect=AssertionError('credentialless I/O forbidden'))):
            response = await action('connect3_check_access')
        assert response[sensor.entity_id]['reason'] == 'local_auth_code_required'
        hass.config_entries.async_update_entry(entry, data={**entry.data, 'auth_code': 'SYNTHETIC_SECRET'})
        with patch.object(cgi, '_post', AsyncMock(return_value=reply('<key>PRIVATE_KEY</key><tdc>PRIVATE_TDC</tdc>'))):
            response = await action('connect3_check_access')
        assert response[sensor.entity_id]['streamkey_received']
        with patch.object(cgi, '_post', AsyncMock(side_effect=[reply('<record><id>PRIVATE_SESSION</id></record>'), page()])):
            response = await action('connect3_list_records', {'include_details': True})
        assert response[sensor.entity_id]['record_count'] == 1
        assert response[sensor.entity_id]['records'][0]['filename'] == 'SYNTHETIC_PRIVATE_FILENAME'
        persisted = json.dumps([await diagnostics.async_get_config_entry_diagnostics(hass, entry), sensor.extra_state_attributes])
        for secret in ('PRIVATE', 'SYNTHETIC', '192.0.2.1', '"records"'):
            assert secret not in persisted, secret

        # Wrong-family target fails even though the service is registered.
        legacy = sensors.WelcomeEyeSensor(SimpleNamespace(entry=SimpleNamespace(
            unique_id='legacy', title='legacy'), device_model='legacy', connected=True, capabilities=caps.MATRIX[caps.DeviceVariant.V1]),
            'fps', 'fps')
        legacy.hass = hass
        legacy.entity_id = 'sensor.legacy_fixture'
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES][('sensor', 'welcomeeye_local')][legacy.entity_id] = legacy
        try:
            await action('connect3_discover', {'entity_id': legacy.entity_id})
        except HomeAssistantError:
            pass
        else:
            raise AssertionError('Wrong family service accepted')

        # Actual flow methods/schemas, not a test-only copy of the implementation.
        flow = config.WelcomeEyeConfigFlow()
        flow.hass = hass
        flow.context = {'source': 'user'}
        with patch.object(config, 'validate_connection', side_effect=AssertionError('legacy login forbidden')), patch.object(
            config, 'fingerprint', side_effect=AssertionError('R002 fingerprint forbidden')):
            menu = await flow.async_step_user()
            assert menu['type'] == 'menu'
            form = await flow.async_step_connect3()
            data = form['data_schema']({'host': '192.0.2.8', 'confirm': True})
            result = await flow.async_step_connect3(data)
            assert result['type'] == 'create_entry'
            assert result['data']['protocol_family'] == 'connect3_qv_experimental'
            assert 'auth_code' not in result['data']
            assert result['data']['identity_source'] == 'provisional_random'
            reauth = config.WelcomeEyeConfigFlow()
            reauth.hass = hass
            reauth.context = {'source': 'reauth', 'entry_id': entry.entry_id}
            assert (await reauth.async_step_reauth(entry.data))['reason'] == 'reauth_not_supported'
            reconfigure = config.WelcomeEyeConfigFlow()
            reconfigure.hass = hass
            reconfigure.context = {'source': 'reconfigure', 'entry_id': entry.entry_id}
            form = await reconfigure.async_step_reconfigure()
            assert form['step_id'] == 'connect3_reconfigure'
            assert 'SYNTHETIC_SECRET' not in str(form)
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                await reconfigure.async_step_connect3_reconfigure({'host': '192.0.2.2'})
            assert entry.unique_id == 'connect3-fixture-connect3_qv_experimental'
            assert entry.data['auth_code'] == 'SYNTHETIC_SECRET'
            # Secret QR is imported via the actual config flow, never a service
            # response or trace; retained identity remains unchanged.
            with patch.object(hass.config_entries, 'async_reload', AsyncMock()):
                await reconfigure.async_step_connect3_reconfigure({
                    'host': '192.0.2.2', 'installation_qr':
                    'PRIVATE_AP PRIVATE_UID PRIVATE_QR_CODE IDS94E6SW'})
            assert entry.data['auth_code'] == 'PRIVATE_QR_CODE'
            assert entry.data['credential_device_uid'] == 'PRIVATE_UID'
            assert entry.data['credential_source'] == 'apk_space'
            assert 'installation_qr' not in entry.data
            assert 'PRIVATE_AP' not in json.dumps(dict(entry.data))
            assert entry.unique_id == 'connect3-fixture-connect3_qv_experimental'
        with patch.object(hub_module, 'discover', AsyncMock(return_value={
            'credential_identity_status': 'mismatch'})), patch.object(hub_module, 'read_device', AsyncMock()) as read:
            response = await action('connect3_check_access')
        read.assert_not_called()
        assert response[sensor.entity_id]['reason'] == 'credential_identity_not_matched'
        persisted = json.dumps([await diagnostics.async_get_config_entry_diagnostics(hass, entry), sensor.extra_state_attributes])
        for private in ('PRIVATE_UID', 'PRIVATE_QR_CODE', 'PRIVATE_AP'):
            assert private not in persisted

        # Unload while explicit discovery is blocked: cancellation, no orphan.
        started = asyncio.Event()
        async def blocked(*args, **kwargs):
            started.set()
            await asyncio.Future()
        with patch.object(hub_module, 'discover', side_effect=blocked), patch.object(
            hass.config_entries, 'async_unload_platforms', AsyncMock(return_value=True)):
            task = asyncio.create_task(sensor.hub.execute('discovery'))
            await started.wait()
            assert await integration.async_unload_entry(hass, entry)
            await asyncio.gather(task, return_exceptions=True)
        assert sensor.hub._task is None
        for name in actions:
            assert not hass.services.has_service('welcomeeye_local', name)
        # Reload/restoration still uses the QV hub and performs no startup I/O.
        with patch.object(hass.config_entries, 'async_forward_entry_setups', AsyncMock()), patch.object(
            integration, 'WelcomeEyeHub', side_effect=AssertionError('legacy reload forbidden')), patch.object(
            integration, 'R002InvestigationHub', side_effect=AssertionError('R002 reload forbidden')):
            assert await integration.async_setup_entry(hass, entry)
        assert isinstance(entry.runtime_data, hub_module.Connect3Hub)
        await entry.runtime_data.stop()
        await hass.async_stop(force=True)
        print('Connect 3 actual HA: family isolation, schema, identity, services, permissions, XML, privacy, unload/reload PASS')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(180):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
