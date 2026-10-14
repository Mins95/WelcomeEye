"""Voluntary, bounded native alarm diagnostic, independent of live video.

See docs/connect3-native-alarm-evidence.md. SDK control and QV live packets
are different protocols on the configured endpoint. The modern protected path
and native V2 inside verified TLS are separate; notification metadata is never
a confirmed HA ring. V2 over TCP requires a separate per-device diagnostic
approval because the encoded login credential is reusable and unencrypted.
"""
import asyncio
from copy import deepcopy
import time

import aiohttp

from . import alarm_protocol as wire
from .alarm_protocol import (AlarmProtocolError, build_control_setup_request,
                             parse_control_setup_response)
from .alarm_stream import ControlStream, ExplicitTCPControlStream, TLSControlStream
from .cgi import CGIError, encode_auth_code, read_stream_material
from .tls import MediaTLSFailure, open_connect3_media_tcp, open_media_tls
from ..r002.transport import close_writer
from ..snapshot import _finish_task

SETUP_TIMEOUT = 5.0
LOGIN_TIMEOUT = 5.0
HEARTBEAT_INTERVAL = 10.0
MAX_DURATION = 90
NATIVE_LISTENER_BLOCKERS = (
    'firmware_alarm_subscription_unestablished',
    'ring_event_channel_mapping_unestablished',
)


