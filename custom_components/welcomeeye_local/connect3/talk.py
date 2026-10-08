"""Explicit QV microphone connection, independent of the shared video reader.

Door Connect builds a distinct /talk/idc=65535&ap=2&tls=1 connection. Evidence
and native addresses are recorded in docs/connect3-talk-evidence.md. There is
one start attempt per request, no CGI request here and no physical command.
"""
import asyncio
from copy import deepcopy
from fractions import Fraction
import struct
import time

import av

from . import protocol as qv
from .tls import open_media_tls, open_r002_media_tcp
from ..r002.transport import close_writer
from ..snapshot import _finish_task
from ..capabilities import DeviceVariant

START_TIMEOUT = 20.0
INACTIVITY_TIMEOUT = 20.0
WRITE_TIMEOUT = 2.0
KEEPALIVE_INTERVAL = 10.0
HEARTBEAT_TIMEOUT = 3.0
TALK_CHANNEL = 65535
SAMPLE_RATE = 8000
MAX_AUDIO_PAYLOAD = 65535
CODECS = {4: 'pcm_alaw', 5: 'pcm_mulaw', 8: 'aac', 9: 'pcm_s16le'}


class TalkError(RuntimeError):
    """Fixed local reason, without endpoint, credentials or audio bytes."""


def build_setup():
    """CQUIITalk.SendSetup 0x550a20: A9 with talk selector 2 at byte 9."""
    raw = bytearray(qv.HEADER_SIZE)
    raw[0], raw[9] = 0xA9, 2
    return bytes(raw)


def build_open(material, password, *, timestamp_seconds):
    """SendOpen 0x550adc, separate talk credentials and channel 65535."""
    params = qv._cstring('adminapp2') + b'&&' + qv._cstring(password) + b'\0\0'
    if len(params) > qv.MAX_PARAMETERS:
        raise TalkError('talk_parameters_size')
    raw = bytearray(qv.HEADER_SIZE)
    raw[0] = 0x0B
    struct.pack_into('<Q', raw, 1, timestamp_seconds)
    struct.pack_into('<H', raw, 11, len(params))
    struct.pack_into('<H', raw, 13, TALK_CHANNEL)
    return qv._encode_command(raw, params, material)


def select_codec(mask):
    """OnRecvOpen 0x551410 prefers AAC bit 4, else first bit 0..5."""
    index = 4 if mask & 16 else next((i for i in range(6) if mask & (1 << i)), None)
    if index is None or index + 4 not in CODECS:
        raise TalkError('unsupported_talk_codec')
    return index, index + 4


def build_request(material, *, sending, enabled, codec_index, timestamp_seconds):
    """OnSendRequest 0x5516a0: 0C transmit, 0D receive, 8000 Hz mono."""
    raw = bytearray(qv.HEADER_SIZE)
    raw[0] = 0x0C if sending else 0x0D
    struct.pack_into('<Q', raw, 1, timestamp_seconds)
    struct.pack_into('<H', raw, 11, TALK_CHANNEL)
    raw[13] = int(enabled)
    if sending:
        raw[14] = codec_index
        struct.pack_into('<H', raw, 15, SAMPLE_RATE)
    return qv._encode_command(raw, b'', material)


def build_audio(material, payload, codec, variant, *, timestamp_seconds, milliseconds=0):
    """OnSendData 0x5521c8: A2, first 32 body bytes encrypted, flag zero.

    No media SHA, extra padding or ADTS is invented. Native format 0 is the
    eight-byte F001 header; nonzero formats use the established QV frame header.
    The native constructor fixes extension=32 and media_encrypted=0.
    """
    if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_AUDIO_PAYLOAD:
        raise TalkError('talk_audio_size')
    if codec not in CODECS:
        raise TalkError('unsupported_talk_codec')
    if variant == 0:
        wire_codec = {4: 14, 5: 10, 8: 31, 9: 12}[codec]
        body = struct.pack('<IBBH', 0xF0010000, wire_codec, 2, len(payload)) + payload
    else:
        raw = bytearray(qv.FRAME_HEADER_SIZE)
        raw[:4] = b'\0\0\1\xe3'
        struct.pack_into('<I', raw, 4, len(payload))
        # CPacket.FrameSetTime packs UTC calendar fields into a 32-bit value.
        # The outer timestamp remains unix seconds; neither value is diagnostic.
        stamp = time.gmtime(timestamp_seconds)  # CQVTime.SetTime 0x88baa0 uses gmtime_r.
        packed = (((stamp.tm_year - 2000) & 63) << 26 | stamp.tm_mon << 22
                  | stamp.tm_mday << 17 | stamp.tm_hour << 12
                  | stamp.tm_min << 6 | stamp.tm_sec)
        struct.pack_into('<IH', raw, 8, packed, milliseconds)
        raw[14], raw[15] = codec, 1
        struct.pack_into('<H', raw, 16, SAMPLE_RATE)
        body = bytes(raw) + payload
    # The APK encrypts exactly 32 body bytes, without rounding/padding. Reject
    # shorter frames instead of reproducing its possible out-of-bounds read.
    extension = 32 if material.encryption_mode else 0
    if len(body) < extension:
        raise TalkError('short_talk_audio_frame')
    raw = bytearray(qv.HEADER_SIZE)
    raw[0] = 0xA2
    struct.pack_into('<Q', raw, 1, timestamp_seconds)
    struct.pack_into('<H', raw, 9, extension)
    struct.pack_into('<I', raw, 11, len(body))
    return (qv._crypt(bytes(raw), material)
            + qv._crypt(body[:extension], material) + body[extension:])


