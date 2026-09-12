"""Privacy-safe downloadable diagnostics for WelcomeEye support."""
from dataclasses import asdict


VERSION = "0.3.0-beta.6"


def _is_active(value):
    return value is not None


async def async_get_config_entry_diagnostics(hass, entry):
    """Return useful runtime state without exposing device secrets or identifiers."""
    hub = entry.runtime_data
    thread = getattr(hub, "thread", None)
    control = getattr(hub, "control", None)
    tlv_counts = getattr(hub, "media_tlv_counts", {})
    webrtc = dict(getattr(hub, "webrtc_diagnostics", {}))

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
            "last_error_message": getattr(hub, "last_error_message", None),
        },
        "media": {
            "stream_format": asdict(hub.format) if hub.format else None,
            "format_available": hub.format is not None,
            "last_media_tlv": getattr(hub, "last_media_tlv", None),
            "selected_media_profile": getattr(hub, "selected_media_profile", None),
            "current_media_profile": getattr(hub, "current_profile", None),
            "profile_attempts": getattr(hub, "profile_attempts", 0),
            "video_packets_received": getattr(hub, "video_packets_received", 0),
            "tlv_counts": {
                "format_203": tlv_counts.get(203, 0),
                "media_98": tlv_counts.get(98, 0),
                "media_100": tlv_counts.get(100, 0),
                "media_101": tlv_counts.get(101, 0),
            },
        },
        "webrtc": {
            "stage": webrtc.get("stage"),
            "failed_at_stage": webrtc.get("failed_at_stage"),
            "last_exception_type": webrtc.get("last_exception_type"),
            "connection_state": webrtc.get("connection_state"),
            "requested_tracks": webrtc.get("requested_tracks", []),
            "created_tracks": webrtc.get("created_tracks", []),
            "active_viewers": webrtc.get("active_viewers", 0),
            "candidate_event": webrtc.get("candidate_event"),
        },
        "control": {
            "session_active": bool(control and control.session is not None),
            "busy": bool(control and control.lock.locked()),
            "closed": bool(control and control.closed.is_set()),
        },
        "doorbell": {
            "connected": hub.ring_connected,
            "ring_count": hub.ring_count,
            "ringing": hub.ringing,
            "last_error_type": hub.ring_error,
        },
        "privacy": {
            "device_ip_included": False,
            "username_included": False,
            "password_included": False,
            "device_uid_included": False,
            "internal_stream_url_included": False,
            "media_payloads_included": False,
            "sdp_included": False,
            "ice_candidate_values_included": False,
        },
    }
