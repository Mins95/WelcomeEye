"""HA entity services provide target resolution and camera permission checks."""
import voluptuous as vol

from homeassistant.core import SupportsResponse
from homeassistant.helpers import config_validation as cv, service

from .const import DOMAIN


def async_setup_services(hass):
    service.async_register_platform_entity_service(
        hass, DOMAIN, 'capture_snapshot', entity_domain='camera',
        schema={vol.Optional('save_to_media', default=True): cv.boolean},
        func='async_capture_snapshot', supports_response=SupportsResponse.OPTIONAL,
    )
