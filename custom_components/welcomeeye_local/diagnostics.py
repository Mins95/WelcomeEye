"""Diagnostics deliberately omit passwords, UID, media and internal stream URL."""
from dataclasses import asdict


async def async_get_config_entry_diagnostics(hass, entry):
    hub = entry.runtime_data
    return {'connected': hub.connected,
            'stream_format': asdict(hub.format) if hub.format else None,
            'last_error_type': type(hub.error).__name__ if hub.error else None,
            'active_consumers': len(hub.consumers),
            'version': '0.3.0-beta.3'}
