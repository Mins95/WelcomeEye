"""Loaded investigation entry with no legacy sessions or media components."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import logging

from ..capabilities import DeviceVariant, MATRIX, ProtocolFamily
from .protocol import ALLOWED_TYPES
from .transport import new_counters, probe_one

_LOGGER = logging.getLogger(__name__)


class R002InvestigationHub:
    variant = DeviceVariant.R002
    protocol_family = ProtocolFamily.R002
    capabilities = MATRIX[DeviceVariant.R002]
    device_model = 'WelcomeEye Connect 2 R002 (experimental)'

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.stopped = True
        self.listeners = set()
        self._task = None
        self.status = 'detected'
        self.last_probe_at = self.last_probe_status = self.last_error_type = None
        self._transport = new_counters()
        self._per_type = {}
        self.probe_runs = self.probe_request_count = 0

    async def start(self):
        self.stopped = False  # Deliberately no network or background tasks.

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def _notify(self):
        if not self.stopped:
            for listener in tuple(self.listeners):
                listener()

    @property
    def detection_confidence(self):
        return 'probable' if self.entry.data.get('fingerprint', {}).get('detected') is True else 'unknown'

    def diagnostics(self):
        """Rebuild fingerprint from typed allowlists, never dump entry.data."""
        saved = self.entry.data.get('fingerprint', {})
        fp = {key: saved.get(key) if type(saved.get(key)) is bool else None for key in (
            'legacy_udp1500_response', 'tcp_8765_reachable', 'tcp_443_reachable',
            'tcp_6987_reachable', 'tcp_34567_reachable', 'tls_443_handshake_ok')}
        for key in ('legacy_udp1500_probe_count', 'fingerprint_runs', 'fingerprint_elapsed_ms'):
            value = saved.get(key)
            fp[key] = value if type(value) is int and value >= 0 else None
        for key in ('tls_certificate_cn', 'tls_certificate_issuer_cn'):
            fp[key] = saved.get(key) if saved.get(key) in ('eziotest', 'other') else None
        error = saved.get('fingerprint_last_error_type')
        fp['fingerprint_last_error_type'] = error if error in (
            'TimeoutError', 'ConnectionRefusedError', 'ConnectionResetError', 'OSError',
            'SSLError', 'ValueError', 'ImportError', 'ModuleNotFoundError') else None
        return {
            **fp, 'detected': saved.get('detected') is True,
            'detection_confidence': self.detection_confidence,
            'detection_source': 'udp_timeout_tcp_tls',
            'identity_source': 'provisional_host_hash',
            'probe_runs': self.probe_runs, 'probe_request_count': self.probe_request_count,
            'per_type': deepcopy(self._per_type), 'transport': dict(self._transport),
            'last_stage': self.status, 'last_error_type': self.last_error_type,
        }

    async def probe(self, types=ALLOWED_TYPES):
        types = tuple(types)
        if not types or len(types) > 4 or len(set(types)) != len(types) or any(
            type(kind) is not int or kind not in ALLOWED_TYPES for kind in types
        ):
            raise ValueError('Only investigation types 14, 15, 26, 28 are permitted')
        if self.stopped:
            raise RuntimeError('Investigation entry is stopped')
        if self._task is not None and not self._task.done():
            raise RuntimeError('An investigation probe is already running')
        self._task = asyncio.create_task(self._probe(types), name='welcomeeye-r002-probe')
        try:
            return await self._task
        finally:
            if self._task.done():
                self._task = None

    async def _probe(self, types):
        self.probe_runs += 1
        self.status = 'probing'
        self.last_error_type = None
        self.last_probe_at = datetime.now(timezone.utc)
        self._notify()
        results = {}
        _LOGGER.debug('r002.probe.start count=%d', len(types))
        try:
            for kind in types:
                self.probe_request_count += 1
                previous = self._per_type.get(str(kind), {})
                self._per_type[str(kind)] = {**previous, 'attempts': previous.get('attempts', 0) + 1}
                result = await probe_one(self.entry.data['host'], kind, self._transport)
                results[str(kind)] = result
                self._per_type[str(kind)].update(result,
                    successes=previous.get('successes', 0) + int(result['status'] == 'ok'))
                if result['last_error_type'] is not None:
                    self.last_error_type = result['last_error_type']
                _LOGGER.debug('r002.probe.response type=%d status=%s length=%s elapsed_ms=%d',
                    kind, result['status'], result.get('received_length'), result['elapsed_ms'])
            self.status = 'probe_ok' if all(item['status'] == 'ok' for item in results.values()) else 'probe_failed'
            return {'protocol': 'r002_8765', 'results': results}
        except asyncio.CancelledError:
            self.status = 'probe_failed'
            self.last_error_type = 'CancelledError'
            raise
        finally:
            self.last_probe_status = self.status
            self._notify()
            _LOGGER.debug('r002.probe.complete status=%s', self.status)

    async def stop(self, *, reason='integration_unload'):
        self.stopped = True
        task = self._task
        if task is not None and not task.done():
            task.cancel()
            await asyncio.gather(task, return_exceptions=True)
        self.listeners.clear()
