"""Local Philips WelcomeEye integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryNotReady

from . import diagnostics as integration_diagnostics
from .client import AuthenticationError
from .hub import WelcomeEyeHub
from .standby_ring import StandbyRingListener

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON]

# Keep downloadable diagnostics aligned with the manifest for this beta.
integration_diagnostics.VERSION = "0.3.1-beta.6"


def _preload_dns_types() -> None:
    """Preload dnspython record implementations outside the HA event loop."""
    import dns.rdata

    dns.rdata.load_all_types(disable_dynamic_load=False)


async def async_setup_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    # aioice/mDNS may ask dnspython for record handlers while WebRTC is active.
    # Load those modules in HA's executor before any platform can create an
    # RTCPeerConnection, avoiding dynamic imports from the event loop.
    await hass.async_add_executor_job(_preload_dns_types)

    hub = WelcomeEyeHub(hass, entry)

    # Connect V1 doorbell support is temporarily on standby.  Do this before
    # hub.start(), so no persistent V1 control-session listener can seize the
    # single control path used by door/gate output commands.  Connect 2 keeps
    # the real RingListener unchanged.
    if hub.device_model == "WelcomeEye Connect V1":
        hub.v1_doorbell_standby = True
        hub.ring_listener.close()
        hub.ring_listener = StandbyRingListener()
        hub.ring_connected = False
        hub.ringing = False
    else:
        hub.v1_doorbell_standby = False

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
