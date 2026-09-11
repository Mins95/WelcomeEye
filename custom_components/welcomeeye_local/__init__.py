"""Local Philips WelcomeEye Connect 2 integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from .client import AuthenticationError
from .hub import WelcomeEyeHub

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    hub = WelcomeEyeHub(hass, entry)
    try:
        await hub.start()
    except AuthenticationError as exc:
        raise ConfigEntryAuthFailed('Authentication refused by WelcomeEye') from exc
    except Exception as exc:
        raise ConfigEntryNotReady('Cannot connect to WelcomeEye') from exc
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    async def async_shutdown(event):
        await hub.stop()

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_shutdown))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False
