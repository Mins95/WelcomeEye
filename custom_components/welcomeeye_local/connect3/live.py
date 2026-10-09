"""Reference-counted Connect 3 media; explicit viewers share one QV session."""
import asyncio
from collections import deque
from copy import deepcopy
import time

from .audio import AudioDecoder, AudioDecodeError, UnsupportedAudioFormat
from .cgi import CGIError, encode_auth_code, read_stream_material
from .certificate import failure_reason
from .discovery import discover
from .protocol import MediaProtocolError
from .session import QVSession
from .tls import MediaTLSFailure
from .video import VideoDecoder
from ..capabilities import DeviceVariant
from ..snapshot import _finish_task

ACQUIRE_TIMEOUT = 35.0


class LiveMedia:
    def __init__(self, hub, *, channel=1, video_only=False):
        self.hub = hub
        self.channel, self.video_only = channel, video_only
        self.consumers = set()
        self.task = None
        self.lock = asyncio.Lock()
        self._ready = None
        self.connected = False
        self.image = None
        self.observation = {'stage': 'idle', 'decoded_frames': 0}
        self.session_count = 0
        self.session = None
        self.previous_sessions = deque(maxlen=3)

    def talk_parameters(self):
        """Ephemeral material for the APK's separate talk socket, never diagnostics."""
        if self.video_only:
            raise RuntimeError('Channel trial is video only')
        session = self.session
        tcp = (getattr(session, '_transport', 'tls') == 'connect3_tcp'
                or (getattr(self.hub, 'variant', None) == DeviceVariant.CONNECT3
                    and self.hub.entry.data.get('media_transport', 'tls') == 'connect3_tcp'))
        if tcp and (self.hub.entry.data.get('experimental_tcp_controls') is not True
                    or getattr(self.hub.capabilities, 'talkback', False) is not True):
            raise RuntimeError('Connect 3 TCP microphone disabled')
        if (self.hub.stopped or not self.connected or session is None
                or not self.observation.get('play_accepted') or session._close_task is not None):
            raise RuntimeError('Connect 3 live media required')
        parameters = dict(host=session._host, port=session._port, pin=session._pin,
                    stream_key=session._stream_key, password=session._password,
                    transport=getattr(session, '_transport', 'tls'))
        if tcp:
            parameters['cgi_verified'] = self.observation.get('cgi_https_verified') is True
        return parameters

    async def acquire(self, owner):
        async with self.lock:
            if (self.hub.stopped or not self.hub.capabilities.live_media
                    or (self.hub._task is not None and not self.hub._task.done())):
                raise RuntimeError('Connect 3 media unavailable or busy')
            claim = getattr(self.hub, '_claim_media', None)
            if claim is not None:
                claim(self)
            self.consumers.add(owner)
            if self.task is None or self.task.done():
                self._ready = asyncio.get_running_loop().create_future()
                # A cancelled sole viewer can leave no waiter for an error.
                self._ready.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
                self.task = asyncio.create_task(self._run(), name='welcomeeye-connect3-media')
            ready = self._ready
        try:
            async with asyncio.timeout(ACQUIRE_TIMEOUT):
                # The session owns this future; a viewer's cancellation must
                # not cancel it or install a shield callback that reports its
                # later error independently of our cleanup on Python 3.14.
                await asyncio.wait({ready})
                ready.result()
        except BaseException:
            cleanup = asyncio.create_task(self.release(owner, reason='acquisition_failed'))
            await _finish_task(cleanup, cancel_on_cancel=False)
            raise

    async def release(self, owner, *, reason='viewer_closed'):
        await _finish_task(asyncio.create_task(self._release(owner, reason)), cancel_on_cancel=False)

    async def _release(self, owner, reason):
        async with self.lock:
            self.consumers.discard(owner)
            if not self.consumers:
                await self._halt(reason)
                release = getattr(self.hub, '_release_media', None)
                if release is not None:
                    release(self)

    async def _halt(self, reason):
        task = self.task
        if task is not None:
            if not task.done():
                self.observation['close_reason'] = reason
                task.cancel()
            await asyncio.gather(task, return_exceptions=True)
            if self.task is task:
                self.task = None

    async def stop(self):
        await _finish_task(asyncio.create_task(self._stop()), cancel_on_cancel=False)

    async def _stop(self):
        async with self.lock:
            self.consumers.clear()
            await self._halt('integration_stop')
            release = getattr(self.hub, '_release_media', None)
            if release is not None:
                release(self)
        self.image = None

    async def _run(self):
        if self.observation.get('stage') == 'closed':
            self.previous_sessions.append(deepcopy(self.observation))
        obs = self.observation = {'stage': 'stream_key', 'decoded_frames': 0,
            'decode_errors': 0, 'ignored_nonvideo_frames': 0, 'last_error_type': None,
            'last_error_reason': None, 'first_frame_elapsed_ms': None,
            'tcp_closed': False, 'streamkey_received': False, 'cgi_https_verified': False}
        self.connected = False
        self.session_count += 1
        start = time.monotonic()
        session = material = decoder = audio_decoder = decode_task = None
        cgi_observation = {}
        ready = self._ready
        try:
            data = self.hub.entry.data
            auth = data.get('auth_code')
            if not auth:
                raise CGIError('local_auth_code_required')
            check_tls_trust = getattr(self.hub, 'check_tls_trust', None)
            if check_tls_trust is not None:
                check_tls_trust()
            expected_uid = data.get('credential_device_uid')
            if data.get('credential_source') in ('apk_json', 'apk_space') and not expected_uid:
                raise CGIError('credential_identity_required')
            endpoint = None
            prepare_endpoint = getattr(self.hub, 'prepare_media_endpoint', None)
            if prepare_endpoint is not None:
                # R002 supplies its own freshly verified discovery policy.
                # Complete it before any credential-bearing HTTPS request.
                obs['stage'] = 'media_endpoint_identity'
                endpoint = await prepare_endpoint(obs)
            if expected_uid and (prepare_endpoint is None
                    or getattr(self.hub, 'variant', None) == DeviceVariant.CONNECT3):
                obs['stage'] = 'credential_identity'
                identity = await discover(data['host'], expected_uid=expected_uid)
                if identity['credential_identity_status'] != 'matched':
                    raise CGIError('credential_identity_not_matched')
            obs['stage'] = 'stream_key'
            try:
                material = await read_stream_material(data['host'], auth,
                    port=endpoint['cgi_port'] if endpoint is not None else data.get('cgi_port', 443),
                    certificate_sha256=data.get('certificate_sha256', ''),
                    diagnostics=cgi_observation)
            finally:
                self.hub._authentication = {'status': cgi_observation.get('authentication_status', 'not_checked'),
                                            'operation': 'media_stream_key'}
                obs['cgi_https_verified'] = cgi_observation.get('tls_verified') is True
                obs['cgi_tls_policy'] = cgi_observation.get('tls_policy') if cgi_observation.get('tls_policy') in (
                    'certificate_pin', 'system_ca') else 'not_checked'
            obs['streamkey_received'] = True
            transport = endpoint['transport'] if endpoint is not None else 'tls'
            tcp = transport == 'connect3_tcp'
            options = {'transport': transport} if endpoint is not None else {}
            if self.video_only:
                options.update(channel=self.channel, video_only=True)
            if tcp:
                options['cgi_verified'] = obs['cgi_https_verified']
                options['tcp_outputs_enabled'] = (
                    not self.video_only and data.get('experimental_tcp_controls') is True
                    and data.get('experimental_outputs') is True and bool(data.get('opening_code'))
                    and (getattr(self.hub.capabilities, 'strike', False) is True
                         or getattr(self.hub.capabilities, 'gate', False) is True))
            session = QVSession(data['host'], endpoint['port'] if endpoint is not None else data.get('media_port', 8443),
                '' if tcp else data.get('media_certificate_sha256') or data.get('certificate_sha256', ''),
                material.key, encode_auth_code(auth), obs,
                **options)
            self.session = session
            doorbell = getattr(self.hub, 'doorbell', None)
            if doorbell is not None:
                session.control_observer = lambda packet: doorbell.observe(session, packet)
            material.clear()
            material = None
            decoder = await asyncio.to_thread(VideoDecoder)
            if not self.video_only:
                audio_decoder = await asyncio.to_thread(AudioDecoder)
                obs['audio'] = audio_decoder.diagnostics
            else:
                obs.update(requested_channel=self.channel, requested_stream=1,
                    video_only=True, audio={'status': 'disabled_channel_trial',
                        'input_packets': 0, 'decoded_frames': 0}, ignored_audio_frames=0)

            async def on_frame(packet):
                nonlocal decode_task
                if getattr(packet, 'is_audio', False):
                    if self.video_only:
                        obs['ignored_audio_frames'] += 1
                        return
                    decode_task = asyncio.create_task(asyncio.to_thread(audio_decoder.feed, packet))
                    try:
                        # wait() leaves the owned worker running on cancellation.
                        # cleanup joins it before closing the decoder and is
                        # responsible for retrieving any subsequent exception.
                        await asyncio.wait({decode_task})
                        frames = decode_task.result()
                    except (UnsupportedAudioFormat, AudioDecodeError):
                        # Audio format/decode failures are isolated from video;
                        # fixed reasons and format facts remain in diagnostics.
                        decode_task = None
                        return
                    else:
                        decode_task = None
                    if frames and 'first_audio_frame_elapsed_ms' not in obs:
                        obs['first_audio_frame_elapsed_ms'] = round((time.monotonic() - start) * 1000)
                    if not self.hub.stopped and self.consumers:
                        for frame in frames:
                            for callback in tuple(self.hub.frame_listeners):
                                callback('audio', frame)
                    return
                if packet.frame_type not in (0, 1, 9, 10, 11):
                    obs['ignored_nonvideo_frames'] += 1
                    return
                decode_task = asyncio.create_task(asyncio.to_thread(decoder.feed, packet))
                await asyncio.wait({decode_task})
                frames, image = decode_task.result()
                decode_task = None
                obs['decode_errors'] = decoder.errors
                if self.hub.stopped or not self.consumers:
                    return
                if image is not None:
                    self.image = image
                for frame in frames:
                    obs['decoded_frames'] += 1
                    if not self.connected:
                        self.connected = True
                        obs['first_frame_elapsed_ms'] = round((time.monotonic() - start) * 1000)
                        if not ready.done():
                            ready.set_result(None)
                        self.hub._notify()
                    obs['stage'] = 'video_received'
                    for callback in tuple(self.hub.frame_listeners):
                        callback('video', frame)
            try:
                await session.run(on_frame)
            finally:
                if doorbell is not None:
                    doorbell.media_closed(session)
        except asyncio.CancelledError:
            obs['exit_reason'] = 'cancelled'
            raise
        except Exception as exc:
            report_tls_error = getattr(self.hub, 'report_tls_error', None)
            if report_tls_error is not None:
                report_tls_error(exc, endpoint='cgi' if material is None and session is None else 'media')
            obs['failed_at_stage'] = obs['stage']
            obs['last_error_type'] = type(exc).__name__
            if isinstance(exc, (CGIError, MediaProtocolError, MediaTLSFailure)):
                obs['last_error_reason'] = str(exc)
            elif isinstance(exc, asyncio.IncompleteReadError):
                obs['last_error_reason'] = 'remote_eof'
            elif (getattr(self.hub, 'variant', None) == DeviceVariant.CONNECT3
                    and data.get('media_transport', 'tls') == 'connect3_tcp'
                    and isinstance(exc, (OSError, TimeoutError))):
                obs['last_error_reason'] = failure_reason(exc)
            obs['exit_reason'] = 'failed'
            if not ready.done():
                ready.set_exception(RuntimeError('Connect 3 media failed; see diagnostics'))
        finally:
            async def cleanup():
                try:
                    if decode_task is not None:
                        await asyncio.gather(decode_task, return_exceptions=True)
                    talkback = getattr(self.hub, 'talkback', None)
                    if talkback is not None:
                        try:
                            await talkback.close()
                        except Exception as exc:
                            obs['talk_cleanup_error_type'] = type(exc).__name__
                    if session is not None:
                        await session.close()
                finally:
                    if material is not None:
                        material.clear()
                    if decoder is not None:
                        obs['decode_errors'] = decoder.errors
                        decoder.close()
                    if audio_decoder is not None:
                        audio_decoder.close()
                    self.session = None
                    if not ready.done():
                        ready.set_exception(RuntimeError('Connect 3 media closed'))
                    self.connected = False
                    obs['stage'] = 'closed'
                    obs['close_reason'] = obs.get('close_reason') or obs.get('last_error_reason') or obs.get('exit_reason', 'closed')
                    obs['elapsed_ms'] = round((time.monotonic() - start) * 1000)
                    self.hub._notify()
            await _finish_task(asyncio.create_task(cleanup()), cancel_on_cancel=False)
