"""Pure, bounded QV call parsing and private replay state.

Only the regular QV data branch is supported. Door Connect's APK identifies
AlarmEvent 19 as CALL, and converts DevChNo from zero based to one based.
The HS/CameraId and LT/message_content branches have different event maps.
AlarmTime has no trustworthy timezone contract here and never proves freshness.
"""
from __future__ import annotations

from collections import deque
from dataclasses import dataclass, field
import hashlib
import json
import math
import time

MAX_PAYLOAD_BYTES = 65536
MAX_FIELDS = 64
MAX_FIELD_LENGTH = 16384
MAX_IDENTIFIER_LENGTH = 256
MAX_CHANNEL = 256
MAX_RING_AGE_SECONDS = 120
MAX_FUTURE_SECONDS = 5
SEEN_LIMIT = 256
STATE_VERSION = 1
_FIELDS = frozenset({'AlarmEvent', 'DevUmid', 'AlarmId', 'DevChNo',
                     'AlarmTime', 'AlarmState'})
REQUIRED_FIELDS = frozenset({'AlarmEvent', 'DevUmid', 'AlarmId', 'DevChNo'})
PAYLOAD_REJECTION_SUBREASONS = frozenset({
    'invalid_map', 'invalid_field_count', 'invalid_key', 'invalid_value',
    'invalid_utf8', 'payload_too_large', 'generic_notification',
    'unsupported_branch', 'missing_required_fields',
})
PAYLOAD_BRANCHES = frozenset({'unclassified', 'notification', 'HS', 'share',
                             'LT', 'house_QV', 'regular_QV'})


@dataclass(frozen=True)
class QVPayloadInspection:
    """Bounded data plus fixed structural categories, never diagnostic values."""

    data: dict | None = field(default=None, repr=False)
    subreason: str | None = None
    branch: str = 'unclassified'
    missing_fields: tuple[str, ...] = ()


@dataclass(frozen=True)
class QVRingEvent:
    """An internal candidate; the caller still verifies its configured target."""

    channel: int
    event_id: str = field(repr=False)
    sent_ms: int = field(default=0, repr=False)


def _identifier(value):
    return (isinstance(value, str) and 0 < len(value) <= MAX_IDENTIFIER_LENGTH
            and value == value.strip()
            and not any(ord(char) < 32 or ord(char) == 127 for char in value))


def _number(value, maximum):
    # Android FCM data are strings. No bool/float coercion, sign or whitespace.
    if (not isinstance(value, str) or not 1 <= len(value) <= 10
            or not value.isascii() or not value.isdecimal()):
        return None
    result = int(value)
    return result if result <= maximum else None


def inspect_qv_notification_data(data, *, has_notification=False):
    """Validate before SDK data extraction; report only fixed structural facts."""
    if not isinstance(data, dict):
        return QVPayloadInspection(subreason='invalid_map')
    if not 1 <= len(data) <= MAX_FIELDS:
        return QVPayloadInspection(subreason='invalid_field_count')
    size = 0
    for key, value in data.items():
        if not isinstance(key, str) or not 1 <= len(key) <= 128:
            return QVPayloadInspection(subreason='invalid_key')
        if not isinstance(value, str) or len(value) > MAX_FIELD_LENGTH:
            return QVPayloadInspection(subreason='invalid_value')
        try:
            size += len(key.encode('utf-8')) + len(value.encode('utf-8'))
        except UnicodeError:
            return QVPayloadInspection(subreason='invalid_utf8')
        if size > MAX_PAYLOAD_BYTES:
            return QVPayloadInspection(subreason='payload_too_large')
    # Firebase NotificationParams.isNotification requires the exact enable flag.
    # A real Web Push notification envelope remains generic regardless of data.
    if (has_notification or data.get('gcm.n.e') == '1'
            or data.get('gcm.notification.e') == '1'):
        return QVPayloadInspection(subreason='generic_notification', branch='notification')
    # RemoteMessage.getData excludes these SDK keys, not a developer string
    # called "notification". The transport captures google.sent_time separately
    # before this extraction; never promote a developer timestamp to metadata.
    data = {key: value for key, value in data.items()
            if not key.startswith(('google.', 'gcm.'))
            and key not in ('from', 'message_type', 'collapse_key')}
    # Preserve the APK's branch priority; their event maps/identities differ.
    branch = ('HS' if data.get('CameraId') else 'share' if data.get('SrcAccountId')
              else 'LT' if 'message_content' in data else 'house_QV'
              if data.get('userHouseId') else 'regular_QV')
    if branch != 'regular_QV':
        return QVPayloadInspection(subreason='unsupported_branch', branch=branch)
    missing = tuple(sorted(REQUIRED_FIELDS - data.keys()))
    if missing:
        return QVPayloadInspection(subreason='missing_required_fields', branch=branch,
                                   missing_fields=missing)
    return QVPayloadInspection(data={key: value for key, value in data.items() if key in _FIELDS},
                               branch=branch)


