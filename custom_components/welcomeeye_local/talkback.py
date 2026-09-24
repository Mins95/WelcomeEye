"""LT microphone path recovered from the WelcomeEye APK; one media session."""
import asyncio
from dataclasses import dataclass
import struct
import time

import av

from .protected import ProtocolError, build_private_query, decode_private_reply, owsp, parse_tlvs, tlv

TALK_REQUEST = 331
TALK_RESPONSE = 332
TALK_TIMEOUT = 4.0


async def _session_job(callback, *args):
    """Finish a socket write before cancellation can start its paired cleanup.

    Cancelling to_thread does not stop its worker. In particular, stop-talk
    must never overtake a cancelled start-talk waiting for the write lock.
    """
    task = asyncio.create_task(asyncio.to_thread(callback, *args))
    cancelled = False
    while not task.done():
        try:
            await asyncio.shield(task)
        except asyncio.CancelledError:
            cancelled = True
        except Exception:
            break
    if cancelled:
        if not task.cancelled():
            task.exception()
        raise asyncio.CancelledError
    return task.result()


class UnsupportedTalkFormat(ProtocolError):
    """An actual TLV 332 advertises an unsupported microphone format."""


class TalkRefused(ProtocolError):
    """The device explicitly rejected the talk request in TLV 332."""


def talk_request(session, enabled):
    # sendTalkCmd: eight zeroed bytes, command 1/2 at offset four.
    command = bytes(4) + bytes((1 if enabled else 2, 0, 0, 0))
    # Protected DataChannel::sendData2 wraps the complete inner OWSP in 509.
    return build_private_query(session.info.uid, session.encryption_profile,
                               session.device_now(), owsp(tlv(TALK_REQUEST, command)))


@dataclass(frozen=True)
class TalkFormat:
    codec: str
    sample_rate: int
    channels: int
    bits: int

    @classmethod
    def parse(cls, body):
        # DataChannelIOCtrl::onParse (332): result+0, rate+4, codec+12,
        # channels+14, bits+18, all little endian. No inferred format fallback.
        if len(body) < 20:
            raise ProtocolError('Truncated talk response')
        result = struct.unpack_from('<H', body)[0]
        if result != 1:
            raise TalkRefused('Microphone refused by device')
        rate = struct.unpack_from('<I', body, 4)[0]
        codec, channels = struct.unpack_from('<HH', body, 12)
        bits = struct.unpack_from('<H', body, 18)[0]
        codecs = {31257: 'pcm_alaw', 31269: 'pcm_mulaw'}
        if codec not in codecs or rate != 8000 or channels != 1 or bits != 16:
            raise UnsupportedTalkFormat('Unsupported microphone format')
        return cls(codecs[codec], rate, channels, bits)


def audio_request(channel, encoded):
    if not 0 <= channel < 64 or not 0 < len(encoded) <= 1280:
        raise ValueError('Invalid microphone frame')
    # getChannelNO()+1, three reserved bytes, caller timestamp zero (APK).
    return owsp(tlv(97, struct.pack('<B3xI', channel + 1, 0)) + tlv(98, encoded))


