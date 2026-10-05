"""Real HA entity-service dispatch and permissions; device I/O is forbidden.

Run inside the isolated supported HA Core images. All identities/targets are
synthetic. The network session factory is an assertion tripwire, never a socket.
"""
import asyncio
import json
from pathlib import Path
import sys
import tempfile
import threading
from types import MappingProxyType, SimpleNamespace
from unittest.mock import AsyncMock, Mock, patch

from homeassistant.components.camera import Camera
from homeassistant.config_entries import ConfigEntries, ConfigEntry
from homeassistant.core import Context, HomeAssistant, SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers.entity_platform import DATA_DOMAIN_PLATFORM_ENTITIES

# Import after HA installs its supported validation backend (probatio on 2026.9).
import voluptuous as vol


def identity(identifier='synthetic-admin', *, admin=True, control=True):
    return SimpleNamespace(id=identifier, is_admin=admin,
        permissions=SimpleNamespace(check_entity=Mock(return_value=control)))


async def main(root):
    sys.path.insert(0, str(root / 'tests'))
    from load_integration import load
    services = load('services')
    backend = load('experimental_diagnostics')
    udt = load('experimental_udt')
    capabilities = load('capabilities')
    class SyntheticCamera(Camera):
        def __init__(self, hub):
            super().__init__()
            self.hub = hub

    with tempfile.TemporaryDirectory() as temporary:
        hass = HomeAssistant(temporary)
        hass.config_entries = ConfigEntries(hass, {})
        entry = ConfigEntry(version=1, minor_version=1, domain='welcomeeye_local',
            title='Legacy experimental synthetic fixture', unique_id='synthetic-legacy-id',
            data={'host': '192.0.2.1', 'username': 'synthetic-user', 'password': 'synthetic-secret'},
            options={}, source='user', subentries_data=None, discovery_keys=MappingProxyType({}))
        hass.config_entries._entries[entry.entry_id] = entry
        hub = SimpleNamespace(entry=entry, protocol_family=capabilities.ProtocolFamily.LEGACY,
            variant=capabilities.DeviceVariant.V1, stopped=False, consumers=set(),
            session=None, thread=None, lock=asyncio.Lock(),
            control=SimpleNamespace(lock=threading.Lock()),
            ring_listener=SimpleNamespace(thread=None, session=None),
            ring_image=None, manual_snapshot=None)
        camera = SyntheticCamera(hub)
        camera.hass, camera.entity_id = hass, 'camera.synthetic_experimental'
        other_hub = SimpleNamespace(**vars(hub))
        other_hub.protocol_family = capabilities.ProtocolFamily.CONNECT3
        other_camera = SyntheticCamera(other_hub)
        other_camera.hass, other_camera.entity_id = hass, 'camera.synthetic_connect3'
        r002_hub = SimpleNamespace(**vars(hub))
        r002_hub.protocol_family = capabilities.ProtocolFamily.R002
        r002_camera = SyntheticCamera(r002_hub)
        r002_camera.hass, r002_camera.entity_id = hass, 'camera.synthetic_r002'
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {
            ('camera', 'welcomeeye_local'): {
                camera.entity_id: camera, other_camera.entity_id: other_camera,
                r002_camera.entity_id: r002_camera,
            },
        }
        services.async_setup_services(hass)
        admin = identity()
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=admin))
        names = ('experimental_version_info', 'experimental_additional_camera', 'experimental_udt_probe')
        before = json.dumps([dict(entry.data), dict(entry.options)])
        before_attributes = json.dumps(camera.extra_state_attributes)

        async def action(name, data=None, *, entity_id=None, context=None):
            fields = {'udp_port': 12345} if name == 'experimental_udt_probe' else {}
            return await hass.services.async_call('welcomeeye_local', name,
                {'entity_id': entity_id or camera.entity_id, **fields, **(data or {})},
                blocking=True, return_response=True,
                context=context if context is not None else Context(user_id=admin.id))

        try:
            for name in names:
                assert hass.services.supports_response('welcomeeye_local', name) is SupportsResponse.ONLY
            with patch.object(backend, '_DiagnosticSession', side_effect=AssertionError('Device I/O forbidden')) as factory, patch.object(
                udt, 'probe_handshake', side_effect=AssertionError('UDT device I/O forbidden')) as probe:
                for name in names:
                    confirmed = {'confirm': True}
                    if name == 'experimental_udt_probe':
                        confirmed['legacy_discovery_absent'] = True
                    # Default confirmation false: actual schema, dispatcher,
                    # backend and target resolution all run without a session.
                    answer = await action(name)
                    assert answer[camera.entity_id]['reason'] == 'explicit_confirmation_required'
                    for who, context in ((None, Context()), (identity(admin=False), Context(user_id='nonadmin')),
                            (identity(admin=False, control=False), Context(user_id='denied')),
                            (identity(control=False), Context(user_id='admin-no-control'))):
                        hass.auth.async_get_user.return_value = who
                        try:
                            await action(name, confirmed, context=context)
                        except (HomeAssistantError, PermissionError):
                            pass
                        else:
                            raise AssertionError('Experimental action bypassed identified-admin/control checks')
                    hass.auth.async_get_user.return_value = admin
                    for incompatible in (other_camera, r002_camera):
                        answer = await action(name, confirmed, entity_id=incompatible.entity_id)
                        assert answer[incompatible.entity_id]['reason'] == 'legacy_protocol_required'
                        assert not incompatible.hub._experimental_diagnostics._attempted
                    if name == 'experimental_udt_probe':
                        answer = await action(name, {'confirm': True})
                        assert answer[camera.entity_id]['reason'] == 'legacy_discovery_absence_required'
                        for port in (0, -1, 65536, 'not-a-port'):
                            try:
                                await action(name, {**confirmed, 'udp_port': port})
                            except (HomeAssistantError, ValueError, vol.Invalid):
                                pass
                            else:
                                raise AssertionError('UDT port outside 1..65535 accepted')
                        for port in (1, 65535):
                            answer = await action(name, {'udp_port': port})
                            assert answer[camera.entity_id]['reason'] == 'explicit_confirmation_required'
                        try:
                            await hass.services.async_call('welcomeeye_local', name,
                                {'entity_id': camera.entity_id, **confirmed}, blocking=True,
                                return_response=True, context=Context(user_id=admin.id))
                        except (HomeAssistantError, ValueError, vol.Invalid):
                            pass
                        else:
                            raise AssertionError('UDT action accepted missing udp_port')
                    # HA's target routing must not redirect an unknown entity
                    # to a configured legacy target, and no trial is consumed.
                    try:
                        unknown = await action(name, confirmed, entity_id='camera.synthetic_absent')
                    except HomeAssistantError:
                        pass
                    else:
                        assert camera.entity_id not in unknown
                    hub.ring_listener.thread = Mock(is_alive=Mock(return_value=True))
                    answer = await action(name, confirmed)
                    assert answer[camera.entity_id]['reason'] == 'busy'
                    hub.ring_listener.thread = None
                    hub.consumers.add('existing-viewer')
                    answer = await action(name, confirmed)
                    assert answer[camera.entity_id]['reason'] == 'busy'
                    assert hub.consumers == {'existing-viewer'}
                    hub.consumers.clear()
                    # Run one authorized synthetic worker via real dispatcher.
                    # Its returned marker must not be added to entity/config.
                    operation = {'experimental_version_info': 'version_469',
                                 'experimental_additional_camera': 'additional_camera',
                                 'experimental_udt_probe': 'udt_handshake'}[name]
                    worker_method = '_run_udt' if name == 'experimental_udt_probe' else '_run'
                    assert operation not in hub._experimental_diagnostics._attempted
                    safe_report = {'operation': operation, 'status': 'observed'}
                    safe_report.update({'handshake_accepted': True, 'last_stage': 'reply_received',
                                        'attempt_count': 1} if name == 'experimental_udt_probe' else {'synthetic': True})
                    with patch.object(hub._experimental_diagnostics, worker_method, return_value=safe_report) as worker:
                        answer = await action(name, confirmed)
                    worker.assert_called_once()
                    assert operation in hub._experimental_diagnostics._attempted
                    if name == 'experimental_udt_probe':
                        assert answer[camera.entity_id]['handshake_accepted'] is True
                        assert worker.call_args.args[0] == 12345
                    else:
                        assert answer[camera.entity_id]['synthetic'] is True
                    repeat_fields = {**confirmed, 'udp_port': 12346} if name == 'experimental_udt_probe' else confirmed
                    repeat = await action(name, repeat_fields)
                    assert repeat[camera.entity_id]['reason'] == 'already_attempted_for_loaded_entry'
                factory.assert_not_called()
                probe.assert_not_called()
            assert before == json.dumps([dict(entry.data), dict(entry.options)])
            assert before_attributes == json.dumps(camera.extra_state_attributes)
            assert 'synthetic' not in json.dumps(camera.extra_state_attributes)
            persisted = repr(vars(hub._experimental_diagnostics))
            assert '12345' not in persisted and 'handshake_accepted' not in persisted
            assert hub._experimental_diagnostics._session is None
            assert hub._experimental_diagnostics._task is None
            assert not hub.lock.locked() and not hub.control.lock.locked()
            await hub._experimental_diagnostics.stop()
        finally:
            await hass.async_stop(force=True)
    print('REAL_HA_EXPERIMENTAL_OPT_IN_TARGET_PERMISSION_UDT_ENDPOINT_NO_DEVICE_IO_AND_SINGLE_TRIAL_OK')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(60):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