class AutonomousDoorbellDiagnostic:
    """One active control connection per explicit action, without retries."""

    def __init__(self, hub=None):
        self.hub = hub
        self.task = self._writer = self._started = None
        self._unloaded = False
        self._closing = False
        self.start_attempts = 0
        self._data = self._empty()

    @staticmethod
    def _empty():
        return dict(operation='autonomous_doorbell', requested_operation='status',
            diagnostic_scope='native_alarm_subscription', status='not_started',
            last_stage='not_started', last_error_type=None, last_error_reason=None,
            blockers=list(NATIVE_LISTENER_BLOCKERS), requested_duration_seconds=None,
            maximum_duration_seconds=MAX_DURATION, effective_duration_seconds=0,
            start_attempts=0, listening=False, listen_established=False,
            connection_attempts=0, tcp_connected=False, tls_verified=False,
            transport=None, port=None, endpoint_source='configured_media_endpoint_candidate',
            control_endpoint_confirmed=False, authentication_status='not_attempted',
            credentials_sent=False, login_request_count=0, setup_request_count=0,
            setup_response_received=False, setup_selector=100, setup_accepted=False,
            protocol_version=None, encryption_mode=None, sha_mode=None,
            setup_variant=None, setup_response_selector=None, crypto_mode_source=None,
            control_reconnect_required=False, control_reconnect_performed=False,
            control_login_profile=None, credential_protection='not_sent',
            legacy_tcp_login_approved=False,
            protected_login_capability=False, subscription_request_count=0,
            cgi_https_verified=False, cgi_tls_policy='not_checked', streamkey_received=False,
            firmware_subscription_confirmed=False, notifications_observed=0,
            heartbeat_count=0, control_records_received=0, control_bytes_received=0,
            native_checksum_verified=False, control_peer_authenticated=False,
            setup_transcript_authenticated=False, replay_protected=False,
            command_counts={}, events_omitted=0, observation_category='listen_not_established',
            messages_received=0, bytes_received=0, notification_candidates=0,
            hangup_candidates=0, physical_ring_confirmed=False, ring_events_emitted=0,
            events=[], elapsed_ms=0, video_session_created=False,
            microphone_session_created=False, connection_closed=True,
            cleanup_status='no_connection_opened', close_reason=None,
            integration_unloaded=False)

    @property
    def active(self):
        return self.task is not None and not self.task.done()

    def _busy(self):
        hub = self.hub
        if hub is None or hub.stopped:
            return True
        if (hub._media_claim is not None or hub.control._busy
                or hub.talkback.active or hub.talkback.owner is not None
                or (hub._task is not None and not hub._task.done())):
            return True
        lives = (hub.live, hub.channel2.live if hub.channel2 else None)
        return any(live is not None and (live.consumers or
            (live.task is not None and not live.task.done())) for live in lives)

    def _endpoint(self):
        """Use the configured endpoint as a candidate, never scan or fall back."""
        data = self.hub.entry.data
        transport = data.get('media_transport', 'tls')
        if transport == 'connect3_tcp':
            if data.get('media_tcp_approved') is not True:
                raise MediaTLSFailure('connect3_tcp_approval_required')
            port, pin = 34567, ''
        elif transport == 'tls':
            port = data.get('media_port', 8443)
            pin = data.get('media_certificate_sha256', '') or data.get('certificate_sha256', '')
        else:
            raise MediaTLSFailure('invalid_media_transport_policy')
        if type(port) is not int or not 1 <= port <= 65535:
            raise MediaTLSFailure('invalid_media_endpoint')
        return data.get('host'), port, pin, transport

    def execute(self, operation, duration=90):
        """Start returns immediately; callers await wait_closed after stop."""
        if type(operation) is not str or operation not in ('start', 'status', 'stop'):
            raise ValueError('Unsupported autonomous doorbell operation')
        if operation == 'start':
            if type(duration) is not int or not 1 <= duration <= MAX_DURATION:
                raise ValueError('Autonomous observation duration must be 1 to 90 seconds')
            if self.active:
                raise ValueError('Connect 3 diagnostic already running')
            self.start_attempts = min(self.start_attempts + 1, 2**31 - 1)
            self._data = self._empty()
            self._data.update(requested_duration_seconds=duration,
                              start_attempts=self.start_attempts)
            self._started = None
            self._closing = False
            reason = ('integration_unloaded' if self._unloaded else
                      'native_login_protocol_unestablished' if self.hub is None else
                      'device_busy' if self._busy() else None)
            if reason is not None:
                self._data.update(status='unavailable', last_stage='protocol_preflight',
                    last_error_reason=reason, close_reason='preflight_refused')
            else:
                try:
                    self.hub.check_tls_trust()
                    endpoint = self._endpoint()
                except (ValueError, RuntimeError, ConnectionError):
                    self._data.update(status='unavailable', last_stage='endpoint_policy',
                        last_error_reason='endpoint_approval_required', close_reason='preflight_refused')
                else:
                    self._data.update(status='starting', last_stage='scheduled',
                        transport=endpoint[3], port=endpoint[1],
                        legacy_tcp_login_approved=self._legacy_tcp_allowed())
                    # Reserve synchronously before any await or socket open.
                    self.task = asyncio.create_task(self._run(endpoint, duration,
                        dict(self.hub.entry.data)),
                        name='welcomeeye-connect3-alarm-diagnostic')
                    self.task.add_done_callback(self._completed)
        elif operation == 'stop':
            self._request_stop('user_stop')
        self._data['requested_operation'] = operation
        return self.diagnostics()

    def _request_stop(self, reason):
        self._data['close_reason'] = reason
        if self.active:
            self._data['status'] = 'stopping'
            if self._writer is not None:
                self._writer.close()
            if not self._closing and not self.task.cancelling():
                self.task.cancel()

    def _completed(self, task):
        if task is not self.task:
            return
        if not task.cancelled() and task.exception() is not None:
            self._data.update(status='failed', last_error_type='InternalError',
                              last_error_reason='control_probe_failed')
        if self._data['status'] == 'stopping':
            self._data.update(status='stopped', connection_closed=True)
        if self.hub is not None:
            self.hub._notify()

    async def wait_closed(self):
        if self.task is not None:
            try:
                await _finish_task(self.task, cancel_on_cancel=False)
            except asyncio.CancelledError:
                if asyncio.current_task().cancelling() or not self.task.cancelled():
                    raise
            if self._data['status'] == 'stopping':
                self._data.update(status='stopped', connection_closed=True)

    def finish(self, reason='integration_unload'):
        if reason not in ('integration_unload', 'home_assistant_stop'):
            raise ValueError('Unsupported autonomous doorbell shutdown reason')
        self._unloaded = True
        self._request_stop(reason)

    def _unchanged(self, endpoint):
        if self._unloaded or self.hub.stopped or self._endpoint() != endpoint:
            raise AlarmProtocolError('endpoint_changed_during_probe')
        self.hub.check_tls_trust()

    def _check_binding(self, endpoint, binding):
        self._unchanged(endpoint)
        if self.hub.entry.data != binding:
            raise AlarmProtocolError('configuration_changed_during_listen')

    def _legacy_tcp_allowed(self):
        data = self.hub.entry.data
        return (data.get('media_transport') == 'connect3_tcp'
                and data.get('media_tcp_approved') is True
                and data.get('legacy_alarm_tcp_approved') is True)

    async def _reconnect_legacy(self, endpoint, writer, binding):
        """Native V2 reconnects once, retaining endpoint and transport policy.

        The setup socket never sent credentials. Its replacement sends no
        second setup. Neither a failed certificate check nor a changed entry
        can fall back to TCP or proceed to CGI/login.
        """
        tls = endpoint[3] == 'tls'
        if tls and self._data['tls_verified'] is not True:
            raise AlarmProtocolError('control_tls_required')
        if not tls and not self._legacy_tcp_allowed():
            raise AlarmProtocolError('control_legacy_tcp_approval_required')
        self._check_binding(endpoint, binding)
        self._data['last_stage'] = 'legacy_setup_close'
        await _finish_task(asyncio.create_task(close_writer(writer)), cancel_on_cancel=False)
        self._writer = None
        self._check_binding(endpoint, binding)
        self._data.update(last_stage='legacy_tls_reconnect' if tls else 'legacy_tcp_reconnect', connection_attempts=2,
                          tls_verified=False)
        observation = {}
        if tls:
            reader, replacement = await open_media_tls(endpoint[0], endpoint[1], endpoint[2], observation)
        else:
            reader, replacement = await open_connect3_media_tcp(endpoint[0], endpoint[1], observation)
        self._writer = replacement
        try:
            self._data['tls_verified'] = observation.get('media_tls_verified') is True
            if tls and self._data['tls_verified'] is not True:
                raise AlarmProtocolError('control_tls_required')
            self._check_binding(endpoint, binding)
        except BaseException:
            await _finish_task(asyncio.create_task(close_writer(replacement)), cancel_on_cancel=False)
            self._writer = None
            raise
        self._data.update(control_reconnect_performed=True,
                          last_stage='legacy_tls_reconnected' if tls else 'legacy_tcp_reconnected')
        return reader, replacement

    async def _login_and_listen(self, reader, writer, endpoint, setup):
        """Use verified CGI auth for the explicitly selected control policy.

        Imported QR credentials retain their identity gate: this diagnostic
        does not perform discovery or bypass it. Manual local credentials need
        no broadcast. CGI HTTPS stays verified for every control profile.
        Unencrypted V2 login requires its own device-specific consent.
        """
        data = dict(self.hub.entry.data)
        auth = data.get('auth_code')
        if not auth:
            self._data.update(status='blocked', last_error_reason='local_auth_code_required',
                              close_reason='credentials_required')
            return
        if data.get('credential_device_uid') or data.get('credential_source') in ('apk_json', 'apk_space'):
            self._data.update(status='blocked', last_error_reason='credential_identity_check_required',
                              close_reason='identity_preflight_refused')
            return
        material = stream = read_task = None
        cgi = {}
        try:
            self._check_binding(endpoint, data)
            self._data['last_stage'] = 'cgi_stream_key'
            try:
                material = await read_stream_material(endpoint[0], auth,
                    port=data.get('cgi_port', 443),
                    certificate_sha256=data.get('certificate_sha256', ''), diagnostics=cgi)
            finally:
                self._data.update(cgi_https_verified=cgi.get('tls_verified') is True,
                    cgi_tls_policy=cgi.get('tls_policy') if cgi.get('tls_policy') in (
                        'certificate_pin', 'system_ca') else 'not_checked')
                self.hub._authentication = {'status': cgi.get('authentication_status', 'not_checked'),
                                            'operation': 'alarm_stream_key'}
            if not self._data['cgi_https_verified'] or cgi.get('authentication_status') != 'accepted':
                raise AlarmProtocolError('cgi_trust_required')
            self._data['streamkey_received'] = True
            legacy = setup.variant == wire.SetupVariant.LEGACY_V2
            legacy_tls = legacy and endpoint[3] == 'tls'
            if legacy:
                if legacy_tls:
                    stream = TLSControlStream(reader, writer, self._data,
                        tls_verified=self._data['tls_verified'])
                else:
                    stream = ExplicitTCPControlStream(reader, writer, self._data,
                        tcp_approved=data.get('media_tcp_approved'),
                        legacy_login_approved=data.get('legacy_alarm_tcp_approved'))
                cipher = None
            else:
                cipher = wire.ControlCipherMaterial(material.key)
                stream = ControlStream(reader, writer, cipher, self._data)
            self._check_binding(endpoint, data)
            self._data['last_stage'] = 'native_login_write'
            # Two native login records are the single SDK sequence, not retries.
            parameters = dict(username='adminapp2', password=encode_auth_code(auth),
                              client_type=3, customer='A0000G0000C0000')
            if legacy:
                if legacy_tls:
                    login_records = wire.build_tls_legacy_login_requests(setup,
                        tls_verified=self._data['tls_verified'], **parameters)
                    self._data.update(control_login_profile='legacy_v2_verified_tls',
                                      credential_protection='verified_tls')
                else:
                    login_records = wire.build_explicit_tcp_legacy_login_requests(setup,
                        tcp_approved=data.get('media_tcp_approved'),
                        legacy_login_approved=data.get('legacy_alarm_tcp_approved'), **parameters)
                    self._data.update(control_login_profile='legacy_v2_explicit_tcp',
                        credential_protection='reusable_hash_unencrypted_tcp',
                        legacy_tcp_login_approved=True)
            else:
                login_records = wire.build_control_login_requests(cipher, setup,
                    timestamp_seconds=int(time.time()), **parameters)
                self._data.update(control_login_profile='modern_v3_native_envelope',
                                  credential_protection='native_aes256_sha256')
            del parameters
            for packet in login_records:
                self._check_binding(endpoint, data)
                self._data['credentials_sent'] = True
                self._data['login_request_count'] += 1
                if legacy:
                    await stream.write_login(packet)
                else:
                    await stream.write_encrypted(packet)
            del login_records, packet, cipher
            self._data.update(last_stage='native_login_read', authentication_status='pending')
            async with asyncio.timeout(LOGIN_TIMEOUT):
                login = wire.parse_control_login_response(await stream.read_packet())
            if not login.accepted:
                self._data.update(status='blocked', authentication_status='rejected',
                    last_error_reason='native_login_rejected', native_login_result=login.result,
                    close_reason='authentication_rejected')
                return
            self._data.update(authentication_status='accepted', control_endpoint_confirmed=True,
                              control_peer_authenticated=self._data['tls_verified'])
            self._data['last_stage'] = 'alarm_subscription_write'
            # Fresh main-login CDvrDevice info14 remains its constructor value0.
            # No network query, inferred panel number or output command is used.
            for request in wire.build_alarm_subscription_requests(device_information_word=0):
                self._check_binding(endpoint, data)
                self._data['subscription_request_count'] += 1
                await stream.write_native(request, allowed_commands=(0x68,))
            self._data.update(status='listening', listening=True, listen_established=True,
                last_stage='alarm_observation', observation_category='no_relevant_message')
            self.hub._notify()
            listening_started = time.monotonic()
            next_heartbeat = listening_started + HEARTBEAT_INTERVAL
            try:
                while True:
                    self._check_binding(endpoint, data)
                    # Keep one reader across heartbeat deadlines; cancellation
                    # between an encrypted header and body would lose framing.
                    if read_task is None:
                        read_task = asyncio.create_task(stream.read_packet())
                    done, _ = await asyncio.wait((read_task,),
                        timeout=max(0, next_heartbeat - time.monotonic()))
                    if done:
                        packet = read_task.result()
                        read_task = None
                        self._observe(packet)
                    if time.monotonic() >= next_heartbeat:
                        self._check_binding(endpoint, data)
                        await stream.write_native(wire.build_control_keepalive_request(), allowed_commands=(0xA1,))
                        self._data['heartbeat_count'] += 1
                        next_heartbeat = time.monotonic() + HEARTBEAT_INTERVAL
            finally:
                self._data['effective_duration_seconds'] = round(time.monotonic() - listening_started, 3)
        finally:
            if read_task is not None:
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)
            if stream is not None:
                stream.clear()
            if material is not None:
                material.clear()

    def _observe(self, packet):
        header = wire.parse_native_packet_header(packet[:32])
        self._data['messages_received'] += 1
        counts = self._data['command_counts']
        key = str(header.command)
        counts[key] = counts.get(key, 0) + 1
        event = dict(command=header.command, length=len(packet), channel=None,
            relative_ms=round((time.monotonic() - self._started) * 1000),
            category='control_message', structure_valid=None)
        if header.command == 0x69:
            event['category'] = 'notification_observed'
            try:
                notification = wire.parse_alarm_notification(packet)
            except AlarmProtocolError:
                event['structure_valid'] = False
            else:
                event.update(structure_valid=True, subtype=notification.subtype,
                    callback_type=notification.callback_type, category='notification_candidate')
                self._data['notification_candidates'] += 1
                self._data['observation_category'] = 'notification_candidate'
            self._data['notifications_observed'] += 1
        if len(self._data['events']) < 64:
            self._data['events'].append(event)
        else:
            self._data['events_omitted'] += 1

    async def _run(self, endpoint, duration, binding):
        self._started = time.monotonic()
        host, port, pin, transport = endpoint
        transport_observation = {}
        writer = None
        try:
            async with asyncio.timeout(duration):
                self._check_binding(endpoint, binding)
                self._data.update(status='connecting', last_stage='tcp_connect',
                                  connection_attempts=1, connection_closed=False)
                if transport == 'connect3_tcp':
                    reader, writer = await open_connect3_media_tcp(host, port, transport_observation)
                else:
                    reader, writer = await open_media_tls(host, port, pin, transport_observation)
                self._writer = writer
                self._data.update(tcp_connected=True,
                    tls_verified=transport_observation.get('media_tls_verified', False),
                    status='negotiating', last_stage='control_setup_write')
                self._check_binding(endpoint, binding)
                # Preauthentication setup never carries credentials.
                packet = build_control_setup_request()
                if packet != b'\xa9' + bytes(8) + b'\x64' + bytes(22):
                    raise AlarmProtocolError('unexpected_setup_request')
                self._data['setup_request_count'] = 1
                async with asyncio.timeout(SETUP_TIMEOUT):
                    writer.write(packet)
                    await writer.drain()
                self._data['last_stage'] = 'control_setup_read'
                async with asyncio.timeout(SETUP_TIMEOUT):
                    response = await reader.readexactly(32)
                self._data.update(bytes_received=32, messages_received=1)
                setup = parse_control_setup_response(response)
                self._data.update(setup_response_received=True,
                    setup_accepted=setup.result == 0, protocol_version=setup.protocol_version,
                    encryption_mode=setup.encryption_mode, sha_mode=setup.sha_mode,
                    setup_variant=setup.variant.value, setup_response_selector=setup.response_selector,
                    crypto_mode_source=setup.crypto_mode_source,
                    control_reconnect_required=setup.variant == wire.SetupVariant.LEGACY_V2,
                    protected_login_capability=setup.supports_protected_login)
                self._check_binding(endpoint, binding)
                self._data['last_stage'] = 'control_setup_received'
                if setup.supports_protected_login:
                    await self._login_and_listen(reader, writer, endpoint, setup)
                elif setup.variant == wire.SetupVariant.LEGACY_V2:
                    if (transport == 'tls' and self._data['tls_verified'] is True
                            or transport == 'connect3_tcp' and self._legacy_tcp_allowed()):
                        reader, writer = await self._reconnect_legacy(endpoint, writer, binding)
                        await self._login_and_listen(reader, writer, endpoint, setup)
                    else:
                        self._data.update(status='blocked',
                            last_error_reason='native_control_legacy_tcp_approval_required',
                            close_reason='legacy_tcp_credentials_refused')
                elif setup.variant == wire.SetupVariant.LEGACY_V1:
                    self._data.update(status='blocked',
                        last_error_reason='native_control_legacy_variant_unsupported',
                        close_reason='setup_probe_complete')
                else:
                    self._data.update(status='blocked',
                        last_error_reason='native_control_setup_rejected' if setup.result
                            else 'native_control_protection_insufficient',
                        close_reason='setup_probe_complete')
        except asyncio.CancelledError:
            self._data.update(status='stopped', close_reason=self._data['close_reason'] or 'cancelled')
            raise
        except asyncio.IncompleteReadError as exc:
            stage = self._data['last_stage']
            self._data.update(status='failed',
                last_error_type='IncompleteReadError', last_error_reason=(
                    'control_setup_incomplete' if stage == 'control_setup_read' else 'native_control_incomplete'),
                close_reason='peer_closed')
            if stage == 'control_setup_read':
                self._data['bytes_received'] = len(exc.partial)
        except TimeoutError:
            if self._data['listen_established'] and time.monotonic() - self._started >= duration:
                self._data.update(status='completed', close_reason='deadline')
            else:
                self._data.update(status='failed', last_error_type='TimeoutError',
                    last_error_reason='control_setup_timeout' if self._data['last_stage'].startswith('control_setup')
                        or self._data['last_stage'] == 'tcp_connect' else
                        'native_observation_timeout' if self._data['listen_established'] else 'native_authentication_timeout',
                    close_reason='timeout')
        except (AlarmProtocolError, MediaTLSFailure, ValueError, OSError, aiohttp.ClientError) as exc:
            # No exception text, socket addresses or certificate bytes escape.
            self._data.update(status='failed', last_error_type=(
                'ProtocolError' if isinstance(exc, AlarmProtocolError) else
                'CGIError' if isinstance(exc, CGIError) else
                'TLSOrEndpointError' if isinstance(exc, MediaTLSFailure) else 'NetworkError'),
                last_error_reason=('control_setup_invalid' if self._data['last_stage'].startswith('control_setup')
                    else str(exc)) if isinstance(exc, (AlarmProtocolError, CGIError))
                    else 'control_connection_failed', close_reason='failed')
            if self.hub.report_tls_error(exc, endpoint='cgi' if self._data['last_stage'] == 'cgi_stream_key' else 'media') is not None:
                self._data['last_error_reason'] = 'tls_reapproval_required'
        except Exception:
            self._data.update(status='failed', last_error_type='InternalError',
                last_error_reason='control_probe_failed', close_reason='failed')
        finally:
            self._closing = True
            self._data['tcp_connected'] |= transport_observation.get('media_tcp_connected', False)
            try:
                if writer is not None:
                    await _finish_task(asyncio.create_task(close_writer(writer)), cancel_on_cancel=False)
            finally:
                self._writer = None
                self._data.update(connection_closed=True, listening=False, cleanup_status='closed' if writer else 'no_writer',
                    elapsed_ms=round((time.monotonic() - self._started) * 1000))

    def diagnostics(self):
        result = deepcopy(self._data)
        result['integration_unloaded'] = self._unloaded
        result['probe_active'] = self.active
        if self.active and self._started is not None:
            result['elapsed_ms'] = round((time.monotonic() - self._started) * 1000)
        return result
