"""Opt-in bounded metadata observation; never reads or writes a device socket."""
from collections import deque
import threading
import time

# Enable only for an attended diagnostic session, then restore the baseline.
PASSIVE_TRACE_ENABLED = False


class RingTrace:
    """One listener's ordered metadata, relative to its latest accepted ring.

    Sequence zero is the no-ring control period, relative to observer creation.
    Limits bound observation only; they never trigger or delay media acquisition.
    The optional sink must enqueue work, not perform I/O on the listener thread.
    """

    _EVENTS = {
        'session_open', 'authenticated', 'session_close', 'listener_state',
        'ring', 'duplicate_ring', 'tlv', 'inner_tlv', 'alarm', 'decode_error',
        'owsp_zero', 'owsp_header', 'owsp_complete', 'framing_error',
        'keepalive_sent', 'read_timeout', 'error',
    }
    _FIELDS = {'kind', 'length', 'count', 'connected', 'alarm_type'}

    def __init__(self, *, enabled=False, sink=None, clock=time.monotonic,
                 max_events=4096, duration=900):
        self.enabled = enabled
        self._clock, self._sink = clock, sink
        self._started = self._origin = clock()
        self._duration, self._limit = duration, max_events
        self._rows = deque(maxlen=max_events)
        self._lock = threading.Lock()
        self._sequence = self._session = self._order = 0
        self._sink_errors = 0

    def record(self, event, **fields):
        if not self.enabled or event not in self._EVENTS:
            return
        with self._lock:
            now = self._clock()
            if now - self._started >= self._duration or self._order >= self._limit:
                self.enabled = False
                return
            if event == 'ring':
                self._sequence += 1
                self._origin = now
            elif event == 'session_open':
                self._session += 1
            self._order += 1
            row = {'order': self._order, 'ring_sequence': self._sequence,
                   'session': self._session, 't_ms': round((now - self._origin) * 1000),
                   'observer_ms': round((now - self._started) * 1000),
                   'event': event}
            row.update({key: value for key, value in fields.items()
                        if key in self._FIELDS and type(value) in (int, bool)})
            self._rows.append(row)
            if self._sink is not None:
                try:
                    self._sink(dict(row))
                except Exception:
                    # Observation must never prevent ring delivery or cleanup.
                    self._sink_errors += 1

    def snapshot(self):
        with self._lock:
            return {'enabled': self.enabled, 'sink_errors': self._sink_errors,
                    'events': [dict(row) for row in self._rows]}
