"""Small, payload-free snapshots of one media session's lifetime."""
from .protected import ProtocolError


def new_lifecycle():
    return {
        'stage': 'connecting', 'worker_exit_reason': None,
        'worker_exception_type': None, 'worker_exception_stage': None,
        'stop_event_set_at_exit': False, 'socket_closed_by_peer': False,
        'pending_output_state_at_exit': None, 'pending_output_sent': False,
        'pending_output_confirmation_seen': False, 'pending_output_age_ms': None,
        'stop_av_attempted': False, 'stop_av_sent': False,
        'stop_av_response_received': False, 'stop_av_result': None,
        'stop_av_wait_duration_ms': 0, 'session_stop_attempted': False,
        'session_stop_sent': False, 'tcp_close_reason': None,
        'tcp_closed': False, 'cleanup_errors': [],
        # Sending/closing locally cannot prove the physical busy state.
        'v1_device_release_complete': None,
        'video_packets_received': 0,
        'decoded_video_frames': 0, 'decoded_audio_frames': 0,
        'last_video_packet_elapsed_ms': None, 'session_duration_ms': None,
        'live_idle_poll_count': 0,
    }


def exit_reason(exc, stage, stop_requested, release_reason, session):
    if stop_requested:
        return release_reason or 'stop_event'
    if isinstance(exc, ConnectionResetError):
        return 'connection_reset'
    if getattr(session, 'remote_eof', False):
        return 'remote_tcp_eof'
    if isinstance(exc, TimeoutError):
        if stage in ('discovering', 'rediscovering_after_refused'):
            return 'discovery_timeout'
        if stage == 'tcp_connecting':
            return 'tcp_connect_timeout'
        if stage in ('sending_login', 'waiting_login_response', 'login_response_timeout'):
            return 'login_timeout'
        return 'socket_timeout'
    if isinstance(exc, ProtocolError):
        if str(exc) == 'V1 OWSP receive deadline exceeded':
            return 'partial_socket_timeout'
        return 'owsp_error' if stage == 'read' else 'protocol_error'
    if isinstance(exc, OSError):
        return 'socket_error'
    if stage == 'decode_video':
        return 'h264_error'
    if stage in ('decode_audio', 'pipeline'):
        return 'codec_error'
    return 'unexpected_exception' if exc else 'stop_event'
