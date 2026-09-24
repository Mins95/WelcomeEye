"""Privacy-safe downloadable diagnostics for WelcomeEye support."""
from dataclasses import asdict
import sys

from .client import discovery_diagnostics


from .const import VERSION
from .crc32c_diagnostics import get_crc32c_diagnostics


def _is_active(value):
    return value is not None


def _counter_map(values):
    return {str(key): values[key] for key in sorted(values)}


def _media_worker_stack(thread):
    """Locate a blocked media call without source lines, locals or file paths."""
    if thread is None or thread.ident is None or not thread.is_alive():
        return []
    frame = sys._current_frames().get(thread.ident)
    result = []
    while frame is not None and len(result) < 12:
        filename = frame.f_code.co_filename.replace('\\', '/').rsplit('/', 1)[-1]
        result.append({
            'module': filename if filename in ('hub.py', 'media.py', 'client.py', 'threading.py') else 'external',
            'line': frame.f_lineno,
        })
        frame = frame.f_back
    return result


async def async_get_config_entry_diagnostics(hass, entry):
    """Return useful runtime state without exposing device secrets or payloads."""
    hub = entry.runtime_data
    thread = getattr(hub, "thread", None)
    control = getattr(hub, "control", None)
    ring = getattr(hub, "ring_listener", None)
    webrtc = dict(getattr(hub, "webrtc_diagnostics", {}))
    announced = getattr(hub, "last_announced_format", None)
    session = hub.session
    crc32c = await hass.async_add_executor_job(get_crc32c_diagnostics)

    return {
        "integration": {
            "version": VERSION,
            "domain": entry.domain,
            "frontend_transport": "home_assistant_stream",
            "native_webrtc_advertised": False,
            "crc32c": crc32c,
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
            "media_stop_requested": hub.stop_event.is_set(),
            "media_worker_stage": getattr(hub, 'worker_stage', None),
            "media_session_active": _is_active(hub.session),
            "proxy_clients": len(hub.handlers),
            "stream_queues": len(hub.queues),
            "buffer_chunks": len(hub.buffer),
            "buffer_bytes": hub.buffer_size,
            "has_snapshot": hub.image is not None,
            "image_generation": getattr(hub, "image_generation", 0),
            "snapshot_requests": getattr(hub, "snapshot_requests", 0),
            "snapshot_successes": getattr(hub, "snapshot_successes", 0),
            "snapshot_timeouts": getattr(hub, "snapshot_timeouts", 0),
            "snapshot_errors": getattr(hub, "snapshot_errors", 0),
            "snapshot_failures": getattr(hub, "snapshot_errors", 0) + getattr(hub, "snapshot_timeouts", 0),
            "snapshot_started_media": getattr(hub, "snapshot_started_media", 0),
            "snapshot_reused_media": getattr(hub, "snapshot_reused_media", 0),
            "snapshot_wait_elapsed_ms": getattr(hub, "snapshot_wait_elapsed_ms", 0),
            "snapshot_last_error_type": getattr(hub, "snapshot_last_error_type", None),
            "last_error_type": type(hub.error).__name__ if hub.error else None,
            "last_error_message": None,
        },
        "manual_snapshot": dict(hub.manual_snapshot.diagnostics),
        "media": {
            "microphone": dict(hub.talkback.diagnostics),
            "acquisition": dict(getattr(hub, 'acquire_diagnostics', {})),
            "shutdown": dict(getattr(hub, 'shutdown_diagnostics', {})),
            "connection": session.connection_diagnostics() if session else
                dict(getattr(hub, 'lifecycle', {}).get('connection', {})),
            "lifecycle": dict(getattr(hub, 'lifecycle', {})),
            "worker_stack": _media_worker_stack(thread),
            "previous_lifecycles": list(getattr(hub, 'previous_lifecycles', [])),
            "last_media_lifecycle": getattr(hub, 'last_media_lifecycle', None),
            "codec": dict(getattr(hub, 'codec_diagnostics', {})),
            "codec_totals": dict(getattr(hub, 'codec_totals', {})),
            "stream_format": asdict(hub.format) if hub.format else None,
            "format_available": hub.format is not None,
            "last_announced_stream_format": asdict(announced) if announced else None,
            "selected_media_profile": getattr(hub, "selected_media_profile", None),
            "profile_attempts": getattr(hub, "profile_attempts", 0),
            "video_packets_received": getattr(hub, "video_packets_received", 0),
            "selected_video_tlv": getattr(hub, "selected_video_tlv", None),
            "h264_summary": {
                "framing_counts": dict(
                    sorted(getattr(hub, "h264_framing_counts", {}).items())
                ),
                "nal_types_seen": sorted(getattr(hub, "h264_nal_types", set())),
                "idr_total": sum(getattr(hub, "h264_idr_counts", {}).values()),
            },
            "v1_av": {
                "profile": "channel16_stream1_mode2",
                "stream_mode_query_enabled": False,
                "stream_mode_query_sent": getattr(hub, "lt_query_stream_mode_sent", 0),
                "profile_used": getattr(hub, "v1_apk_profile_used", False),
                "start_request_sent": getattr(hub, "lt_start_av_request_sent", 0),
                "start_response_count": getattr(hub, "lt_start_av_response_count", 0),
                "start_result": getattr(hub, "lt_start_av_result", None),
                "start_errors": getattr(hub, "lt_start_av_request_errors", 0),
                "stop_request_sent": getattr(hub, "lt_stop_av_request_sent", 0),
                "stop_response_count": getattr(hub, "lt_stop_av_response_count", 0),
                "stop_result": getattr(hub, "lt_stop_av_result", None),
                "stop_errors": getattr(hub, "lt_stop_av_request_errors", 0),
            },
            "transport_framing": session.framing_diagnostics() if session else
                getattr(hub, "media_framing_diagnostics", {}),
        },
        "webrtc": {
            "status": {
                "DEVICE_MEDIA_OK": bool(hub.connected),
                "H264_DECODE_OK": bool(getattr(hub, 'codec_diagnostics', {}).get('decoded_video_pts', 0)),
                "WEBRTC_NEGOTIATION_OK": bool(webrtc.get('negotiation_ok', False)),
                "ICE_CONNECTED": webrtc.get('ice_connection_state') in ('connected', 'completed'),
                "ICE_FAILED": webrtc.get('ice_connection_state') == 'failed',
                "ICE_CHECKING": webrtc.get('ice_connection_state') == 'checking',
                "STUN_AVAILABLE": webrtc.get('stun_server_count', 0) > 0,
                "TURN_AVAILABLE": webrtc.get('turn_available', False),
                # Candidate presence is not proof of the selected pair. aiortc
                # exposes no public selected-pair stats: keep this unknown.
                "TURN_USED": None,
            },
            "native_webrtc_advertised": False,
            "viewer_states": webrtc.get('viewer_states', []),
            "cleanup_error_type": webrtc.get('cleanup_error_type'),
            "stun_transactions_cancelled": webrtc.get('stun_transactions_cancelled', 0),
            "cleanup_stage": webrtc.get('cleanup_stage'),
            "cleanup_failed_stage": webrtc.get('cleanup_failed_stage'),
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
            "local_candidate_protocols": webrtc.get("local_candidate_protocols", []),
            "remote_candidate_types": webrtc.get("remote_candidate_types", []),
            "remote_candidate_protocols": webrtc.get("remote_candidate_protocols", []),
        },
        "control": {
            "physical_result_uncertain": getattr(control, 'physical_result_uncertain', False),
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
            "v1_settle_wait_count": getattr(control, "v1_settle_wait_count", 0),
            "v1_settle_requested_ms": getattr(control, "v1_settle_requested_ms", 0),
            "v1_settle_elapsed_ms": getattr(control, "v1_settle_elapsed_ms", 0),
            "cleanup_error_type": getattr(control, "cleanup_error_type", None),
            "response_count": getattr(control, "response_count", 0),
            "decode_failures": getattr(control, "decode_failures", 0),
            "last_output": getattr(control, "last_output", None),
            "native_control_path": getattr(control, "native_control_path", None),
            "physical_activation_verified": False,
            "last_result": getattr(control, "last_result", None),
            "last_reason": getattr(control, "last_reason", None),
            "last_error_type": getattr(control, "last_error_type", None),
            "last_error_message": None,
            "last_error_stage": getattr(control, "last_error_stage", None),
            "top_level_tlv_counts": _counter_map(getattr(control, "tlv_counts", {})),
            "transport_framing": getattr(control, "framing_diagnostics", {}),
        },
        "doorbell": {
            "local_supported": hub.local_ring_supported,
            **(ring.coordination_diagnostics() if ring is not None else {}),
            "connected": hub.ring_connected,
            "ring_count": hub.ring_count,
            "ringing": hub.ringing,
            "last_error_type": getattr(ring, "last_error_type", None) or hub.ring_error,
            "last_error_message": None,
            "last_error_stage": getattr(ring, "last_error_stage", None),
            "connection_attempts": getattr(ring, "connection_attempts", 0),
            "subscription_request_count": 0,
            "subscription_response_count": 0,
            "subscription_result": None,
            "listen_timeout_count": getattr(ring, "listen_timeout_count", 0),
            "zero_activity_timeout_count": getattr(ring, "zero_activity_timeout_count", 0),
            "paused_for_control": getattr(ring, "paused_for_control", False),
            "control_pause_count": getattr(ring, "control_pause_count", 0),
            "control_pause_timeout_count": getattr(ring, "control_pause_timeout_count", 0),
            "last_top_level_tlv": getattr(ring, "last_top_level_tlv", None),
            "top_level_tlv_counts": _counter_map(getattr(ring, "tlv_counts", {})),
            "inner_tlv_counts": _counter_map(getattr(ring, "inner_tlv_counts", {})),
            "alarm_type_counts": _counter_map(getattr(ring, "alarm_type_counts", {})),
            "candidate_ring_types": getattr(ring, "candidate_ring_types", []),
            "decode_failures": getattr(ring, "decode_failures", 0),
            "transport_framing": getattr(ring, "framing_diagnostics", {}),
        },
        "ring_image": dict(hub.ring_image.diagnostics),
        "network": {"discovery": discovery_diagnostics()},
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
