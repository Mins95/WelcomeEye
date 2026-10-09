"""Connect 3 shared live media and explicit experimental controls."""
import asyncio
from copy import deepcopy
from datetime import datetime, timezone
import ssl
import time

import aiohttp

from ..capabilities import DeviceVariant, MATRIX, ProtocolFamily, connect3_capabilities
from .cgi import CGIError, read_device
from .certificate import inspect_certificate
from .channels import ChannelBusyError, channel2_enabled, media_profile_binding, valid_observed_channels
from .discovery import discover
from .control import Connect3OutputController
from .talk import Talkback
from .doorbell import DoorbellObservation
from .protocol import MediaProtocolError
from .tls import MediaTLSFailure
from .trust import date_exception_matches, key_exception_matches, trust_endpoint_matches
from ..snapshot import _finish_task


class Connect3Hub:
    channel = 1
    variant = DeviceVariant.CONNECT3
    protocol_family = ProtocolFamily.CONNECT3
    capabilities = MATRIX[DeviceVariant.CONNECT3]
    device_model = 'WelcomeEye Connect 3'
    capabilities_for = staticmethod(connect3_capabilities)

    @property
    def supports_multichannel_player(self):
        return self.variant == DeviceVariant.CONNECT3

    @property
    def confirmed_media_channels(self):
        return valid_observed_channels(self.entry.data)

    def __init__(self, hass, entry):
        self.hass, self.entry = hass, entry
        self.stopped = True
        self.status = 'declared'
        self.listeners = set()
        self._task = None
        self._stop_task = None
        self._summary = {}
        self._authentication = {'status': 'not_checked', 'operation': None}
        self._tls_blocked_reason = None
        self._tls_blocked_endpoint = None
        self._tls_close_task = None
        self._media_claim = None
        self._observed_channels = set(valid_observed_channels(entry.data))
        self._observed_binding = media_profile_binding(entry.data)
        self._channel_observation_save_error_type = None
        self.runs = 0
        from .live import LiveMedia
        self.capabilities = self.capabilities_for(entry.data.get('experimental_video', False),
            entry.data.get('experimental_outputs', False) is True and bool(entry.data.get('opening_code')),
            **({'media_transport': entry.data.get('media_transport', 'tls'),
                'tcp_controls_enabled': entry.data.get('experimental_tcp_controls', False)}
               if self.variant == DeviceVariant.CONNECT3 else {}))
        self.frame_listeners = set()
        self.close_listeners = set()
        self.webrtc_diagnostics = {}
        self.ring_image_capture_entity_id = None
        self.live = LiveMedia(self)
        self.talkback = Talkback(self)
        self.control = Connect3OutputController(self)
        self.doorbell = DoorbellObservation(self)
        self.channel2 = None
        if (self.variant == DeviceVariant.CONNECT3 and self.capabilities.live_media
                and channel2_enabled(entry.data)):
            from .channel2 import Channel2Media
            self.channel2 = Channel2Media(self)

    def record_channel_observed(self, channel, observation, binding):
        """Persist only a decoded stream under the unchanged approved profile."""
        if (self.variant != DeviceVariant.CONNECT3 or type(channel) is not int or channel not in (1, 2)
                or binding is None or binding != media_profile_binding(self.entry.data)
                or observation.get('cgi_https_verified') is not True
                or observation.get('play_accepted') is not True
                or observation.get('decoded_frames', 0) <= 0):
            return
        if self._observed_binding != binding:
            self._observed_binding = binding
            self._observed_channels = set(valid_observed_channels(self.entry.data))
        self._observed_channels.add(channel)
        observed = set(valid_observed_channels(self.entry.data)) | self._observed_channels
        record = {'binding': binding, 'channels': sorted(observed)}
        if self.entry.data.get('observed_media_channels') == record:
            return
        if self.hass is None:
            return
        try:
            self.hass.config_entries.async_update_entry(self.entry,
                data={**self.entry.data, 'observed_media_channels': record})
            self._channel_observation_save_error_type = None
        except Exception as exc:
            # Persisting a private observation must never terminate a valid
            # stream. No exception text (possibly containing paths) escapes.
            self._channel_observation_save_error_type = type(exc).__name__

    def channel_diagnostics(self):
        """A selected channel means QV PLAY/frames, not physical panel proof."""
        observed = (self._observed_channels if self._observed_binding == media_profile_binding(self.entry.data)
                    else set(valid_observed_channels(self.entry.data)))
        source = ('explicit_option' if 'second_channel_enabled' in self.entry.data
                  else 'observed_stream' if 2 in valid_observed_channels(self.entry.data)
                  else 'single_channel_default')
        channels = {'1': {'requested_channel': 1,
            'selected_channel': self.live.observation.get('selected_channel'),
            'physical_channel_verified': False,
            'active': bool(self.consumers),
            'media': deepcopy(self.live.observation),
            'webrtc': {key: deepcopy(self.webrtc_diagnostics[key]) for key in (
                'stage', 'failed_at_stage', 'last_exception_type', 'active_viewers',
                'requested_tracks', 'created_tracks', 'downstream_frames_queued',
                'connection_state', 'ice_connection_state', 'negotiation_ok',
                'cleanup_stage', 'cleanup_failed_stage', 'cleanup_error_type')
                if key in self.webrtc_diagnostics}}}
        if self.channel2 is not None:
            channels['2'] = self.channel2.diagnostics()
        return {'channels': channels, 'channel_detection': {
            'configured_count': 2 if self.channel2 is not None else 1,
            'observed_count': len(observed),
            'observed_channels': sorted(observed), 'source': source,
            'observation_save_error_type': self._channel_observation_save_error_type}}

    def _claim_media(self, live):
        """Atomically reserve one QV live reader before any network await."""
        if self.variant != DeviceVariant.CONNECT3:
            return
        if self._media_claim is not None and self._media_claim is not live:
            raise ChannelBusyError('Connect 3 other channel busy')
        if (self.stopped or (self._task is not None and not self._task.done())):
            raise RuntimeError('Connect 3 unavailable or busy')
        if live is not self.live and (self.control._busy or self.talkback.owner is not None
                or self.talkback.active or self.live.consumers
                or (self.live.task is not None and not self.live.task.done())):
            raise ChannelBusyError('Connect 3 main channel busy')
        self._media_claim = live

    def _release_media(self, live):
        if self._media_claim is live and not live.consumers:
            self._media_claim = None

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
        if not self.stopped:
            self.check_tls_trust()
        await self.live.acquire(owner)

    async def release(self, owner, *, reason='viewer_closed'):
        await self.live.release(owner, reason=reason)

    async def prepare_media_endpoint(self, observation):
        """Select only explicit Connect 3 policy without manual-IP discovery."""
        data = self.entry.data
        self._authentication = {'status': 'not_checked', 'operation': 'media_stream_key'}
        transport = data.get('media_transport', 'tls')
        port = 34567 if transport == 'connect3_tcp' else data.get('media_port', 8443)
        cgi_port = data.get('cgi_port', 443)
        if transport == 'connect3_tcp' and data.get('media_tcp_approved') is not True:
            raise CGIError('connect3_tcp_approval_required')
        if (transport not in ('tls', 'connect3_tcp') or type(port) is not int
                or not 1 <= port <= 65535 or type(cgi_port) is not int
                or not 1 <= cgi_port <= 65535 or (transport == 'connect3_tcp' and port != 34567)):
            raise CGIError('invalid_media_transport_policy')
        observation.update(media_transport_selected=transport, media_port_selected=port)
        # Preserve the default TLS session call and its independent pin policy.
        return {'cgi_port': cgi_port, 'port': port, 'transport': transport} if transport == 'connect3_tcp' else None

    async def start(self):
        if self._stop_task is not None and not self._stop_task.done():
            raise RuntimeError('Connect 3 is stopping')
        self._stop_task = None
        if self.control.closed:
            self.control = Connect3OutputController(self)
        self.stopped = False
        if self.variant == DeviceVariant.CONNECT3 and self.hass is not None and getattr(self.entry, 'entry_id', None):
            from ..repairs import async_get_tls_issue
            issue = async_get_tls_issue(self.hass, self.entry.entry_id)
            if issue is not None:
                self._tls_blocked_reason = issue['reason']
                self._tls_blocked_endpoint = issue['endpoint']
                self.status = 'tls_reapproval_required'

    def _block_tls(self, reason, endpoint):
        """Keep trust failures fixed and private; only approval can replace a pin."""
        if self.variant != DeviceVariant.CONNECT3:
            return
        self._tls_blocked_reason, self._tls_blocked_endpoint = reason, endpoint
        self.status = 'tls_reapproval_required'
        if self.hass is not None and getattr(self.entry, 'entry_id', None):
            from ..repairs import async_report_tls_issue
            async_report_tls_issue(self.hass, self.entry.entry_id, reason, endpoint)
        active = [live for live in (self.live, self.channel2.live if self.channel2 else None)
                  if live is not None and live.task is not None and not live.task.done()
                  and live.task is not asyncio.current_task()]
        if (active
                and (self._tls_close_task is None or self._tls_close_task.done())):
            # A changed certificate on the separate talk socket also closes
            # an existing live lease. Trust failure never leaves output access.
            for live in active:
                live.connected = False
            async def close_media():
                for live in active:
                    if self.channel2 is not None and live is self.channel2.live:
                        await self.channel2.stop(reason='tls_reapproval_required')
                    else:
                        await live.stop()
            self._tls_close_task = asyncio.create_task(close_media(), name='welcomeeye-connect3-tls-close')
            self._tls_close_task.add_done_callback(lambda task: task.exception() if not task.cancelled() else None)

    def check_tls_trust(self):
        """Guard only new Connect 3 trust metadata before credential-bearing I/O."""
        if self.variant != DeviceVariant.CONNECT3:
            return
        data = self.entry.data
        if data.get('media_transport', 'tls') == 'connect3_tcp' and data.get('media_tcp_approved') is not True:
            raise CGIError('connect3_tcp_approval_required')
        if self._tls_blocked_reason is None and not trust_endpoint_matches(data):
            self._block_tls('endpoint_changed', 'both')
        expiry = data.get('tls_certificate_expires')
        date_exceptions = data.get('tls_certificate_date_exceptions')
        key_exceptions = data.get('tls_certificate_key_exceptions')
        required_endpoints = ('cgi',) if data.get('media_transport', 'tls') == 'connect3_tcp' else ('cgi', 'media')
        if self._tls_blocked_reason is None:
            if key_exceptions is not None and type(key_exceptions) is not dict:
                self._block_tls('certificate_changed', 'both')
            elif type(key_exceptions) is dict:
                for endpoint in required_endpoints:
                    if endpoint not in key_exceptions:
                        continue
                    pin = data.get('certificate_sha256', '')
                    if endpoint == 'media':
                        pin = data.get('media_certificate_sha256', '') or pin
                    # A present weak-key agreement must remain bound even
                    # for legacy metadata or a future certificate expiry.
                    if (data.get('trust_endpoint') is None
                            or not key_exception_matches(key_exceptions[endpoint], pin)):
                        self._block_tls('certificate_changed', endpoint)
                        break
        legacy_expiry = data.get('trust_endpoint') is None and (
            expiry is None or (type(expiry) is dict and not expiry)) and (
                date_exceptions is None or (type(date_exceptions) is dict
                    and not any(endpoint in date_exceptions for endpoint in required_endpoints)))
        if self._tls_blocked_reason is None and not legacy_expiry:
            if (type(expiry) is not dict or not set(required_endpoints) <= set(expiry)
                    or set(expiry) - {'cgi', 'media'}
                    or (date_exceptions is not None and type(date_exceptions) is not dict)):
                self._block_tls('certificate_expired', 'both')
                raise CGIError('tls_reapproval_required')
            now = datetime.now(timezone.utc)
            for endpoint in required_endpoints:
                try:
                    value = expiry[endpoint]
                    if not isinstance(value, str):
                        raise ValueError
                    until = datetime.fromisoformat(value)
                    if until.tzinfo is None or until.utcoffset() is None:
                        raise ValueError
                    valid = until > now
                    if type(date_exceptions) is dict and endpoint in date_exceptions:
                        pin = data.get('certificate_sha256', '')
                        if endpoint == 'media':
                            pin = data.get('media_certificate_sha256', '') or pin
                        # Approval is specific to this endpoint, exact pin and
                        # original zero-duration date. Other expiry checks and
                        # endpoint binding keep their existing strict behavior.
                        valid = (data.get('trust_endpoint') is not None
                            and date_exception_matches(date_exceptions[endpoint], pin, after=value))
                except (KeyError, TypeError, ValueError, OverflowError):
                    valid = False
                if not valid:
                    self._block_tls('certificate_expired', endpoint)
                    break
        if self._tls_blocked_reason is not None:
            raise CGIError('tls_reapproval_required')

    def report_tls_error(self, exc, *, endpoint):
        """Classify trust errors without serializing network exception contents."""
        if self.variant != DeviceVariant.CONNECT3:
            return None
        reason = None
        if isinstance(exc, aiohttp.ServerFingerprintMismatch):
            reason = 'certificate_changed'
        elif isinstance(exc, (MediaTLSFailure, MediaProtocolError)) and str(exc) in (
                'media_certificate_pin_mismatch', 'certificate_mismatch'):
            reason = 'certificate_changed'
        elif isinstance(exc, (aiohttp.ClientConnectorCertificateError, ssl.SSLCertVerificationError)):
            reason = 'system_ca_failed'
        elif isinstance(exc, aiohttp.ClientSSLError) and isinstance(
                getattr(exc, 'os_error', None), ssl.SSLCertVerificationError):
            reason = 'system_ca_failed'
        if reason is not None:
            self._block_tls(reason, endpoint)
        return reason

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
                'experimental_tcp_controls_enabled': (self.variant == DeviceVariant.CONNECT3
                    and self.entry.data.get('media_transport', 'tls') == 'connect3_tcp'
                    and self.entry.data.get('experimental_tcp_controls') is True),
                'media_received': self.live.observation.get('decoded_frames', 0) > 0,
                'media': {**self.live.observation, 'active_consumers': len(self.consumers),
                          'session_attempts': self.live.session_count,
                          **({'media_transport_selected': self.entry.data.get('media_transport', 'tls')
                              if self.entry.data.get('media_transport', 'tls') in ('tls', 'connect3_tcp') else 'unknown',
                              'media_port_selected': 34567 if self.entry.data.get('media_transport', 'tls') == 'connect3_tcp'
                              else self.entry.data.get('media_port', 8443)
                              if type(self.entry.data.get('media_port', 8443)) is int else None,
                              'media_tcp_connected': self.live.observation.get('media_tcp_connected', False),
                              'media_setup_accepted': self.live.observation.get('setup_accepted', False),
                              'media_play_accepted': self.live.observation.get('play_accepted', False),
                              'media_packets_received': self.live.observation.get('media_packets', 0),
                              'cgi_https_verified': self.live.observation.get('cgi_https_verified', False)}
                             if self.variant == DeviceVariant.CONNECT3 else {}),
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
                **({'tls_trust': {'status': 'reapproval_required' if self._tls_blocked_reason else 'not_checked',
                    'reason': self._tls_blocked_reason, 'endpoint': self._tls_blocked_endpoint}}
                   if self.variant == DeviceVariant.CONNECT3 else {}),
                'device_authenticated': self._authentication['status'] == 'accepted',
                'authentication': dict(self._authentication),
                **(self.channel_diagnostics() if self.variant == DeviceVariant.CONNECT3 else {}),
                'runs': self.runs, 'status': self.status, 'last_operation': deepcopy(self._summary)}

    async def execute(self, operation, *, include_details=False, start=None, end=None, channel=1):
        if operation not in ('discovery', 'certificate', 'media_certificate', 'access', 'history'):
            raise ValueError('Unsupported Connect 3 operation')
        if (self.stopped or self.consumers or self._media_claim is not None
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
            elif operation == 'media_certificate' and self.entry.data.get('media_transport', 'tls') == 'connect3_tcp':
                result.update(status='unavailable', reason='media_tls_not_selected')
                self.status = 'media_tcp_selected'
            elif operation in ('certificate', 'media_certificate'):
                result.update(await inspect_certificate(self.entry.data['host'],
                    port=(self.entry.data.get('media_port', 8443) if operation == 'media_certificate'
                          else self.entry.data.get('cgi_port', 443)), include_details=include_details))
                self.status = 'certificate_observed' if result['status'] == 'observed' else 'read_failed'
            elif not self.entry.data.get('auth_code'):
                result.update(status='unavailable', reason='local_auth_code_required')
                self.status = 'credentials_required'
            else:
                self.check_tls_trust()
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
            if self.report_tls_error(exc, endpoint='cgi') is not None:
                result['reason'] = 'tls_reapproval_required'
            self.status = 'tls_reapproval_required' if self._tls_blocked_reason else 'read_failed'
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
                'last_error_reason', 'certificate_port',
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
        if self.channel2 is not None:
            await self.channel2.stop(reason='integration_unload')
        if self._tls_close_task is not None:
            await _finish_task(self._tls_close_task, cancel_on_cancel=False)
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
