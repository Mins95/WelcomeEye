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


def identity(identifier='synthetic-admin', *, admin=True, control=True):
    return SimpleNamespace(id=identifier, is_admin=admin,
        permissions=SimpleNamespace(check_entity=Mock(return_value=control)))


async def main(root):
    sys.path.insert(0, str(root / 'tests'))
    from load_integration import load
    services = load('services')
    backend = load('experimental_diagnostics')
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
        hass.data[DATA_DOMAIN_PLATFORM_ENTITIES] = {
            ('camera', 'welcomeeye_local'): {
                camera.entity_id: camera, other_camera.entity_id: other_camera,
            },
        }
        services.async_setup_services(hass)
        admin = identity()
        hass.auth = SimpleNamespace(async_get_user=AsyncMock(return_value=admin))
        names = ('experimental_version_info', 'experimental_additional_camera')
        before = json.dumps([dict(entry.data), dict(entry.options)])
        try:
            for name in names:
                assert hass.services.supports_response('welcomeeye_local', name) is SupportsResponse.ONLY
            with patch.object(backend, '_DiagnosticSession', side_effect=AssertionError('Device I/O forbidden')) as factory:
                for name in names:
                    # Default confirmation false: actual schema, dispatcher,
                    # backend and target resolution all run without a session.
                    answer = await hass.services.async_call('welcomeeye_local', name,
                        {'entity_id': camera.entity_id}, blocking=True, return_response=True,
                        context=Context(user_id=admin.id))
                    assert answer[camera.entity_id]['reason'] == 'explicit_confirmation_required'
                    for who, context in ((None, Context()), (identity(admin=False), Context(user_id='nonadmin')),
                            (identity(admin=False, control=False), Context(user_id='denied')),
                            (identity(control=False), Context(user_id='admin-no-control'))):
                        hass.auth.async_get_user.return_value = who
                        try:
                            await hass.services.async_call('welcomeeye_local', name,
                                {'entity_id': camera.entity_id, 'confirm': True},
                                blocking=True, return_response=True, context=context)
                        except (HomeAssistantError, PermissionError):
                            pass
                        else:
                            raise AssertionError('Experimental action bypassed identified-admin/control checks')
                    hass.auth.async_get_user.return_value = admin
                    answer = await hass.services.async_call('welcomeeye_local', name,
                        {'entity_id': other_camera.entity_id, 'confirm': True}, blocking=True,
                        return_response=True, context=Context(user_id=admin.id))
                    assert answer[other_camera.entity_id]['reason'] == 'legacy_protocol_required'
                    # HA's target routing must not redirect an unknown entity
                    # to a configured legacy target, and no trial is consumed.
                    try:
                        unknown = await hass.services.async_call('welcomeeye_local', name,
                            {'entity_id': 'camera.synthetic_absent', 'confirm': True},
                            blocking=True, return_response=True, context=Context(user_id=admin.id))
                    except HomeAssistantError:
                        pass
                    else:
                        assert camera.entity_id not in unknown
                    hub.ring_listener.thread = Mock(is_alive=Mock(return_value=True))
                    answer = await hass.services.async_call('welcomeeye_local', name,
                        {'entity_id': camera.entity_id, 'confirm': True}, blocking=True,
                        return_response=True, context=Context(user_id=admin.id))
                    assert answer[camera.entity_id]['reason'] == 'busy'
                    hub.ring_listener.thread = None
                    # Run one authorized synthetic worker via real dispatcher.
                    # Its returned marker must not be added to entity/config.
                    operation = 'version_469' if name == names[0] else 'additional_camera'
                    with patch.object(hub._experimental_diagnostics, '_run', return_value={
                            'operation': operation, 'status': 'observed', 'synthetic': True}) as worker:
                        answer = await hass.services.async_call('welcomeeye_local', name,
                            {'entity_id': camera.entity_id, 'confirm': True}, blocking=True,
                            return_response=True, context=Context(user_id=admin.id))
                    worker.assert_called_once()
                    assert answer[camera.entity_id]['synthetic'] is True
                    repeat = await hass.services.async_call('welcomeeye_local', name,
                        {'entity_id': camera.entity_id, 'confirm': True}, blocking=True,
                        return_response=True, context=Context(user_id=admin.id))
                    assert repeat[camera.entity_id]['reason'] == 'already_attempted_for_loaded_entry'
                factory.assert_not_called()
            assert before == json.dumps([dict(entry.data), dict(entry.options)])
            assert 'synthetic' not in json.dumps(camera.extra_state_attributes)
            assert hub._experimental_diagnostics._session is None
            assert hub._experimental_diagnostics._task is None
            assert not hub.lock.locked() and not hub.control.lock.locked()
            await hub._experimental_diagnostics.stop()
        finally:
            await hass.async_stop(force=True)
    print('REAL_HA_EXPERIMENTAL_OPT_IN_TARGET_PERMISSION_NO_DEVICE_IO_AND_SINGLE_TRIAL_OK')


if __name__ == '__main__':
    async def bounded():
        async with asyncio.timeout(60):
            await main(Path(__file__).resolve().parents[1])
    asyncio.run(bounded())