class AudioEncoder:
    """Bounded browser PCM conversion, matching native negotiated codecs."""

    def __init__(self, codec):
        if codec not in CODECS:
            raise TalkError('unsupported_talk_codec')
        self.codec = codec
        self.context = None
        fmt = 'fltp' if codec == 8 else 's16'
        self.resampler = av.AudioResampler(format=fmt, layout='mono', rate=SAMPLE_RATE)
        self.fifo = av.AudioFifo()
        self.samples = 0
        if codec != 9:
            self.context = av.CodecContext.create(CODECS[codec], 'w')
            self.context.sample_rate = SAMPLE_RATE
            self.context.layout = 'mono'
            self.context.format = fmt
            self.context.time_base = Fraction(1, SAMPLE_RATE)
            if codec == 8:
                self.context.bit_rate = 24000
            self.context.open()
        # AAC CreateEncoder 0x57cedc uses 1024 samples; G711/PCM native capture
        # accumulates until at least 480 bytes. A 480-sample chunk meets both
        # native minima, independent of the browser's 20 ms packet cadence.
        self.chunk_samples = 1024 if codec == 8 else 480

    def feed(self, frame):
        if (not isinstance(frame, av.AudioFrame)
                or not 8000 <= frame.sample_rate <= 192000
                or not 1 <= len(frame.layout.channels) <= 2
                or not 0 < frame.samples <= frame.sample_rate // 5):
            raise TalkError('invalid_microphone_frame')
        result = []
        for converted in self.resampler.resample(frame):
            converted.pts = None
            self.fifo.write(converted)
            if self.fifo.samples > 4096:
                raise TalkError('microphone_buffer_limit')
            while self.fifo.samples >= self.chunk_samples:
                chunk = self.fifo.read(self.chunk_samples)
                chunk.pts = self.samples
                chunk.time_base = Fraction(1, SAMPLE_RATE)
                self.samples += chunk.samples
                if self.codec == 9:
                    result.append(bytes(chunk.planes[0])[:chunk.samples * 2])
                else:
                    result.extend(bytes(packet) for packet in self.context.encode(chunk))
                if len(result) > 8:
                    raise TalkError('microphone_packet_limit')
        return result


class Talkback:
    """RTC microphone interface; its sole extra socket never starts video."""

    def __init__(self, hub):
        self.hub = hub
        self.owner = None
        self.active = False
        self.last_heartbeat = 0.0
        self._requested = False
        self._live_session = None
        self._reader = self._writer = self._material = self._encoder = None
        self._read_task = None
        self._encode_task = None
        self._lock = asyncio.Lock()
        self._send_lock = asyncio.Lock()
        self._feed_lock = asyncio.Lock()
        self._close_lock = asyncio.Lock()
        self._variant = self._codec_index = self._codec = None
        self._open_attempted = False
        self._diag = self._new_diagnostics()

    @staticmethod
    def _new_diagnostics():
        return dict(state='off', session_active=False, start_attempts=0,
            setup_sent=False, setup_accepted=False, setup_result=None,
            open_sent=False, open_accepted=False, open_result=None,
            transmit_request_sent=False, receive_request_sent=False,
            transmit_accepted=False, receive_accepted=False,
            receive_disable_sent=False, receive_disable_accepted=False,
            codec_mask=None, codec=None, codec_name=None, frame_variant=None,
            sample_rate=SAMPLE_RATE, channels=1, frames_received=0,
            frames_sent=0, bytes_sent=0, send_attempts=0,
            headers_received=0, messages_received=0, bytes_received=0,
            media_packets_received=0, control_command_counts={},
            keepalives_sent=0, last_error_type=None, last_error_reason=None,
            failure_stage=None, last_receive_stage=None,
            teardown_attempted=False, teardown_sent=False,
            tcp_closed=True, cleanup_error_type=None, physically_verified=False)

    @property
    def diagnostics(self):
        return deepcopy(self._diag)

    def heartbeat(self, viewer):
        if self.owner == viewer:
            self.last_heartbeat = time.monotonic()

    def disable(self, viewer):
        if self.owner == viewer:
            self.active = self._requested = False
            self._diag['state'] = 'off'

    def _live_valid(self):
        return (not (getattr(self.hub, 'variant', None) == DeviceVariant.CONNECT3
                    and self.hub.entry.data.get('media_transport', 'tls') == 'connect3_tcp')
                and getattr(self._live_session, '_transport', 'tls') != 'connect3_tcp'
                and not self.hub.stopped and self.hub.live.connected
                and self.hub.live.session is self._live_session
                and self._live_session is not None
                and getattr(self._live_session, '_close_task', None) is None)

    def _check_start(self):
        if not self._requested or not self._live_valid():
            raise TalkError('talk_start_cancelled_or_media_lost')

    async def _send(self, data, *, audio=False):
        async with self._send_lock:
            if audio and (not self.active or not self._live_valid()):
                return False
            async with asyncio.timeout(WRITE_TIMEOUT):
                self._writer.write(data)
                await self._writer.drain()
            return True

    async def _read_exactly(self, count, stage):
        if type(count) is not int or not 0 <= count <= qv.MAX_PACKET_BODY:
            raise TalkError('talk_body_size')
        self._diag['last_receive_stage'] = stage
        try:
            raw = await self._reader.readexactly(count)
        except asyncio.IncompleteReadError as error:
            self._diag['bytes_received'] += len(error.partial)
            raise
        self._diag['bytes_received'] += len(raw)
        return raw

    async def _packet(self):
        raw = await self._read_exactly(qv.HEADER_SIZE, 'talk_header')
        self._diag['headers_received'] += 1
        header = qv.decode_packet_header(raw, self._material)
        body = await self._read_exactly(header.body_length, 'talk_body')
        packet = qv.decode_packet(header, body, self._material)
        self._diag['messages_received'] += 1
        if header.is_media:
            self._diag['media_packets_received'] += 1
        else:
            key = str(header.command)
            counts = self._diag['control_command_counts']
            counts[key] = counts.get(key, 0) + 1
        return packet

    def _error(self, error):
        diag = self._diag
        diag.update(last_error_type=type(error).__name__, failure_stage=diag['state'])
        # Only fixed errors authored by this module/protocol are safe. Never
        # export exception messages from SSL, socket, PyAV or arbitrary callers.
        if isinstance(error, TalkError):
            diag['last_error_reason'] = str(error)
        elif isinstance(error, qv.MediaProtocolError):
            diag['last_error_reason'] = 'talk_protocol_rejected'

    async def start(self, viewer):
        async with self._lock:
            if (getattr(self.hub, 'variant', None) == DeviceVariant.CONNECT3
                    and self.hub.entry.data.get('media_transport', 'tls') == 'connect3_tcp'):
                raise TalkError('connect3_tcp_microphone_disabled')
            if self.owner is not None:
                if self.owner == viewer and self.active:
                    self.heartbeat(viewer)
                    return
                raise TalkError('microphone_owned_or_starting')
            if self.hub.stopped or not self.hub.live.connected:
                raise TalkError('live_media_required')
            check_tls_trust = getattr(self.hub, 'check_tls_trust', None)
            if check_tls_trust is not None:
                check_tls_trust()
            # Material originates from the existing accepted live session. No
            # discovery, second stream key request or second video acquisition.
            params = self.hub.live.talk_parameters()
            self._live_session = self.hub.live.session
            self._diag = self._new_diagnostics()
            self._diag.update(state='starting', start_attempts=1, tcp_closed=False)
            self.owner, self._requested = viewer, True
            try:
                async with asyncio.timeout(START_TIMEOUT):
                    transport = params.get('transport', 'tls')
                    if transport == 'r002_tcp':
                        self._reader, self._writer = await open_r002_media_tcp(
                            params['host'], params['port'], self._diag)
                    elif transport == 'tls':
                        self._reader, self._writer = await open_media_tls(
                            params['host'], params['port'], params['pin'], self._diag)
                    else:
                        raise TalkError('invalid_media_transport_policy')
                    self._diag.update(state='setup', session_active=True)
                    self._check_start()
                    await self._send(build_setup())
                    self._diag['setup_sent'] = True
                    raw = await self._read_exactly(qv.HEADER_SIZE, 'talk_setup')
                    self._diag['headers_received'] += 1
                    self._diag['messages_received'] += 1
                    setup = qv.parse_setup_response(raw)
                    self._diag.update(setup_result=setup.result,
                        encryption_mode=setup.encryption_mode, sha_mode=setup.sha_mode)
                    if setup.result:
                        raise TalkError('talk_setup_rejected')
                    self._material = qv.CipherMaterial(params['stream_key'],
                        setup.encryption_mode, setup.sha_mode)
                    self._diag.update(setup_accepted=True, state='opening')
                    self._check_start()
                    self._open_attempted = True
                    await self._send(build_open(self._material, params['password'],
                        timestamp_seconds=int(time.time())))
                    self._diag['open_sent'] = True
                    # Authentication material no longer needed after one open.
                    params.clear()
                    packet = await self._packet()
                    if not isinstance(packet, qv.ControlPacket) or packet.header.command != 0x0B:
                        raise TalkError('unexpected_talk_open_response')
                    self._diag['open_result'] = packet.header.result
                    if packet.header.result:
                        raise TalkError('talk_open_rejected')
                    header = packet.header.plaintext
                    mask = struct.unpack_from('<H', header, 12)[0]
                    self._codec_index, self._codec = select_codec(mask)
                    self._variant = header[14]
                    self._diag.update(open_accepted=True, state='negotiating',
                        codec_mask=mask, codec=self._codec, codec_name=CODECS[self._codec],
                        frame_variant=self._variant)
                    del packet, header
                    self._check_start()
                    for sending in (True, False):
                        await self._send(build_request(self._material, sending=sending,
                            enabled=True, codec_index=self._codec_index,
                            timestamp_seconds=int(time.time())))
                        self._diag['transmit_request_sent' if sending else 'receive_request_sent'] = True
                    # Native 0D ack marks active; also require 0C success to
                    # avoid transmitting after an unobserved send rejection.
                    while not (self._diag['transmit_accepted'] and self._diag['receive_accepted']):
                        packet = await self._packet()
                        if not isinstance(packet, qv.ControlPacket):
                            raise TalkError('talk_media_before_acceptance')
                        command = packet.header.command
                        if command in (0x0C, 0x0D):
                            if packet.header.result:
                                raise TalkError('talk_request_rejected')
                            self._diag['transmit_accepted' if command == 0x0C else 'receive_accepted'] = True
                        elif command != 0:
                            raise TalkError('unexpected_talk_request_response')
                        self._check_start()
                    # buildTalkConnection() uses mIsTalkListen=false;
                    # AudioPlayerManager.d() calls requestAudio(url,false)
                    # when microphone capture starts. This affects only the
                    # dedicated talk socket, never the video audio receiver.
                    await self._send(build_request(self._material, sending=False,
                        enabled=False, codec_index=self._codec_index,
                        timestamp_seconds=int(time.time())))
                    self._diag['receive_disable_sent'] = True
                    encoder_task = asyncio.create_task(asyncio.to_thread(AudioEncoder, self._codec))
                    self._encoder = await _finish_task(encoder_task, cancel_on_cancel=False)
                    self._check_start()
                    self.active = True
                    self.heartbeat(viewer)
                    self._diag['state'] = 'active'
                    self._read_task = asyncio.create_task(self._monitor(), name='welcomeeye-qv-talk-reader')
            except BaseException as error:
                report_tls_error = getattr(self.hub, 'report_tls_error', None)
                if report_tls_error is not None:
                    report_tls_error(error, endpoint='media')
                self._error(error)
                self.active = self._requested = False
                try:
                    await _finish_task(asyncio.create_task(self._close_connection()), cancel_on_cancel=False)
                finally:
                    self.owner = None
                raise
            finally:
                params.clear()

    async def _monitor(self):
        read_task = None
        try:
            loop = asyncio.get_running_loop()
            last_data = loop.time()
            next_keepalive = last_data + KEEPALIVE_INTERVAL
            read_task = asyncio.create_task(self._packet(), name='welcomeeye-qv-talk-packet')
            while self._requested and self._live_valid():
                now = loop.time()
                remaining = INACTIVITY_TIMEOUT - (now - last_data)
                if remaining <= 0:
                    raise TimeoutError('talk_inactivity_timeout')
                done, _ = await asyncio.wait({read_task},
                    timeout=max(0, min(remaining, next_keepalive - now)))
                if done:
                    packet = read_task.result()
                    read_task = None
                    last_data = loop.time()
                    if isinstance(packet, qv.ControlPacket):
                        if packet.header.command in (7, 8):
                            raise TalkError('talk_remote_teardown')
                        if packet.header.command in (0x0C, 0x0D) and packet.header.result:
                            raise TalkError('talk_request_rejected')
                        if packet.header.command == 0x0D:
                            self._diag['receive_disable_accepted'] = True
                    read_task = asyncio.create_task(self._packet(), name='welcomeeye-qv-talk-packet')
                if loop.time() >= next_keepalive:
                    await self._send(qv.build_keepalive(self._material, timestamp_seconds=int(time.time())))
                    self._diag['keepalives_sent'] += 1
                    next_keepalive = loop.time() + KEEPALIVE_INTERVAL
        except asyncio.CancelledError:
            raise
        except (OSError, TimeoutError, ValueError, asyncio.IncompleteReadError, TalkError) as error:
            self._error(error)
        finally:
            self.active = self._requested = False
            if read_task is not None:
                read_task.cancel()
                await asyncio.gather(read_task, return_exceptions=True)
            try:
                await self._close_connection()
            finally:
                self.owner = None

    async def feed(self, viewer, frame):
        async with self._feed_lock:
            if self.owner != viewer or not self.active:
                return
            if not self._live_valid() or time.monotonic() - self.last_heartbeat > HEARTBEAT_TIMEOUT:
                self._error(TalkError('microphone_heartbeat_or_media_lost'))
                self.disable(viewer)
                await self.stop(viewer)
                return
            self._diag['frames_received'] += 1
            try:
                self._encode_task = asyncio.create_task(asyncio.to_thread(self._encoder.feed, frame))
                try:
                    payloads = await _finish_task(self._encode_task, cancel_on_cancel=False)
                finally:
                    if self._encode_task.done():
                        self._encode_task = None
                for payload in payloads:
                    if not self.active or not self._live_valid():
                        return
                    now = time.time()
                    packet = build_audio(self._material, payload, self._codec, self._variant,
                        timestamp_seconds=int(now), milliseconds=int(now % 1 * 1000))
                    self._diag['send_attempts'] += 1
                    if await self._send(packet, audio=True):
                        self._diag['frames_sent'] += 1
                        self._diag['bytes_sent'] += len(payload)
            except asyncio.CancelledError:
                self.disable(viewer)
                await _finish_task(asyncio.create_task(self.stop(viewer)), cancel_on_cancel=False)
                raise
            except Exception as error:
                self._error(error)
                self.disable(viewer)
                # Keep the RTC inbound consumer alive. A later explicit enable
                # may start a new talk attempt on the same video session.
                await self.stop(viewer)

    async def stop(self, viewer):
        self.disable(viewer)
        await _finish_task(asyncio.create_task(self._stop(viewer)), cancel_on_cancel=False)

    async def _stop(self, viewer):
        async with self._lock:
            if self.owner != viewer:
                return
            if self._encode_task is not None:
                # Off already gates sending. Settle the CPU operation before
                # dropping codec state; do not flush buffered AAC on teardown.
                await asyncio.gather(self._encode_task, return_exceptions=True)
            if self._read_task is not None:
                self._read_task.cancel()
                await self._join_reader()
                self._read_task = None
            try:
                await self._close_connection()
            finally:
                self.owner = None

    async def _join_reader(self):
        await asyncio.gather(self._read_task, return_exceptions=True)

    async def close(self):
        await self.stop(self.owner)

    async def _close_connection(self):
        async with self._close_lock:
            if self._encode_task is not None:
                await asyncio.gather(self._encode_task, return_exceptions=True)
            try:
                if (self._writer is not None and not self._writer.is_closing()
                        and self._open_attempted and self._material is not None):
                    self._diag['teardown_attempted'] = True
                    try:
                        await self._send(qv.build_teardown(self._material,
                            timestamp_seconds=int(time.time())))
                        self._diag['teardown_sent'] = True
                    except (OSError, TimeoutError, ValueError, qv.MediaProtocolError):
                        self._diag['cleanup_error_type'] = 'teardown_failed'
            finally:
                try:
                    if self._writer is not None:
                        try:
                            await close_writer(self._writer)
                        except Exception as error:
                            # A failed wait_closed must not retain ownership or
                            # poison the video teardown. No exception text.
                            self._diag['cleanup_error_type'] = type(error).__name__
                finally:
                    self._reader = self._writer = self._material = self._encoder = None
                    self._live_session = None
                    self._open_attempted = False
                    self._diag.update(state='off', session_active=False, tcp_closed=True)
