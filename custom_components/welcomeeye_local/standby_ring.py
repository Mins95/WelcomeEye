"""No-op doorbell listener used while Connect V1 ring support is on standby."""
import threading


class StandbyRingListener:
    """Expose the RingListener coordination surface without opening a socket."""

    standby = True

    def __init__(self):
        self.closed = threading.Event()
        self.session = None
        self.thread = None
        self.connection_attempts = 0
        self.last_error_type = None
        self.last_error_message = None
        self.last_error_stage = None
        self.last_top_level_tlv = None
        self.tlv_counts = {}
        self.inner_tlv_counts = {}
        self.alarm_type_counts = {}
        self.decode_failures = 0
        self.listen_timeout_count = 0
        self.zero_activity_timeout_count = 0
        self.control_pause_count = 0
        self.control_pause_timeout_count = 0
        self.control_pause_success_count = 0
        self.resume_count = 0
        self.resume_reconnected_count = 0
        self._paused = False

    @property
    def candidate_ring_types(self):
        return []

    @property
    def paused_for_control(self):
        return self._paused

    @property
    def released_for_control(self):
        return self._paused and not self.closed.is_set()

    def start(self):
        """Keep V1 doorbell disabled: never create a worker or network session."""
        return False

    def pause_for_control(self, timeout=5):
        """Acknowledge control immediately because no ring session exists."""
        if self.closed.is_set():
            return False
        self.control_pause_count += 1
        self.control_pause_success_count += 1
        self._paused = True
        return True

    def resume_after_control(self, *, control_released=True):
        """Clear the logical barrier without starting a ring listener."""
        self.resume_count += 1
        self._paused = False
        return False

    def close(self):
        self.closed.set()
        self._paused = False

    def coordination_diagnostics(self):
        return {
            "mode": "standby",
            "enabled": False,
            "reason": "connect_v1_temporarily_disabled",
            "intentional_pause_count": self.control_pause_count,
            "pause_success_count": self.control_pause_success_count,
            "resume_count": self.resume_count,
            "resume_reconnected_count": 0,
            "currently_paused": self._paused,
            "active_session": False,
            "release_failed": False,
            "worker_alive": False,
            "keepalive_count": 0,
        }
