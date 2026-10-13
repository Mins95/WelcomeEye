"""Safe preflight for the unestablished native Connect 3 alarm connection.

The SDK's control-device login and alarm subscription are distinct from CGI
stream-key retrieval and media setup. Until their firmware-specific endpoint,
credential protection and subscription contract are established, this diagnostic
must refuse before any connection. It has no timer, socket, media owner or task.
"""


NATIVE_LISTENER_BLOCKERS = (
    'native_control_endpoint_unestablished',
    'native_login_credential_binding_unestablished',
    'native_login_protection_unestablished',
    'firmware_alarm_subscription_unestablished',
    'ring_event_channel_mapping_unestablished',
)


class AutonomousDoorbellDiagnostic:
    """Report why autonomous listening is unavailable, without pretending to listen."""

    def __init__(self):
        self._status = 'not_started'
        self._operation = 'status'
        self._reason = None
        self._close_reason = None
        self._unloaded = False
        self.requested_duration = None
        self.start_attempts = 0

    def execute(self, operation, duration=90):
        """Return an immediate, sanitized preflight result for start/status/stop."""
        if type(operation) is not str or operation not in ('start', 'status', 'stop'):
            raise ValueError('Unsupported autonomous doorbell operation')
        if operation == 'start':
            if type(duration) is not int or not 1 <= duration <= 90:
                raise ValueError('Autonomous observation duration must be 1 to 90 seconds')
            self.start_attempts = min(self.start_attempts + 1, 2**31 - 1)
            self.requested_duration = duration
            self._status = 'unavailable'
            self._reason = ('integration_unloaded' if self._unloaded
                            else 'native_login_protocol_unestablished')
            self._close_reason = 'preflight_refused'
        elif operation == 'stop':
            self._close_reason = 'user_stop'
        self._operation = operation
        return self.diagnostics()

    def finish(self, reason='integration_unload'):
        """Unload is synchronous because no observation or connection was established."""
        if reason not in ('integration_unload', 'home_assistant_stop'):
            raise ValueError('Unsupported autonomous doorbell shutdown reason')
        self._unloaded = True
        self._close_reason = reason

    def diagnostics(self):
        """Return fresh public containers; never include addresses or credentials."""
        return {
            'operation': 'autonomous_doorbell',
            'requested_operation': self._operation,
            'status': self._status,
            'last_stage': 'protocol_preflight' if self.start_attempts else 'not_started',
            'last_error_type': None,
            'last_error_reason': self._reason,
            'blockers': list(NATIVE_LISTENER_BLOCKERS),
            'requested_duration_seconds': self.requested_duration,
            'maximum_duration_seconds': 90,
            'effective_duration_seconds': 0,
            'start_attempts': self.start_attempts,
            'listening': False,
            'listen_established': False,
            'connection_attempts': 0,
            'tcp_connected': False,
            'transport': None,
            'port': None,
            'authentication_status': 'not_attempted',
            'credentials_sent': False,
            'subscription_request_count': 0,
            'messages_received': 0,
            'notification_candidates': 0,
            'hangup_candidates': 0,
            'physical_ring_confirmed': False,
            'ring_events_emitted': 0,
            'events': [],
            'elapsed_ms': 0,
            'video_session_created': False,
            'microphone_session_created': False,
            'connection_closed': True,
            'cleanup_status': 'no_connection_opened',
            'close_reason': self._close_reason,
            'integration_unloaded': self._unloaded,
        }