def qv_notification_data(data, *, has_notification=False):
    """Return only bounded QV fields, rejecting other APK notification branches."""
    return inspect_qv_notification_data(data, has_notification=has_notification).data


def parse_qv_ring(data, device_uid):
    """Recognize only CALL 19 for the exact UMID; never use names or prefixes."""
    parsed = qv_notification_data(data)
    if parsed is None or not _identifier(device_uid):
        return None
    if parsed['DevUmid'] != device_uid or not _identifier(parsed['AlarmId']):
        return None
    if _number(parsed['AlarmEvent'], 100003) != 19:
        return None
    channel = _number(parsed['DevChNo'], MAX_CHANNEL - 1)
    if channel is None:
        return None
    return QVRingEvent(channel + 1, parsed['AlarmId'])


def _digest(*values):
    return hashlib.sha256(json.dumps(values, ensure_ascii=True,
        separators=(',', ':')).encode('ascii')).hexdigest()


class QVEventFilter:
    """Filter fresh calls and retain bounded hashes for durable deduplication.

    ``sent_ms`` must come from FCMReceiver's authenticated transport metadata,
    never from a vendor payload or reception time. A new controller lifecycle
    rejects every message sent before its startup boundary, including queued
    offline calls. Preserve this object across transport reconnects. Persist
    ``export_state()`` before emitting a returned candidate as an HA event.
    """

    def __init__(self, device_uid, *, now=time.time, startup_time=None, state=None):
        if not _identifier(device_uid):
            raise ValueError('invalid_device_identity')
        self._uid = device_uid
        self._device_hash = _digest('device', device_uid)
        self._now = now
        started = now() if startup_time is None else startup_time
        if (type(started) not in (int, float) or not math.isfinite(started)
                or started <= 0):
            raise ValueError('invalid_startup_time')
        self._started_ms = int(started * 1000)
        self._seen = deque(maxlen=SEEN_LIMIT)
        self._diag = {'messages': 0, 'accepted': 0, 'rejected_payload': 0,
                      'unavailable_timestamp': 0, 'invalid_timestamp': 0,
                      'expired_timestamp': 0, 'future_timestamp': 0,
                      'before_startup': 0, 'duplicate': 0,
                      'last_disposition': 'unobserved'}
        if state is not None:
            self._restore(state)

    def _restore(self, state):
        if (not isinstance(state, dict) or state.get('version') != STATE_VERSION
                or type(state.get('version')) is not int
                or state.get('device') != self._device_hash
                or not isinstance(state.get('seen'), list)
                or len(state['seen']) > SEEN_LIMIT):
            raise ValueError('invalid_event_state')
        for item in state['seen']:
            if (not isinstance(item, str) or len(item) != 64
                    or any(char not in '0123456789abcdef' for char in item)):
                raise ValueError('invalid_event_state')
            if item not in self._seen:
                self._seen.append(item)

    def export_state(self):
        """Private JSON-safe state, bound to exactly one UMID, with no raw IDs."""
        return {'version': STATE_VERSION, 'device': self._device_hash,
                'seen': list(self._seen)}

    def diagnostics(self):
        """Only local categories and counters; no payload, ID or time escapes."""
        return dict(self._diag)

    def _reject(self, reason):
        self._diag[reason] += 1
        self._diag['last_disposition'] = reason
        return None

    def accept(self, data, persistent_id, sent_ms):
        """Match the receiver's callback arguments, returning a candidate or None."""
        self._diag['messages'] += 1
        event = parse_qv_ring(data, self._uid)
        if event is None:
            return self._reject('rejected_payload')
        if sent_ms is None or (type(sent_ms) is int and sent_ms == 0):
            return self._reject('unavailable_timestamp')
        if type(sent_ms) is not int or not 0 < sent_ms <= (1 << 63) - 1:
            return self._reject('invalid_timestamp')
        current = self._now()
        if type(current) not in (int, float) or not math.isfinite(current):
            return self._reject('invalid_timestamp')
        age = current - sent_ms / 1000
        if age < -MAX_FUTURE_SECONDS:
            return self._reject('future_timestamp')
        if age > MAX_RING_AGE_SECONDS:
            return self._reject('expired_timestamp')
        if sent_ms < self._started_ms:
            return self._reject('before_startup')
        keys = [_digest('alarm', self._uid, event.channel, event.event_id)]
        if persistent_id:
            if not _identifier(persistent_id):
                return self._reject('rejected_payload')
            keys.append(_digest('transport', self._uid, persistent_id))
        elif persistent_id not in ('', None):
            return self._reject('rejected_payload')
        if any(key in self._seen for key in keys):
            return self._reject('duplicate')
        self._seen.extend(keys)
        self._diag['accepted'] += 1
        self._diag['last_disposition'] = 'accepted'
        return QVRingEvent(event.channel, event.event_id, sent_ms)
