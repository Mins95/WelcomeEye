"""HA entity services provide target resolution and camera permission checks."""
import voluptuous as vol

from homeassistant.core import SupportsResponse
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
        )},
        func='async_r002_probe', supports_response=SupportsResponse.ONLY,
    )
