"""Privacy-safe downloadable diagnostics for WelcomeEye support."""
from dataclasses import asdict


VERSION = "0.3.0-beta.7"


def _is_active(value):
    return value is not None


def _counter_map(values):
    return {str(key): values[key] for key in sorted(values)}


async def async_get_config_entry_diagnostics(hass, entry):
    """Return useful runtime state without exposing device secrets or payloads."""
    hub = entry.runtime_data
    thread = getattr(hub, "thread", None)
    control = getattr(hub, "control", None)
    ring = getattr(hub, "ring_listener", None)
    tlv_counts = getattr(hub, "media_tlv_counts", {})
    webrtc = dict(getattr(hub, "webrtc_diagnostics", {}))
    announced = getattr(hub, "last_announced_format", None)

    return {
        "integration": {
            "version": VERSION,
            "domain": entry.domain,
        },
        "device": {
            "model": getattr(hub, "device_model", "WelcomeEye"),
            "model_confidence": getattr(hub, "device_model_confidence", "unknown"),
            "model_source": getattr(hub, "device_model_source", None),
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
            "last_announced_stream_format": asdict(announced) if announced else None,
            "last_media_tlv": getattr(hub, "last_media_tlv", None),
            "selected_media_profile": getattr(hub, "selected_media_profile", None),
            "current_media_profile": getattr(hub, "current_profile", None),
            "profile_attempts": getattr(hub, "profile_attempts", 0),
            "video_packets_received": getattr(hub, "video_packets_received", 0),
            "selected_video_tlv": getattr(hub, "selected_video_tlv", None),
            "tlv_counts": {
                "media_97": tlv_counts.get(97, 0),
                "media_98": tlv_counts.get(98, 0),
                "media_99": tlv_counts.get(99, 0),
                "media_100": tlv_counts.get(100, 0),
                "media_101": tlv_counts.get(101, 0),
                "format_203": tlv_counts.get(203, 0),
            },
            "h264_detection": {
                "detected_by_tlv": _counter_map(
                    getattr(hub, "h264_detected_counts", {})
                ),
                "idr_by_tlv": _counter_map(
                    getattr(hub, "h264_idr_counts", {})
                ),
                "nal_types_seen": sorted(getattr(hub, "h264_nal_types", set())),
                "framing_counts": dict(
                    sorted(getattr(hub, "h264_framing_counts", {}).items())
                ),
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
            "last_error_type": getattr(ring, "last_error_type", None) or hub.ring_error,
            "last_error_message": getattr(ring, "last_error_message", None),
            "last_error_stage": getattr(ring, "last_error_stage", None),
            "connection_attempts": getattr(ring, "connection_attempts", 0),
            "last_top_level_tlv": getattr(ring, "last_top_level_tlv", None),
            "top_level_tlv_counts": _counter_map(
                getattr(ring, "tlv_counts", {})
            ),
            "inner_tlv_counts": _counter_map(
                getattr(ring, "inner_tlv_counts", {})
            ),
            "alarm_type_counts": _counter_map(
                getattr(ring, "alarm_type_counts", {})
            ),
            "candidate_ring_types": getattr(ring, "candidate_ring_types", []),
            "decode_failures": getattr(ring, "decode_failures", 0),
        },
        "privacy": {
            "device_ip_included": False,
            "username_included": False,
            "password_included": False,
            "device_uid_included": False,
            "internal_stream_url_included": False,
            "media_payloads_included": False,
            "alarm_payloads_included": False,
            "alarm_timestamps_included": False,
            "sdp_included": False,
            "ice_candidate_values_included": False,
        },
    }
