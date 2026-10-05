"""HA entity services provide target resolution and camera permission checks."""
import voluptuous as vol

from homeassistant.auth.permissions.const import POLICY_CONTROL
from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN
from .r002.protocol import ALLOWED_TYPES


def _experimental_udp_port(value):
    if type(value) is not int or not 1 <= value <= 65535:
        raise vol.Invalid('An explicitly established UDP port is required')
    return value


def async_setup_services(hass):
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'capture_snapshot', entity_domain='camera',
        schema={vol.Optional('save_to_media', default=True): cv.boolean},
        func='async_capture_snapshot', supports_response=SupportsResponse.OPTIONAL,
    )
    for name in ('experimental_version_info', 'experimental_additional_camera'):
        service.async_register_platform_entity_service(
            hass, DOMAIN, name, entity_domain='camera',
            schema={vol.Optional('confirm', default=False): cv.boolean},
            func=_async_experimental_diagnostic, supports_response=SupportsResponse.ONLY,
        )
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'experimental_udt_probe', entity_domain='camera',
        schema={vol.Optional('confirm', default=False): cv.boolean,
                vol.Optional('legacy_discovery_absent', default=False): cv.boolean,
                vol.Required('udp_port'): _experimental_udp_port},
        func=_async_experimental_diagnostic, supports_response=SupportsResponse.ONLY,
    )


async def _async_experimental_diagnostic(entity, call):
    from .experimental_diagnostics import ExperimentalDiagnostics

    user_id = call.context.user_id
    user = await entity.hass.auth.async_get_user(user_id) if user_id else None
    # Authorization is repeated by the backend, including the target permission.
    if user is None or not user.is_admin:
        raise HomeAssistantError('An identified administrator is required')
    if not user.permissions.check_entity(entity.entity_id, POLICY_CONTROL):
        raise HomeAssistantError('Control permission is required for this entity')
    hub = entity.hub
    if not hasattr(hub, '_experimental_diagnostics'):
        hub._experimental_diagnostics = ExperimentalDiagnostics(hub)
    operation = {'experimental_version_info': 'version_469',
                 'experimental_additional_camera': 'additional_camera',
                 'experimental_udt_probe': 'udt_handshake'}[call.service]
    kwargs = {key: call.data[key] for key in ('udp_port', 'legacy_discovery_absent') if key in call.data}
    return await hub._experimental_diagnostics.execute(operation,
        confirm=call.data.get('confirm', False), user=user, entity_id=entity.entity_id, **kwargs)


def async_setup_r002_service(hass):
    """Registered only while an R002 entry is loaded; HA enforces entity permissions."""
    if hass.services.has_service(DOMAIN, 'r002_probe'):
        return
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'r002_probe', entity_domain='sensor',
        schema={vol.Optional('types', default=list(ALLOWED_TYPES)): vol.All(
            cv.ensure_list, vol.Length(min=1, max=4), [vol.All(int, vol.In(ALLOWED_TYPES))],
        ), vol.Optional('include_header', default=False): cv.boolean,
        vol.Optional('include_response', default=False): cv.boolean},
        func=_async_r002_probe, supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'r002_check_certificate', entity_domain='sensor', schema={},
        func='async_r002_check_certificate', supports_response=SupportsResponse.ONLY,
    )
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'r002_discover_qv', entity_domain='sensor',
        schema={vol.Optional('include_response', default=False): cv.boolean,
                vol.Optional('include_details', default=False): cv.boolean},
        func=_async_r002_discover_qv, supports_response=SupportsResponse.ONLY,
    )


async def _async_r002_probe(entity, call):
    """HA checks entity control first; raw detail additionally requires an admin."""
    if not getattr(getattr(entity, 'hub', None), 'capabilities', None) or not entity.hub.capabilities.r002_probe:
        raise HomeAssistantError('R002 investigation is unavailable for this device')
    if call.data['include_header'] or call.data['include_response']:
        user_id = call.context.user_id
        user = await entity.hass.auth.async_get_user(user_id) if user_id else None
        if user is None or not user.is_admin:
            raise HomeAssistantError('An identified administrator is required for raw R002 data')
    return await entity.async_r002_probe(call.data['types'], include_header=call.data['include_header'],
                                        include_response=call.data['include_response'])


async def _async_r002_discover_qv(entity, call):
    if not getattr(getattr(entity, 'hub', None), 'capabilities', None) or not entity.hub.capabilities.r002_probe:
        raise HomeAssistantError('R002 investigation is unavailable for this device')
    user_id = call.context.user_id
    user = await entity.hass.auth.async_get_user(user_id) if user_id else None
    if user is None or not user.is_admin:
        raise HomeAssistantError('An identified administrator is required for QV discovery')
    if not user.permissions.check_entity(entity.entity_id, POLICY_CONTROL):
        raise HomeAssistantError('Control permission is required for this R002 entity')
    return await entity.async_r002_discover_qv(include_response=call.data['include_response'],
                                            include_details=call.data['include_details'])


def async_setup_connect3_services(hass):
    if hass.services.has_service(DOMAIN, 'connect3_discover'):
        return
    for name, fields in {
        'connect3_discover': {vol.Optional('include_details', default=False): cv.boolean},
        'connect3_check_certificate': {vol.Optional('include_details', default=False): cv.boolean},
        'connect3_check_media_certificate': {vol.Optional('include_details', default=False): cv.boolean},
        'connect3_check_access': {},
        'connect3_observe_doorbell': {
            vol.Required('operation'): vol.In(('start', 'mark', 'status', 'stop')),
            vol.Optional('duration', default=90): vol.All(int, vol.Range(min=30, max=120))},
        'connect3_list_records': {
            vol.Required('start'): str, vol.Required('end'): str,
            vol.Optional('channel', default=1): vol.All(int, vol.Range(min=1, max=64)),
            vol.Optional('include_details', default=False): cv.boolean},
    }.items():
        service.async_register_platform_entity_service(hass, DOMAIN, name,
            entity_domain='sensor', schema=fields, func=_async_connect3_read,
            supports_response=SupportsResponse.ONLY)


async def _async_connect3_read(entity, call):
    capabilities = getattr(getattr(entity, 'hub', None), 'capabilities', None)
    if not capabilities or not capabilities.connect3_read:
        raise HomeAssistantError('Connect 3 read is unavailable for this device')
    user_id = call.context.user_id
    user = await entity.hass.auth.async_get_user(user_id) if user_id else None
    if user is None or not user.is_admin:
        raise HomeAssistantError('An identified administrator is required for Connect 3 investigation')
    if not user.permissions.check_entity(entity.entity_id, POLICY_CONTROL):
        raise HomeAssistantError('Control permission is required for this Connect 3 entity')
    if call.service == 'connect3_observe_doorbell':
        return await entity.async_connect3_observe_doorbell(call.data['operation'], call.data['duration'])
    operation = {'connect3_discover': 'discovery', 'connect3_check_access': 'access',
                 'connect3_check_certificate': 'certificate',
                 'connect3_check_media_certificate': 'media_certificate',
                 'connect3_list_records': 'history'}[call.service]
    kwargs = {key: call.data[key] for key in ('include_details', 'start', 'end', 'channel') if key in call.data}
    return await entity.async_connect3_read(operation, **kwargs)
