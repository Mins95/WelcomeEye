"""Explicit, bounded observation of the sole live reader, not a ring detector."""
import asyncio
from copy import deepcopy
import hashlib
import hmac
import secrets


class DoorbellObservation:
    """No socket, subscription, media acquisition, private tail or inferred ring."""

    def __init__(self, hub):
        self.hub = hub
        self._timer = None
        self._session = None
        self._selected_hub = None
        self._channel = None
        self._started = self._deadline = 0.0
        self.runs = 0
        self._result = {'status': 'not_started'}
        self._comparison_key = None
        self._candidate_fingerprints = {}

    def _elapsed(self):
        return max(0, round((asyncio.get_running_loop().time() - self._started) * 1000))

    def _check_session(self):
        if self._timer is None:
            return
        if asyncio.get_running_loop().time() >= self._deadline:
            self.finish('deadline')
        elif (self._selected_hub.stopped or not self._selected_hub.connected
                or self._selected_hub.live.session is not self._session
                or self._session._close_task is not None):
            self.finish('media_closed')

    def execute(self, operation, duration=90, *, channel=1):
        if type(channel) is not int or channel not in (1, 2):
            raise ValueError('Observation channel must be 1 or 2')
        if self.hub.stopped:
            raise RuntimeError('Connect 3 is stopped')
        self._check_session()
        if self._timer is not None and operation in ('mark', 'stop') and channel != self._channel:
            raise ValueError('Observation channel differs from the active observation')
        if operation == 'start':
            if type(duration) is not int or not 30 <= duration <= 300:
                raise ValueError('Observation duration must be 30 to 300 seconds')
            if self._timer is not None:
                raise RuntimeError('Observation already active')
            selected = self.hub if channel == 1 else getattr(self.hub, 'channel2', None)
            if selected is None:
                raise RuntimeError('The selected Connect 3 camera is unavailable')
            session = selected.live.session
            if (selected.stopped or not selected.connected or session is None
                    or session._close_task is not None):
                raise RuntimeError('Open the selected Connect 3 live video before observing')
            loop = asyncio.get_running_loop()
            self._started = loop.time()
            self._deadline = self._started + duration
            self._session = session
            self._selected_hub = selected
            self._channel = channel
            self._comparison_key = secrets.token_bytes(32)
            self._candidate_fingerprints.clear()
            self.runs += 1
            self._result = dict(status='observing', channel=channel, duration_seconds=duration,
                elapsed_ms=0, control_messages=0, control_command_counts={},
                transparent_order_counts={}, other_doorbell_call_candidates=0,
                hangup_candidates=0, malformed_candidates=0, markers=[],
                compared_candidate_messages=0, matching_candidate_messages=0,
                events=[], dropped_events=0, end_reason=None)
            self._timer = loop.call_later(duration, self.finish, 'deadline')
        elif operation == 'mark':
            if self._timer is None:
                raise RuntimeError('No active observation; marker not recorded')
            if len(self._result['markers']) >= 5:
                raise RuntimeError('Observation marker limit reached')
            self._result['markers'].append(self._elapsed())
        elif operation == 'stop':
            self.finish('user_stop')
        elif operation != 'status':
            raise ValueError('Unsupported observation operation')
        return self.diagnostics()

    def observe(self, session, packet):
        if self._timer is None or session is not self._session:
            return
        self._check_session()
        if self._timer is None:
            return
        result = self._result
        command = packet.header.command
        result['control_messages'] += 1
        key = str(command)
        counts = result['control_command_counts']
        counts[key] = counts.get(key, 0) + 1
        event = dict(sequence=result['control_messages'], elapsed_ms=self._elapsed(),
                     channel=self._channel, command=command,
                     header_bytes=len(packet.header.plaintext),
                     parameter_bytes=len(packet.parameters), candidate_type='unclassified_control')
        if command == 0xFE and len(packet.header.plaintext) == 32:
            # QvPlayerCore.u (classes2 0x28b2e0), switch order23:
            # onOtherDoorBellCall(byte0, UTF8 rest). Only the raw first byte
            # is exposed; it is not a proven channel and the tail is private.
            # Order1 calls ReceiveHangUpData. These are candidates only.
            order = packet.header.plaintext[13]
            key = str(order)
            counts = result['transparent_order_counts']
            counts[key] = counts.get(key, 0) + 1
            event['order'] = order
            if order in (1, 23):
                valid = len(packet.parameters) >= 1
                event['candidate_type'] = 'other_doorbell_call' if order == 23 else 'hangup'
                event['candidate_structure_valid'] = valid
                event['candidate_tail_bytes'] = max(0, len(packet.parameters) - 1)
                if valid:
                    if order == 23:
                        event['candidate_selector'] = packet.parameters[0]
                        event['candidate_selector_interpretation'] = 'unknown'
                    result['other_doorbell_call_candidates' if order == 23
                           else 'hangup_candidates'] += 1
                    if len(result['events']) < 128:
                        self._compare_candidate(order, packet.parameters, event)
                else:
                    result['malformed_candidates'] += 1
        elif command == 0xFE:
            event['candidate_structure_valid'] = False
            result['malformed_candidates'] += 1
        if len(result['events']) < 128:
            result['events'].append(event)
        else:
            result['dropped_events'] += 1

    def _compare_candidate(self, order, parameters, event):
        """Record content equality, never infer a duplicate physical action.

        The UTF-8 tail may identify a caller. A fresh random HMAC key makes
        comparisons local to this observation; neither it nor digests leave
        memory, and all are discarded when observation stops. Compare only
        retained, structurally valid candidates to keep state bounded at 128.
        """
        fingerprint = hmac.new(self._comparison_key, bytes((order,)), hashlib.sha256)
        fingerprint.update(parameters)
        digest = fingerprint.digest()
        self._result['compared_candidate_messages'] += 1
        previous = self._candidate_fingerprints.get(digest)
        if previous is None:
            self._candidate_fingerprints[digest] = event['sequence']
        else:
            event['same_candidate_as_sequence'] = previous
            self._result['matching_candidate_messages'] += 1

    def media_closed(self, session):
        if session is self._session:
            self.finish('media_closed')

    def finish(self, reason='integration_unload'):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
            self._result.update(status='finished', elapsed_ms=self._elapsed(), end_reason=reason)
        self._session = None
        self._selected_hub = None
        self._comparison_key = None
        self._candidate_fingerprints.clear()

    def diagnostics(self):
        self._check_session()
        result = deepcopy(self._result)
        if self._timer is not None:
            result['elapsed_ms'] = self._elapsed()
        # Relative proximity is useful to compare a voluntary marker with a
        # candidate. It never proves that a physical press caused a packet.
        for event in result.get('events', ()):
            markers = result.get('markers', ())
            if markers:
                index = min(range(len(markers)), key=lambda i: abs(event['elapsed_ms'] - markers[i]))
                event['nearest_marker'] = index + 1
                event['marker_delta_ms'] = event['elapsed_ms'] - markers[index]
        return dict(local_detection_implemented=False, ring_events_emitted=0,
            observation_scope='existing_live_session_only', background_listener_active=False,
            physical_ring_confirmed=False, marker_correlation_only=True,
            candidate_comparison_scope='same_order_and_parameters_within_observation',
            candidate_content_equality_is_not_ring_deduplication=True,
            runs=self.runs, observation=result)
