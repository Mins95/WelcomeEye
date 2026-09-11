"""Privacy-safe downloadable diagnostics for WelcomeEye support."""
from dataclasses import asdict


VERSION = "0.3.0-beta.4"


def _is_active(value):
    return value is not None


async def async_get_config_entry_diagnostics(hass, entry):
    """Return useful runtime state without exposing device secrets or identifiers."""
    hub = entry.runtime_data
    thread = getattr(hub, "thread", None)
    control = getattr(hub, "control", None)

    return {
        "integration": {
            "version": VERSION,
            "domain": entry.domain,
        },
        "configuration": {
            "channel": entry.data.get("channel", 16),
            "host_redacted": "host" in entry.data,
            "username_redacted": "username" in entry.data,
            "password_redacted": "password" in entry.data,
            "unique_id_redacted": bool(entry.unique_id),
        },
        "runtime": {
            "connected": bool(hub.connected),
            "stopped": bool(hub.stopped),
            "connection_count": hub.connection_count,
            "active_consumers": len(hub.consumers),
            "media_worker_alive": bool(thread and thread.is_alive()),
            "media_session_active": _is_active(hub.session),
            "proxy_clients": len(hub.handlers),
            "stream_queues": len(hub.queues),
            "buffer_chunks": len(hub.buffer),
            "buffer_bytes": hub.buffer_size,
            "has_snapshot": hub.image is not None,
            "last_error_type": type(hub.error).__name__ if hub.error else None,
        },
        "media": {
            "stream_format": asdict(hub.format) if hub.format else None,
            "format_available": hub.format is not None,
        },
        "control": {
            "session_active": bool(control and control.session is not None),
            "busy": bool(control and control.lock.locked()),
            "closed": bool(control and control.closed.is_set()),
        },
        "privacy": {
            "device_ip_included": False,
            "username_included": False,
            "password_included": False,
            "device_uid_included": False,
            "internal_stream_url_included": False,
            "media_payloads_included": False,
        },
    }
