"""Local Philips WelcomeEye integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv

from . import diagnostics as integration_diagnostics
from .client import AuthenticationError
from .hub import WelcomeEyeHub

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON, Platform.IMAGE]

from .const import DOMAIN, VERSION

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

integration_diagnostics.VERSION = VERSION


async def async_setup(hass, config):
    from .player import async_setup_player
    await async_setup_player(hass)
    return True


def _preload_dns_types() -> None:
    """Warm dnspython classes used by aioice mDNS outside the event loop."""
    import dns.rdata
    import dns.rdatatype

    dns.rdata.load_all_types(disable_dynamic_load=False)
    mdns_rdclass = 1 | 0x8000
    for rdtype in dns.rdatatype.RdataType:
        dns.rdata.get_rdata_class(mdns_rdclass, rdtype, True)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    await hass.async_add_executor_job(_preload_dns_types)
    hub = WelcomeEyeHub(hass, entry)
    # Experimental branch: use the existing Connect 2 listener (0/3/0)
    # on V1 as well. Receiving actual V1 rings is NOT yet validated.
    # No new subscription, output command or media reader is added.
    hub.v1_doorbell_standby = False
    try:
        await hub.start()
    except AuthenticationError as exc:
        await hub.stop()
        raise ConfigEntryAuthFailed('Authentication refused by WelcomeEye') from exc
    except Exception as exc:
        await hub.stop()
        raise ConfigEntryNotReady('Cannot connect to WelcomeEye') from exc
    except BaseException:
        await hub.stop()
        raise
    entry.runtime_data = hub
    try:
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    async def async_shutdown(event):
        await hub.stop(reason='home_assistant_stop')

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_shutdown))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        await entry.runtime_data.stop()
        return True
    return False