class Talkback:
    def __init__(self, hub):
        self.hub = hub
        self.lock = asyncio.Lock()
        self.owner = self.session = self.reply = None
        self.encoder = self.resampler = None
        self.active = False
        self.last_heartbeat = 0.0
        self.last_stop = 0.0
        self.diagnostics = {'state': 'off', 'frames_sent': 0, 'bytes_sent': 0,
                            'last_error_type': None, 'last_error_stage': None,
                            'cleanup_error_type': None, 'response_received': False,
                            'response_format': None,
                            'physically_verified': False}

    def heartbeat(self, owner):
        if self.owner is owner:
            self.last_heartbeat = time.monotonic()

    def disable(self, owner):
        """Close the audio gate immediately, even while start awaits its reply."""
        if self.owner is not owner:
            return
        self.active = False
        if self.session:
            self.session.talk_enabled = False
        if self.reply and not self.reply.done():
            self.reply.set_exception(ConnectionAbortedError('Microphone cancelled'))

    async def start(self, owner):
        async with self.lock:
            if self.owner is not None:
                if self.owner is owner and self.active:
                    return
                raise ProtocolError('Microphone already in use')
            session = self.hub.session
            if not self.hub.connected or session is None or session.closed.is_set():
                raise ProtocolError('Open video before microphone')
            if time.monotonic() - self.last_stop < .8:
                raise ProtocolError('Please wait before enabling microphone again')
            self.owner, self.session = owner, session
            self.reply = asyncio.get_running_loop().create_future()
            self.diagnostics.update(state='starting', last_error_type=None, last_error_stage=None,
                                    cleanup_error_type=None, response_received=False, response_format=None)
            stage = 'sending_start'
            try:
                await _session_job(session.start_talk)
                stage = 'waiting_tlv_332'
                fmt = await asyncio.wait_for(asyncio.shield(self.reply), TALK_TIMEOUT)
                stage = 'initializing_encoder'
                if self.session is not session or session.closed.is_set():
                    raise ConnectionError('Media session ended')
                self.resampler = av.AudioResampler(format='s16', layout='mono', rate=8000, frame_size=320)
                self.encoder = av.CodecContext.create(fmt.codec, 'w')
                self.encoder.sample_rate, self.encoder.layout, self.encoder.format = 8000, 'mono', 's16'
                self.encoder.open()
                session.talk_enabled = self.active = True
                self.heartbeat(owner)
                self.diagnostics.update(state='on', codec=fmt.codec, sample_rate=8000, channels=1)
            except BaseException as exc:
                self.diagnostics.update(state='failed', last_error_type=type(exc).__name__, last_error_stage=stage)
                await self._stop()
                raise

    def observe(self, session, parts):
        """Called by the sole media reader; never read the socket here."""
        if session is not self.session or self.active or self.reply is None:
            return
        try:
            responses = list(parts)
            for kind, body in parts:
                if kind == 510:
                    _, data = decode_private_reply(session.info.uid, body)
                    if len(data) >= 8 and int.from_bytes(data[:4], 'big') == len(data) - 4:
                        responses.extend(parse_tlvs(data[8:]))
            for kind, body in responses:
                if kind == TALK_RESPONSE:
                    self.diagnostics['response_received'] = True
                    if len(body) >= 20:
                        self.diagnostics['response_format'] = {
                            'result': struct.unpack_from('<H', body)[0],
                            'sample_rate': struct.unpack_from('<I', body, 4)[0],
                            'codec_id': struct.unpack_from('<H', body, 12)[0],
                            'channels': struct.unpack_from('<H', body, 14)[0],
                            'bits': struct.unpack_from('<H', body, 18)[0],
                        }
                    self.hub.loop.call_soon_threadsafe(self._reply, session, TalkFormat.parse(body), None)
                    return
        except (ProtocolError, ValueError) as exc:
            self.hub.loop.call_soon_threadsafe(self._reply, session, None, exc)

    def _reply(self, session, value, error):
        if session is self.session and self.reply and not self.reply.done():
            if error:
                self.reply.set_exception(error)
            else:
                self.reply.set_result(value)

    async def feed(self, owner, frame):
        if self.owner is not owner or not self.active:
            return
        if time.monotonic() - self.last_heartbeat > 3:
            await self.stop(owner)
            return
        session = self.session
        if session is not self.hub.session or session.closed.is_set():
            await self.stop(owner)
            return
        resampler, encoder = self.resampler, self.encoder
        for pcm in resampler.resample(frame):
            if not self.active or self.session is not session or self.owner is not owner:
                return
            for packet in encoder.encode(pcm):
                sent = await _session_job(session.send_talk_audio, bytes(packet))
                if sent:
                    self.diagnostics['frames_sent'] += 1
                    self.diagnostics['bytes_sent'] += packet.size

    async def stop(self, owner):
        self.disable(owner)
        async with self.lock:
            if self.owner is owner:
                await self._stop()

    async def _stop(self):
        session = self.session
        self.active = False
        try:
            if session:
                session.talk_enabled = False
                try:
                    await _session_job(session.stop_talk)
                except Exception as exc:
                    self.diagnostics['cleanup_error_type'] = type(exc).__name__
        finally:
            if self.reply:
                if not self.reply.done():
                    self.reply.cancel()
                elif not self.reply.cancelled():
                    self.reply.exception()
            self.owner = self.session = self.reply = None
            self.encoder = self.resampler = None
            self.last_stop = time.monotonic()
            self.diagnostics['state'] = 'off'

    def media_closed(self, session):
        # Worker teardown sends stop-talk before Stop AV/session-stop.
        if session is self.session:
            try:
                session.stop_talk()
            finally:
                self.hub.loop.call_soon_threadsafe(self._reply, session, None, ConnectionError('Media session ended'))
                self.hub.loop.call_soon_threadsafe(self._ended, session)

    def _ended(self, session):
        if self.session is session:
            self.active = False
            self.diagnostics['state'] = 'off'
