"""HA entity services provide target resolution and camera permission checks."""
import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.exceptions import HomeAssistantError
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN
from .r002.protocol import ALLOWED_TYPES


def async_setup_services(hass):
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'capture_snapshot', entity_domain='camera',
        schema={vol.Optional('save_to_media', default=True): cv.boolean},
        func='async_capture_snapshot', supports_response=SupportsResponse.OPTIONAL,
    )


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
        schema={vol.Optional('include_response', default=False): cv.boolean},
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
    return await entity.async_r002_discover_qv(include_response=call.data['include_response'])


def async_setup_connect3_services(hass):
    if hass.services.has_service(DOMAIN, 'connect3_discover'):
        return
    for name, fields in {
        'connect3_discover': {vol.Optional('include_details', default=False): cv.boolean},
        'connect3_check_access': {},
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
    operation = {'connect3_discover': 'discovery', 'connect3_check_access': 'access',
                 'connect3_list_records': 'history'}[call.service]
    kwargs = {key: call.data[key] for key in ('include_details', 'start', 'end', 'channel') if key in call.data}
    return await entity.async_connect3_read(operation, **kwargs)
