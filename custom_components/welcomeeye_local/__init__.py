"""Local Philips WelcomeEye integration."""
from homeassistant.config_entries import ConfigEntry
from homeassistant.const import EVENT_HOMEASSISTANT_STOP, Platform
from homeassistant.core import HomeAssistant
from homeassistant.exceptions import ConfigEntryAuthFailed, ConfigEntryError, ConfigEntryNotReady
from homeassistant.helpers import config_validation as cv
from homeassistant.helpers import entity_registry as er

from . import diagnostics as integration_diagnostics
from .client import AuthenticationError
from .hub import WelcomeEyeHub
from .capabilities import DeviceVariant, MATRIX, unsupported_entity_ids, variant_for
from .r002.hub import R002InvestigationHub
from .connect3.hub import Connect3Hub

PLATFORMS = [Platform.CAMERA, Platform.BINARY_SENSOR, Platform.SENSOR, Platform.BUTTON, Platform.IMAGE, Platform.SWITCH]

from .const import DOMAIN, VERSION

CONFIG_SCHEMA = cv.config_entry_only_config_schema(DOMAIN)

integration_diagnostics.VERSION = VERSION


async def async_setup(hass, config):
    from .player import async_setup_player
    from .services import async_setup_services
    await async_setup_player(hass)
    async_setup_services(hass)
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
    try:
        variant = variant_for(entry.data)
    except ValueError as exc:
        raise ConfigEntryError('Unsupported WelcomeEye protocol family') from exc
    if variant == DeviceVariant.R002:
        hub = R002InvestigationHub(hass, entry)
        if hub.capabilities.live_media:
            await hass.async_add_executor_job(_preload_dns_types)
    elif variant == DeviceVariant.CONNECT3:
        hub = Connect3Hub(hass, entry)
        if hub.capabilities.live_media:
            await hass.async_add_executor_job(_preload_dns_types)
    else:
        await hass.async_add_executor_job(_preload_dns_types)
        hub = WelcomeEyeHub(hass, entry)
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
        registry = er.async_get(hass)
        for entity_id in unsupported_entity_ids(
            er.async_entries_for_config_entry(registry, entry.entry_id), entry.entry_id,
            entry.unique_id, hub.capabilities,
        ):
            registry.async_remove(entity_id)
        await hass.config_entries.async_forward_entry_setups(entry, PLATFORMS)
    except BaseException:
        await hub.stop()
        raise
    if hub.capabilities.r002_probe:
        from .services import async_setup_r002_service
        async_setup_r002_service(hass)
    if hub.capabilities.connect3_read:
        from .services import async_setup_connect3_services
        async_setup_connect3_services(hass)
    async def async_shutdown(event):
        if diagnostic := getattr(hub, '_experimental_diagnostics', None):
            await diagnostic.stop()
        await hub.stop(reason='home_assistant_stop')

    entry.async_on_unload(hass.bus.async_listen_once(EVENT_HOMEASSISTANT_STOP, async_shutdown))
    return True


async def async_unload_entry(hass: HomeAssistant, entry: ConfigEntry) -> bool:
    if await hass.config_entries.async_unload_platforms(entry, PLATFORMS):
        if diagnostic := getattr(entry.runtime_data, '_experimental_diagnostics', None):
            await diagnostic.stop()
        await entry.runtime_data.stop()
        if entry.runtime_data.capabilities.connect3_read and not any(
            other.entry_id != entry.entry_id
            and isinstance(getattr(other, 'runtime_data', None), Connect3Hub)
            and other.runtime_data.capabilities.connect3_read
            and not other.runtime_data.stopped
            for other in hass.config_entries.async_entries(DOMAIN)
        ):
            for name in ('connect3_discover', 'connect3_check_certificate', 'connect3_check_media_certificate', 'connect3_check_access', 'connect3_list_records', 'connect3_observe_doorbell'):
                hass.services.async_remove(DOMAIN, name)
        if entry.runtime_data.capabilities.r002_probe and not any(
            other.entry_id != entry.entry_id
            and isinstance(getattr(other, 'runtime_data', None), R002InvestigationHub)
            and not other.runtime_data.stopped
            for other in hass.config_entries.async_entries(DOMAIN)
        ):
            hass.services.async_remove(DOMAIN, 'r002_probe')
            hass.services.async_remove(DOMAIN, 'r002_check_certificate')
            hass.services.async_remove(DOMAIN, 'r002_discover_qv')
            for name in ('r002_check_access', 'r002_check_qv_certificate', 'r002_observe_doorbell'):
                hass.services.async_remove(DOMAIN, name)
        return True
    return False


async def async_remove_entry(hass: HomeAssistant, entry: ConfigEntry) -> None:
    """Remove a Connect 3 trust issue when its configured device is deleted."""
    try:
        variant = variant_for(entry.data)
    except ValueError:
        return
    if variant == DeviceVariant.CONNECT3:
        from .repairs import async_clear_tls_issue
        async_clear_tls_issue(hass, entry.entry_id)
