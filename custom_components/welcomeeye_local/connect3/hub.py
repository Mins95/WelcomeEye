"""Connect 3 explicit read operations; no polling, media or physical commands."""
import asyncio
from copy import deepcopy
import time

import aiohttp

from ..capabilities import DeviceVariant, MATRIX, ProtocolFamily
from .cgi import CGIError, read_device
from .certificate import inspect_certificate
from .discovery import discover


class Connect3Hub:
    variant = DeviceVariant.CONNECT3
    protocol_family = ProtocolFamily.CONNECT3
    capabilities = MATRIX[DeviceVariant.CONNECT3]
    device_model = 'WelcomeEye Connect 3 (declared, experimental)'

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.stopped = True
        self.status = 'declared'
        self.listeners = set()
        self._task = None
        self._summary = {}
        self.runs = 0

    async def start(self):
        self.stopped = False

    def subscribe(self, listener):
        self.listeners.add(listener)
        return lambda: self.listeners.discard(listener)

    def _notify(self):
        if not self.stopped:
            for callback in tuple(self.listeners):
                callback()

    def diagnostics(self):
        source = self.entry.data.get('credential_source', 'manual' if self.entry.data.get('auth_code') else 'none')
        return {'identity_source': 'provisional_random', 'model_source': 'user_declared',
                'model_confirmed': False, 'hardware_validated': False,
                'media_available': False, 'cloud_used': False,
                'local_credential_configured': bool(self.entry.data.get('auth_code')),
                'credential_source': source if source in ('manual', 'apk_json', 'apk_space') else 'none',
                'certificate_pin_configured': bool(self.entry.data.get('certificate_sha256')),
                'runs': self.runs, 'status': self.status, 'last_operation': deepcopy(self._summary)}

    async def execute(self, operation, *, include_details=False, start=None, end=None, channel=1):
        if operation not in ('discovery', 'certificate', 'access', 'history'):
            raise ValueError('Unsupported Connect 3 operation')
        if self.stopped or (self._task is not None and not self._task.done()):
            raise RuntimeError('Connect 3 unavailable or busy')
        task = asyncio.create_task(self._run(operation, include_details, start, end, channel),
                                   name='welcomeeye-connect3-read')
        self._task = task
        try:
            return await task
        finally:
            if self._task is task:
                self._task = None

    async def _run(self, operation, include_details, start, end, channel):
        started = time.monotonic()
        self.runs += 1
        self.status = 'reading'
        self._notify()
        result = {'operation': operation, 'last_stage': operation, 'last_error_type': None}
        try:
            if operation == 'discovery':
                result.update(await discover(self.entry.data['host'], include_details=include_details))
                self.status = 'qv_decoded' if result['decoded_records'] else 'discovery_inconclusive'
            elif operation == 'certificate':
                result.update(await inspect_certificate(self.entry.data['host'],
                    port=self.entry.data.get('cgi_port', 443), include_details=include_details))
                self.status = 'certificate_observed' if result['status'] == 'observed' else 'read_failed'
            elif not self.entry.data.get('auth_code'):
                result.update(status='unavailable', reason='local_auth_code_required')
                self.status = 'credentials_required'
            else:
                expected_uid = self.entry.data.get('credential_device_uid')
                if self.entry.data.get('credential_source') in ('apk_json', 'apk_space') and not expected_uid:
                    result.update(status='unavailable', reason='credential_identity_required')
                    self.status = 'credentials_required'
                    return result
                if expected_uid:
                    result['last_stage'] = 'credential_identity'
                    observed = await discover(self.entry.data['host'], expected_uid=expected_uid)
                    identity_status = observed['credential_identity_status']
                    result['credential_identity_status'] = identity_status
                    if identity_status != 'matched':
                        result.update(status='unavailable', reason='credential_identity_not_matched')
                        self.status = 'identity_unconfirmed'
                        return result
                result['last_stage'] = 'cgi_read'
                result.update(await read_device(self.entry.data['host'], self.entry.data['auth_code'],
                    port=self.entry.data.get('cgi_port', 443),
                    certificate_sha256=self.entry.data.get('certificate_sha256', ''),
                    operation=operation, start=start, end=end, channel=channel,
                    include_details=include_details))
                result['status'] = 'ok'
                self.status = 'cgi_accepted'
        except asyncio.CancelledError:
            result.update(status='cancelled', last_error_type='CancelledError')
            self.status = 'cancelled'
            raise
        except (CGIError, ValueError, OSError, aiohttp.ClientError) as exc:
            # No str(network_exception), URL, pin, remote certificate or secret.
            result.update(status='failed', last_error_type=(
                'CGIError' if isinstance(exc, CGIError) else
                'TimeoutError' if isinstance(exc, TimeoutError) else
                'TLSCertificateError' if isinstance(exc, (aiohttp.ClientSSLError, aiohttp.ServerFingerprintMismatch))
                else 'NetworkError'))
            if isinstance(exc, CGIError):
                result['reason'] = str(exc)
            self.status = 'read_failed'
        finally:
            result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            # Explicit allowlist: no remote strings/records or arbitrary fields.
            self._summary = {key: deepcopy(result[key]) for key in (
                'operation', 'status', 'reason', 'last_stage', 'last_error_type', 'elapsed_ms',
                'credential_identity_status', 'tcp_connected', 'tls_handshake_ok',
                'certificate_metadata_status', 'certificate_serial_status', 'certificate_parser',
                'certificate_parse_error_type', 'certificate_trust_authenticated', 'certificate_pin_saved',
                'tls_certificate_cn', 'tls_certificate_issuer_cn',
                'request_sent_count', 'datagrams_seen', 'matching_datagrams',
                'ignored_datagrams', 'bytes_collected', 'truncated_datagrams',
                'decoded_records', 'duplicate_records', 'decode_errors',
                'record_count', 'pages_read', 'history_complete', 'streamkey_received',
                'authentication') if key in result}
            self._notify()
        return result

    async def stop(self, *, reason='integration_unload'):
        self.stopped = True
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        self.listeners.clear()
