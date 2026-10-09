"""R002 diagnostics and explicitly enabled shared QV media trials."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import logging
import time

import aiohttp

from ..capabilities import DeviceVariant, MATRIX, ProtocolFamily, r002_capabilities
from ..connect3.hub import Connect3Hub
from ..connect3.cgi import CGIError, read_device
from ..connect3.certificate import inspect_certificate
from ..connect3.discovery import decode_observation
from .protocol import ALLOWED_TYPES
from .fingerprint import check_certificate
from .transport import new_counters, probe_one
from .qv_discovery import discover_qv, safe_summary
from .qv import resolve_endpoint

_LOGGER = logging.getLogger(__name__)


class R002InvestigationHub(Connect3Hub):
    variant = DeviceVariant.R002
    protocol_family = ProtocolFamily.R002
    capabilities = MATRIX[DeviceVariant.R002]
    device_model = 'WelcomeEye Connect 2 R002 (experimental)'
    capabilities_for = staticmethod(r002_capabilities)

    @property
    def supports_multichannel_player(self):
        return True

    @property
    def secondary_channel_enabled(self):
        # The original WelcomeEye APK carries a selected QV channel in PLAY
        # and order 4. Its physical mapping is unverified: only explicit
        # consent enables this trial, never the discovery channel count or
        # a persisted Connect 3 observation under another transport policy.
        return self.entry.data.get('second_channel_enabled') is True

    @property
    def confirmed_media_channels(self):
        return frozenset()

    def __init__(self, hass, entry):
        super().__init__(hass, entry)
        self.status = 'detected'
        self.last_probe_at = self.last_probe_status = self.last_error_type = None
        self._transport = new_counters()
        self._per_type = {}
        self.probe_runs = self.probe_request_count = 0
        self._certificate_check = {}
        self.certificate_check_runs = 0
        self.qv_discovery_runs = 0
        self._qv_discovery = {}

    async def start(self):
        await super().start()  # Deliberately no network or background tasks.

    def _busy(self):
        return (self.stopped or self.consumers or self._media_claim is not None
                or (self.live.task is not None and not self.live.task.done())
                or (self.channel2 is not None and self.channel2.live.task is not None
                    and not self.channel2.live.task.done())
                or (self._task is not None and not self._task.done()))

    def channel_diagnostics(self):
        result = super().channel_diagnostics()
        # This R002 trial does not persist channel observations. Reusing the
        # facade must not turn a Connect 3 profile record into R002 evidence.
        result['channel_detection'].update(observed_count=0, observed_channels=[],
            source=('explicit_option' if 'second_channel_enabled' in self.entry.data
                    else 'single_channel_default'))
        return result

    async def prepare_media_endpoint(self, observation):
        """Fresh allowlisted discovery before CGI, never an implicit retry."""
        self._authentication = dict(status='not_checked', operation='media_stream_key')
        observation['stage'] = 'r002_qv_discovery'
        return await resolve_endpoint(self.entry.data['host'], observation)

    async def execute(self, operation, *, include_details=False):
        if operation not in ('certificate', 'access'):
            raise ValueError('Unsupported R002 QV operation')
        return await super().execute(operation, include_details=include_details)

    async def _run(self, operation, include_details, start, end, channel):
        started = time.monotonic()
        self.runs += 1
        self.status = 'qv_reading'
        self._notify()
        result = dict(operation=operation, status='failed', last_stage='qv_discovery',
                      last_error_type=None)
        observation = {}
        try:
            async with asyncio.timeout(18.0):
                endpoint = await resolve_endpoint(self.entry.data['host'], observation)
                if operation == 'certificate':
                    result.update(await inspect_certificate(self.entry.data['host'],
                        port=endpoint['cgi_port'], include_details=include_details))
                elif not self.entry.data.get('auth_code'):
                    result.update(status='unavailable', reason='local_auth_code_required')
                else:
                    result['last_stage'] = 'cgi_read'
                    result.update(await read_device(self.entry.data['host'], self.entry.data['auth_code'],
                        port=endpoint['cgi_port'],
                        certificate_sha256=self.entry.data.get('certificate_sha256', ''),
                        operation='access', diagnostics=observation))
                    result['status'] = 'ok'
                self.status = 'qv_read_ok' if result['status'] in ('ok', 'observed') else 'qv_read_failed'
        except asyncio.CancelledError:
            result.update(status='cancelled', last_error_type='CancelledError')
            self.status = 'qv_read_cancelled'
            raise
        except (CGIError, ValueError, OSError, aiohttp.ClientError) as exc:
            result.update(status='failed', last_error_type=(
                'CGIError' if isinstance(exc, CGIError) else
                'TimeoutError' if isinstance(exc, TimeoutError) else
                'TLSCertificateError' if isinstance(exc, (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch))
                else 'NetworkError'))
            if isinstance(exc, CGIError):
                result['reason'] = str(exc)
            self.status = 'qv_read_failed'
        finally:
            result.update(observation)
            if operation == 'access':
                status = observation.get('authentication_status', 'not_checked')
                self._authentication = dict(status=status, operation='access')
                result['device_authenticated'] = status == 'accepted'
            result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            self._summary = {key: deepcopy(result[key]) for key in (
                'operation', 'status', 'reason', 'last_stage', 'last_error_type', 'elapsed_ms',
                'endpoint_source', 'discovery_model_matched', 'endpoint_profile',
                'discovery_request_count', 'discovery_datagrams_seen', 'media_transport',
                'media_tls_advertised', 'udt_used', 'tcp_connected', 'tls_handshake_ok',
                'tls_policy', 'tls_verified', 'http_status', 'device_error_code', 'error_source',
                'authentication_status', 'device_authenticated', 'request_sent_count',
                'streamkey_received', 'authentication', 'certificate_metadata_status',
                'certificate_serial_status', 'certificate_parser', 'certificate_parse_error_type',
                'certificate_trust_authenticated', 'certificate_pin_saved',
                'tls_certificate_cn', 'tls_certificate_issuer_cn') if key in result}
            self.last_error_type = result['last_error_type']
            self._notify()
        return result

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
        saved = {**self.entry.data.get('fingerprint', {}), **self._certificate_check}
        fp = {key: saved.get(key) if type(saved.get(key)) is bool else None for key in (
            'legacy_udp1500_response', 'tcp_8765_reachable', 'tcp_443_reachable',
            'tcp_6987_reachable', 'tcp_34567_reachable', 'tls_443_handshake_ok',
            'certificate_trust_authenticated')}
        for key in ('legacy_udp1500_probe_count', 'fingerprint_runs', 'fingerprint_elapsed_ms',
                    'certificate_check_elapsed_ms'):
            value = saved.get(key)
            fp[key] = value if type(value) is int and value >= 0 else None
        for key in ('tls_certificate_cn', 'tls_certificate_issuer_cn'):
            fp[key] = saved.get(key) if saved.get(key) in ('eziotest', 'other') else None
        error = saved.get('fingerprint_last_error_type')
        fp['fingerprint_last_error_type'] = error if error in (
            'TimeoutError', 'ConnectionRefusedError', 'ConnectionResetError', 'OSError',
            'SSLError', 'ValueError', 'ImportError', 'ModuleNotFoundError', 'CertificateMetadataError') else None
        for key, allowed in {
            'certificate_metadata_status': ('not_received', 'parsed', 'invalid'),
            'certificate_serial_status': ('positive', 'non_positive', 'unknown'),
            'certificate_parser': ('bounded_der_metadata_v1',),
            'certificate_parse_error_type': ('CertificateMetadataError',),
            'certificate_check_error_type': ('TimeoutError', 'ConnectionRefusedError',
                'ConnectionResetError', 'OSError', 'SSLError'),
        }.items():
            fp[key] = saved.get(key) if saved.get(key) in allowed else None
        return {
            **super().diagnostics(),
            **fp, 'detected': saved.get('detected') is True,
            'detection_confidence': self.detection_confidence,
            'detection_source': 'udp_timeout_tcp_tls',
            'model_source': 'r002_experimental_fingerprint',
            'identity_source': 'provisional_host_hash',
            'probe_runs': self.probe_runs, 'probe_request_count': self.probe_request_count,
            'certificate_check_runs': self.certificate_check_runs,
            'qv_discovery_runs': self.qv_discovery_runs,
            'qv_discovery': deepcopy(self._qv_discovery),
            'per_type': deepcopy(self._per_type), 'transport': dict(self._transport),
            'last_stage': self.status, 'last_error_type': self.last_error_type,
        }

    async def probe(self, types=ALLOWED_TYPES, *, include_header=False, include_response=False):
        types = tuple(types)
        if not types or len(types) > 4 or len(set(types)) != len(types) or any(
            type(kind) is not int or kind not in ALLOWED_TYPES for kind in types
        ):
            raise ValueError('Only investigation types 14, 15, 26, 28 are permitted')
        if self.stopped:
            raise RuntimeError('Investigation entry is stopped')
        if self._busy():
            raise RuntimeError('An investigation probe is already running')
        self._task = asyncio.create_task(self._probe(types, include_header, include_response), name='welcomeeye-r002-probe')
        try:
            return await self._task
        finally:
            if self._task.done():
                self._task = None

    async def _probe(self, types, include_header, include_response):
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
                result = await probe_one(self.entry.data['host'], kind, self._transport,
                                         include_header=include_header, include_response=include_response)
                results[str(kind)] = result
                # Never persist the detailed service response or candidate values.
                safe = {key: deepcopy(result[key]) for key in (
                    'requested_type', 'status', 'last_stage', 'last_error_type',
                    'header_valid', 'header_validation_errors', 'prefix_bytes_received',
                    'declared_length', 'received_length', 'elapsed_ms',
                    'observation_status', 'collection_end_reason', 'bytes_collected') if key in result}
                self._per_type[str(kind)] = {**safe, 'attempts': previous.get('attempts', 0) + 1,
                    'successes': previous.get('successes', 0) + int(result['status'] == 'ok'),
                    'observations': previous.get('observations', 0) + int(result['status'] == 'observed')}
                if result['last_error_type'] is not None:
                    self.last_error_type = result['last_error_type']
                _LOGGER.debug('r002.probe.response type=%d status=%s length=%s elapsed_ms=%d',
                    kind, result['status'], result.get('received_length'), result['elapsed_ms'])
            expected_status = 'observed' if include_response else 'ok'
            self.status = ('probe_observed' if include_response else 'probe_ok') if all(
                item['status'] == expected_status for item in results.values()) else 'probe_failed'
            return {'protocol': 'r002_8765', 'results': results}
        except asyncio.CancelledError:
            self.status = 'probe_failed'
            self.last_error_type = 'CancelledError'
            raise
        finally:
            self.last_probe_status = self.status
            self._notify()
            _LOGGER.debug('r002.probe.complete status=%s', self.status)

    async def check_certificate(self):
        if self._busy():
            raise RuntimeError('Investigation unavailable or busy')
        self._task = asyncio.create_task(check_certificate(self.entry.data['host']),
                                        name='welcomeeye-r002-certificate')
        try:
            result = await self._task
            self.certificate_check_runs += 1
            self._certificate_check = result
            self._notify()
            return {key: value for key, value in self.diagnostics().items()
                    if key.startswith(('certificate_', 'tls_')) or key == 'tcp_443_reachable'}
        finally:
            if self._task.done():
                self._task = None

    async def discover_qv(self, *, include_response=False, include_details=False):
        if self._busy():
            raise RuntimeError('Investigation unavailable or busy')
        self._task = asyncio.create_task(self._discover_qv(include_response, include_details),
                                        name='welcomeeye-r002-qv-discovery')
        try:
            return await self._task
        finally:
            if self._task.done():
                self._task = None

    async def _discover_qv(self, include_response, include_details):
        self.qv_discovery_runs += 1
        self.status = 'qv_discovering'
        self.last_error_type = None
        self._notify()
        try:
            # Decode this one already-bounded observation. Never issue another
            # broadcast to obtain metadata, and never infer R002 authentication.
            result = await discover_qv(self.entry.data['host'], include_response=True)
            decoded = decode_observation(self.entry.data['host'], result,
                                         include_details=include_details)
            result.update(decoded)
            if not include_response:
                for response in result['responses']:
                    response.pop('response_hex', None)
            self._qv_discovery = {**safe_summary(result), **{
                key: deepcopy(result[key]) for key in (
                    'decoded_records', 'duplicate_records', 'decode_errors', 'metadata_decoded')}}
            self.last_error_type = result['last_error_type']
            self.status = ('qv_decoded' if result['metadata_decoded'] else 'qv_observed') if (
                result['status'] == 'observed') else 'qv_failed'
            return result
        except asyncio.CancelledError:
            self.status = 'qv_failed'
            self.last_error_type = 'CancelledError'
            raise
        except (ValueError, RuntimeError) as exc:
            self.status = 'qv_failed'
            self.last_error_type = type(exc).__name__
            raise
        finally:
            self._notify()

    async def stop(self, *, reason='integration_unload'):
        await super().stop(reason=reason)
