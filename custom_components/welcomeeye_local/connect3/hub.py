"""Connect 3 shared live media and explicit experimental controls."""
import asyncio
from copy import deepcopy
import time

import aiohttp

from ..capabilities import DeviceVariant, MATRIX, ProtocolFamily, connect3_capabilities
from .cgi import CGIError, read_device
from .certificate import inspect_certificate
from .discovery import discover
from .control import Connect3OutputController
from .talk import Talkback
from .doorbell import DoorbellObservation
from ..snapshot import _finish_task


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
        self._stop_task = None
        self._summary = {}
        self._authentication = {'status': 'not_checked', 'operation': None}
        self.runs = 0
        from .live import LiveMedia
        self.capabilities = connect3_capabilities(entry.data.get('experimental_video', False),
            entry.data.get('experimental_outputs', False) is True and bool(entry.data.get('opening_code')))
        self.frame_listeners = set()
        self.close_listeners = set()
        self.webrtc_diagnostics = {}
        self.ring_image_capture_entity_id = None
        self.live = LiveMedia(self)
        self.talkback = Talkback(self)
        self.control = Connect3OutputController(self)
        self.doorbell = DoorbellObservation(self)

    @property
    def connected(self):
        return self.live.connected

    @property
    def image(self):
        return self.live.image

    @property
    def consumers(self):
        return self.live.consumers

    async def acquire(self, owner):
        await self.live.acquire(owner)

    async def release(self, owner, *, reason='viewer_closed'):
        await self.live.release(owner, reason=reason)

    async def start(self):
        if self._stop_task is not None and not self._stop_task.done():
            raise RuntimeError('Connect 3 is stopping')
        self._stop_task = None
        if self.control.closed:
            self.control = Connect3OutputController(self)
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
                'media_available': self.capabilities.live_media, 'cloud_used': False,
                'experimental_video_enabled': self.capabilities.live_media,
                'media_received': self.live.observation.get('decoded_frames', 0) > 0,
                'media': {**self.live.observation, 'active_consumers': len(self.consumers),
                          'session_attempts': self.live.session_count,
                          'worker_active': self.live.task is not None and not self.live.task.done()},
                'previous_media_sessions': deepcopy(list(self.live.previous_sessions)),
                'audio': deepcopy(self.live.observation.get('audio', {
                    'status': 'not_observed', 'input_packets': 0, 'decoded_frames': 0})),
                'microphone': self.talkback.diagnostics,
                'control': {**self.control.diagnostics(),
                    'experimental_outputs_enabled': self.entry.data.get('experimental_outputs', False) is True,
                    'opening_code_configured': bool(self.entry.data.get('opening_code'))},
                'doorbell': self.doorbell.diagnostics(),
                'webrtc': {key: self.webrtc_diagnostics[key] for key in (
                    'stage', 'failed_at_stage', 'last_exception_type', 'active_viewers',
                    'requested_tracks', 'created_tracks', 'downstream_frames_queued',
                    'inbound_audio_frames_received', 'microphone_start_requests',
                    'microphone_stop_requests', 'microphone_error_type',
                    'connection_state', 'ice_connection_state', 'negotiation_ok',
                    'cleanup_stage', 'cleanup_failed_stage', 'cleanup_error_type')
                    if key in self.webrtc_diagnostics},
                'local_credential_configured': bool(self.entry.data.get('auth_code')),
                'credential_source': source if source in ('manual', 'apk_json', 'apk_space') else 'none',
                'certificate_pin_configured': bool(self.entry.data.get('certificate_sha256')),
                'device_authenticated': self._authentication['status'] == 'accepted',
                'authentication': dict(self._authentication),
                'runs': self.runs, 'status': self.status, 'last_operation': deepcopy(self._summary)}

    async def execute(self, operation, *, include_details=False, start=None, end=None, channel=1):
        if operation not in ('discovery', 'certificate', 'media_certificate', 'access', 'history'):
            raise ValueError('Unsupported Connect 3 operation')
        if (self.stopped or self.consumers
                or (self.live.task is not None and not self.live.task.done())
                or (self._task is not None and not self._task.done())):
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
        observation = {}
        try:
            if operation == 'discovery':
                result.update(await discover(self.entry.data['host'], include_details=include_details))
                self.status = 'qv_decoded' if result['decoded_records'] else 'discovery_inconclusive'
            elif operation in ('certificate', 'media_certificate'):
                result.update(await inspect_certificate(self.entry.data['host'],
                    port=(self.entry.data.get('media_port', 8443) if operation == 'media_certificate'
                          else self.entry.data.get('cgi_port', 443)), include_details=include_details))
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
                    include_details=include_details, diagnostics=observation))
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
            result.update(observation)
            if operation in ('access', 'history'):
                status = observation.get('authentication_status',
                    'accepted' if result.get('authentication') == 'cgi_accepted' else 'not_checked')
                self._authentication = {'status': status, 'operation': operation}
                result['device_authenticated'] = status == 'accepted'
            result['elapsed_ms'] = round((time.monotonic() - started) * 1000)
            # Explicit allowlist: no remote strings/records or arbitrary fields.
            self._summary = {key: deepcopy(result[key]) for key in (
                'operation', 'status', 'reason', 'last_stage', 'last_error_type', 'elapsed_ms',
                'credential_identity_status', 'tcp_connected', 'tls_handshake_ok',
                'tls_policy', 'tls_verified', 'http_status', 'device_error_code', 'error_source',
                'authentication_status', 'device_authenticated',
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
        if self._stop_task is None:
            self._stop_task = asyncio.create_task(self._stop(), name='welcomeeye-connect3-stop')
        await _finish_task(self._stop_task, cancel_on_cancel=False)

    async def _stop(self):
        self.doorbell.finish()
        if self._task is not None and not self._task.done():
            self._task.cancel()
            await asyncio.gather(self._task, return_exceptions=True)
        await self.control.close()
        if self.close_listeners:
            await asyncio.gather(*(callback() for callback in tuple(self.close_listeners)),
                                 return_exceptions=True)
        await self.live.stop()
        await self.talkback.close()
        self.frame_listeners.clear()
        self.close_listeners.clear()
        self.listeners.clear()
