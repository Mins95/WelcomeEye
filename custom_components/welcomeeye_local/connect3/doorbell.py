"""Explicit, bounded observation of the sole live reader, not a ring detector."""
import asyncio
from copy import deepcopy


class DoorbellObservation:
    """No socket, subscription, media acquisition, raw data or inferred HA ring."""

    def __init__(self, hub):
        self.hub = hub
        self._timer = None
        self._session = None
        self._started = self._deadline = 0.0
        self.runs = 0
        self._result = {'status': 'not_started'}

    def _elapsed(self):
        return max(0, round((asyncio.get_running_loop().time() - self._started) * 1000))

    def execute(self, operation, duration=90):
        if self.hub.stopped:
            raise RuntimeError('Connect 3 is stopped')
        if self._timer is not None and asyncio.get_running_loop().time() >= self._deadline:
            self.finish('deadline')
        if operation == 'start':
            if type(duration) is not int or not 30 <= duration <= 120:
                raise ValueError('Observation duration must be 30 to 120 seconds')
            if self._timer is not None:
                raise RuntimeError('Observation already active')
            session = self.hub.live.session
            if (not self.hub.connected or session is None
                    or session._close_task is not None):
                raise RuntimeError('Open Connect 3 live video before observing')
            loop = asyncio.get_running_loop()
            self._started = loop.time()
            self._deadline = self._started + duration
            self._session = session
            self.runs += 1
            self._result = dict(status='observing', duration_seconds=duration,
                elapsed_ms=0, control_messages=0, control_command_counts={},
                transparent_order_counts={}, other_doorbell_call_candidates=0,
                hangup_candidates=0, malformed_candidates=0, markers=[],
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
        if asyncio.get_running_loop().time() >= self._deadline:
            self.finish('deadline')
            return
        result = self._result
        command = packet.header.command
        result['control_messages'] += 1
        key = str(command)
        counts = result['control_command_counts']
        counts[key] = counts.get(key, 0) + 1
        event = dict(elapsed_ms=self._elapsed(), command=command,
                     parameter_bytes=len(packet.parameters))
        if command == 0xFE:
            # QvPlayerCore.u (classes2 0x28b2e0), switch order23:
            # onOtherDoorBellCall(byte0, UTF8 rest). Neither value is retained.
            # Order1 calls ReceiveHangUpData. These are candidates only.
            order = packet.header.plaintext[13]
            key = str(order)
            counts = result['transparent_order_counts']
            counts[key] = counts.get(key, 0) + 1
            event['order'] = order
            if order in (1, 23):
                valid = len(packet.parameters) >= 1
                event['candidate_structure_valid'] = valid
                if valid:
                    result['other_doorbell_call_candidates' if order == 23
                           else 'hangup_candidates'] += 1
                else:
                    result['malformed_candidates'] += 1
        if len(result['events']) < 128:
            result['events'].append(event)
        else:
            result['dropped_events'] += 1

    def media_closed(self, session):
        if session is self._session:
            self.finish('media_closed')

    def finish(self, reason='integration_unload'):
        if self._timer is not None:
            self._timer.cancel()
            self._timer = None
            self._result.update(status='finished', elapsed_ms=self._elapsed(), end_reason=reason)
        self._session = None

    def diagnostics(self):
        result = deepcopy(self._result)
        if self._timer is not None:
            result['elapsed_ms'] = self._elapsed()
        return dict(local_detection_implemented=False, ring_events_emitted=0,
            observation_scope='existing_live_session_only', background_listener_active=False,
            runs=self.runs, observation=result)
