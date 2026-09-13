"""Privacy-safe downloadable diagnostics for WelcomeEye support."""
from dataclasses import asdict

from .client import discovery_diagnostics


VERSION = "0.3.0-beta.15"


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
            "all_top_level_tlv_counts": _counter_map(
                getattr(hub, "media_all_tlv_counts", {})
            ),
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
                "detected_any_tlv": _counter_map(
                    getattr(hub, "h264_any_tlv_counts", {})
                ),
                "idr_any_tlv": _counter_map(
                    getattr(hub, "h264_any_idr_counts", {})
                ),
            },
            "v1_apk_compat": {
                "apk_profile": "channel16_stream1_mode2",
                "apk_profile_used": getattr(hub, "v1_apk_profile_used", False),
                "apk_profile_attempts": getattr(hub, "lt_apk_profile_attempts", 0),
                "start_av_request_tlv": 5007,
                "start_av_response_tlv": 5008,
                "start_av_request_sent": getattr(hub, "lt_start_av_request_sent", 0),
                "start_av_request_errors": getattr(hub, "lt_start_av_request_errors", 0),
                "start_av_response_count": getattr(hub, "lt_start_av_response_count", 0),
                "start_av_decode_failures": getattr(hub, "lt_start_av_decode_failures", 0),
                "start_av_result": getattr(hub, "lt_start_av_result", None),
                "start_av_reply_reserved": getattr(hub, "lt_start_av_reply_reserved", None),
                "stop_av_request_tlv": 5009,
                "stop_av_response_tlv": 5010,
                "stop_av_request_sent": getattr(hub, "lt_stop_av_request_sent", 0),
                "stop_av_request_errors": getattr(hub, "lt_stop_av_request_errors", 0),
                "stop_av_response_count": getattr(hub, "lt_stop_av_response_count", 0),
                "stop_av_decode_failures": getattr(hub, "lt_stop_av_decode_failures", 0),
                "stop_av_result": getattr(hub, "lt_stop_av_result", None),
                "query_stream_mode_sent": getattr(hub, "lt_query_stream_mode_sent", 0),
                "private_request_errors": getattr(hub, "lt_private_request_errors", 0),
                "private_response_count": getattr(hub, "lt_private_response_count", 0),
                "private_decode_failures": getattr(hub, "lt_private_decode_failures", 0),
                "last_manu_command": getattr(hub, "lt_last_manu_command", None),
                "last_manu_subcommand": getattr(hub, "lt_last_manu_subcommand", None),
                "stream_mode_wire": getattr(hub, "lt_stream_mode_wire", None),
                "stream_mode_app": getattr(hub, "lt_stream_mode_app", None),
            },
            "transport_framing": getattr(hub, "media_framing_diagnostics", {}),
            "v1_video_structure": (
                hub.v1_video_diagnostics.snapshot()
                if getattr(hub, "v1_video_diagnostics", None) is not None else None
            ),
            "v1_video_previous_sessions": list(
                getattr(hub, "v1_video_previous_sessions", ())
            ),
        },
        "webrtc": {
            "stage": webrtc.get("stage"),
            "failed_at_stage": webrtc.get("failed_at_stage"),
            "last_exception_type": webrtc.get("last_exception_type"),
            "connection_state": webrtc.get("connection_state"),
            "ice_connection_state": webrtc.get("ice_connection_state"),
            "ice_gathering_state": webrtc.get("ice_gathering_state"),
            "signaling_state": webrtc.get("signaling_state"),
            "requested_tracks": webrtc.get("requested_tracks", []),
            "created_tracks": webrtc.get("created_tracks", []),
            "active_viewers": webrtc.get("active_viewers", 0),
            "candidate_event": webrtc.get("candidate_event"),
            "ice_server_source": webrtc.get("ice_server_source"),
            "ice_server_count": webrtc.get("ice_server_count", 0),
            "stun_server_count": webrtc.get("stun_server_count", 0),
            "turn_server_count": webrtc.get("turn_server_count", 0),
            "turn_available": webrtc.get("turn_available", False),
            "local_candidate_types": webrtc.get("local_candidate_types", []),
            "local_candidate_protocols": webrtc.get(
                "local_candidate_protocols", []
            ),
            "remote_candidate_types": webrtc.get("remote_candidate_types", []),
            "remote_candidate_protocols": webrtc.get(
                "remote_candidate_protocols", []
            ),
        },
        "control": {
            "session_active": bool(control and control.session is not None),
            "busy": bool(control and control.lock.locked()),
            "closed": bool(control and control.closed.is_set()),
            "command_count": getattr(control, "command_count", 0),
            "request_sent_count": getattr(control, "request_sent_count", 0),
            "request_send_attempt_count": getattr(control, "request_send_attempt_count", 0),
            "ring_pause_requested": getattr(control, "ring_pause_requested", 0),
            "ring_pause_success": getattr(control, "ring_pause_success", 0),
            "ring_pause_timeout": getattr(control, "ring_pause_timeout", 0),
            "ring_resume_requested": getattr(control, "ring_resume_requested", 0),
            "ring_resume_success": getattr(control, "ring_resume_success", 0),
            "command_started_after_ring_release": getattr(control, "command_started_after_ring_release", False),
            "cleanup_error_type": getattr(control, "cleanup_error_type", None),
            "response_count": getattr(control, "response_count", 0),
            "decode_failures": getattr(control, "decode_failures", 0),
            "last_output": getattr(control, "last_output", None),
            "last_result": getattr(control, "last_result", None),
            "last_reason": getattr(control, "last_reason", None),
            "last_error_type": getattr(control, "last_error_type", None),
            "last_error_message": getattr(control, "last_error_message", None),
            "last_error_stage": getattr(control, "last_error_stage", None),
            "top_level_tlv_counts": _counter_map(
                getattr(control, "tlv_counts", {})
            ),
            "transport_framing": getattr(control, "framing_diagnostics", {}),
        },
        "doorbell": {
            **(ring.coordination_diagnostics() if ring is not None else {}),
            "connected": hub.ring_connected,
            "ring_count": hub.ring_count,
            "ringing": hub.ringing,
            "last_error_type": getattr(ring, "last_error_type", None) or hub.ring_error,
            "last_error_message": getattr(ring, "last_error_message", None),
            "last_error_stage": getattr(ring, "last_error_stage", None),
            "connection_attempts": getattr(ring, "connection_attempts", 0),
            "listen_timeout_count": getattr(ring, "listen_timeout_count", 0),
            "zero_activity_timeout_count": getattr(ring, "zero_activity_timeout_count", 0),
            "paused_for_control": getattr(ring, "paused_for_control", False),
            "control_pause_count": getattr(ring, "control_pause_count", 0),
            "control_pause_timeout_count": getattr(ring, "control_pause_timeout_count", 0),
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
            "transport_framing": getattr(ring, "framing_diagnostics", {}),
        },
        "network": {
            "discovery": discovery_diagnostics(),
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
            "ice_candidate_addresses_included": False,
            "ice_server_urls_included": False,
            "ice_server_credentials_included": False,
            "transport_frame_bytes_included": False,
            "manufacturer_payloads_included": False,
        },
    }
