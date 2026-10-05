"""One shared QV live session/reader; explicit outputs are never replayed."""
import asyncio
import time

from . import protocol as qv
from .control import OutputFailure, UNLOCK_TIMEOUT, build_unlock_request, parse_unlock_response
from .tls import open_media_tls
from ..r002.transport import close_writer
from ..snapshot import _finish_task

START_TIMEOUT = 20.0
INACTIVITY_TIMEOUT = 20.0
KEEPALIVE_INTERVAL = 10.0  # CQUIIStreamBase ctor/OnRecvPlay, native seconds.
WRITE_TIMEOUT = 2.0
MEDIA_HEADER_FIELDS = frozenset(('command', 'body_length', 'extension_length',
    'media_offset', 'media_encrypted', 'extension_within_body',
    'offset_within_body', 'offset_before_extension'))
MEDIA_HEADER_REASONS = frozenset(('unaligned_extension', 'media_size',
    'media_extension_size', 'media_offset'))
MEDIA_BODY_REASONS = frozenset(('body_size', 'invalid_cipher_input',
    'unaligned_cipher_input', 'incomplete_media_body',
    'media_before_play_acceptance'))


def _safe_media_header(metadata):
    """Only scalar layout facts and fixed local validation reasons may escape."""
    safe = {key: value for key, value in metadata.items()
            if key in MEDIA_HEADER_FIELDS and type(value) in (int, bool)}
    safe['header_validation_errors'] = [reason for reason in
        metadata.get('header_validation_errors', ()) if reason in MEDIA_HEADER_REASONS]
    return safe


class QVSession:
    def __init__(self, host, port, pin, stream_key, password, observation):
        self._host, self._port, self._pin = host, port, pin
        self._stream_key, self._password = stream_key, password
        self.observation = observation
        self._reader = self._writer = self._read_task = self._material = None
        self._play_attempted = False
        self._close_task = None
        self.control_observer = None
        self._write_lock = asyncio.Lock()
        self._output_future = None
        self._output_attempted = self._output_confirmed = self._output_uncertain = False
        self._output_observation = dict(request_send_attempt_count=0, request_sent_count=0,
            response_count=0, physical_request_uncertain=False, session_blocked=False,
            last_result=None, last_error_type=None, stage='idle')

    async def _send(self, data, *, physical=False):
        async with asyncio.timeout(WRITE_TIMEOUT):
            async with self._write_lock:
                if self._writer is None or self._writer.is_closing():
                    raise OutputFailure('output_session_closed')
                if physical:
                    # A close may have begun while this coroutine waited for
                    # the shared write lock. Never write after that boundary.
                    if (self._close_task is not None or self._output_future is None
                            or self._output_future.done()):
                        raise OutputFailure('output_session_closed')
                    if self._output_attempted:
                        raise OutputFailure('output_already_attempted', True)
                    # Mark BEFORE write: even an exception/partial write may
                    # have reached the device. No subsequent send is allowed.
                    self._output_attempted = True
                    self._output_observation['request_send_attempt_count'] += 1
                self._writer.write(data)
                await self._writer.drain()
                if physical:
                    self._output_observation['request_sent_count'] += 1
        self.observation['messages_sent'] += 1

    def output_diagnostics(self):
        return dict(self._output_observation)

    def _output_failed(self, reason):
        if self._output_attempted:
            self._output_uncertain = True
        self._output_observation.update(physical_request_uncertain=self._output_uncertain,
            session_blocked=self._output_uncertain, last_error_type=reason, stage='failed')
        return OutputFailure(reason, self._output_uncertain)

    async def execute_output(self, output, opening_code, *, channel=1):
        """One explicit action, one write attempt, ACK on the existing reader.

        Order 4 has no request ID in its reply. An unconfirmed physical attempt
        permanently blocks output on this session; a late ACK cannot satisfy a
        subsequent action. This method never reconnects or reads a socket.
        """
        if self._output_uncertain:
            raise OutputFailure('output_session_uncertain', True)
        if self._output_future is not None:
            raise OutputFailure('output_busy')
        if (self._close_task is not None or self._writer is None
                or self._material is None or not self.observation.get('play_accepted')):
            raise OutputFailure('output_session_unavailable')
        packet = build_unlock_request(self._material, channel=channel, output=output,
            opening_code=opening_code, timestamp_seconds=int(time.time()))
        future = asyncio.get_running_loop().create_future()
        future.add_done_callback(lambda f: f.exception() if not f.cancelled() else None)
        self._output_future = future
        self._output_attempted = self._output_confirmed = False
        self._output_observation.update(stage='sending', last_result=None, last_error_type=None)
        try:
            await self._send(packet, physical=True)
            del packet
            self._output_observation['stage'] = 'waiting_confirmation'
            async with asyncio.timeout(UNLOCK_TIMEOUT):
                response = await asyncio.shield(future)
            self._output_observation['stage'] = 'confirmed' if response.accepted else 'rejected'
            return response
        except asyncio.CancelledError as exc:
            failure = self._output_failed('output_cancelled')
            exc.physical_request_uncertain = failure.physical_request_uncertain
            raise
        except OutputFailure as exc:
            raise self._output_failed(str(exc)) from None
        except TimeoutError:
            reason = ('output_confirmation_timeout' if self._output_observation['stage']
                      == 'waiting_confirmation' else 'output_send_timeout')
            raise self._output_failed(reason) from None
        except Exception:
            raise self._output_failed('output_send_failed') from None
        finally:
            if self._output_future is future:
                self._output_future = None
            if not future.done():
                future.cancel()

    def _observe_output_response(self, packet):
        future = self._output_future
        # Unsolicited/early/late replies are not saved for a future command.
        if future is None or future.done() or not self._output_attempted:
            return
        try:
            response = parse_unlock_response(packet)
        except OutputFailure:
            future.set_exception(self._output_failed('invalid_output_response'))
            return
        if response is not None:
            self._output_confirmed = True
            self._output_observation['response_count'] += 1
            self._output_observation['last_result'] = response.result
            future.set_result(response)

    async def _read_exactly(self, length, stage):
        """Count consumed bytes, including EOF partials, without a second reader."""
        if type(length) is not int or not 0 <= length <= qv.MAX_PACKET_BODY:
            raise qv.MediaProtocolError('body_size')
        self.observation['last_receive_stage'] = stage
        try:
            data = await self._reader.readexactly(length)
        except asyncio.IncompleteReadError as error:
            self.observation['bytes_received'] += len(error.partial)
            raise
        self.observation['bytes_received'] += len(data)
        return data

    def _reject_media(self, metadata, reason, *, header=False):
        obs = self.observation
        obs['media_packets_rejected'] += 1
        if header:
            obs['media_headers_rejected'] += 1
        rejected = _safe_media_header(metadata)
        rejected['rejection_reason'] = (reason if reason in
            MEDIA_HEADER_REASONS | MEDIA_BODY_REASONS else 'media_packet_invalid')
        obs['last_rejected_media_header'] = rejected

    async def _read_packet(self):
        obs = self.observation
        raw = await self._read_exactly(qv.HEADER_SIZE, 'packet_header_read')
        obs['headers_received'] += 1
        obs['last_receive_stage'] = 'packet_header_decode'
        metadata = {}
        try:
            header = qv.decode_packet_header(raw, self._material, diagnostics=metadata)
        except qv.MediaProtocolError as error:
            if metadata.get('command') in qv.MEDIA_COMMANDS:
                obs['media_headers_received'] += 1
                obs['last_media_header'] = _safe_media_header(metadata)
                obs['last_receive_stage'] = 'media_header_rejected'
                self._reject_media(metadata, str(error), header=True)
            raise
        if header.is_media:
            obs['media_headers_received'] += 1
            obs['last_media_header'] = _safe_media_header(metadata)
        try:
            body = await self._read_exactly(header.body_length,
                'media_body_read' if header.is_media else 'control_body_read')
        except asyncio.IncompleteReadError:
            if header.is_media:
                self._reject_media(metadata, 'incomplete_media_body')
            raise
        obs['messages_received'] += 1
        obs['last_receive_stage'] = 'media_body_decode' if header.is_media else 'control_body_decode'
        try:
            return qv.decode_packet(header, body, self._material)
        except qv.MediaProtocolError as error:
            if header.is_media:
                obs['last_receive_stage'] = 'media_body_rejected'
                self._reject_media(metadata, str(error))
            raise

    async def run(self, on_frame):
        obs = self.observation
        obs.update(messages_sent=0, messages_received=0, bytes_received=0,
                   headers_received=0, media_headers_received=0,
                   media_headers_rejected=0, media_packets_rejected=0,
                   media_packets_accepted=0, last_receive_stage='not_started',
                   last_media_header=None, last_rejected_media_header=None,
                   setup_sent=False, setup_accepted=False, play_sent=False,
                   play_accepted=False, keepalives_sent=0, media_packets=0,
                   control_command_counts={}, control_parameter_bytes=0,
                   frame_type_counts={}, frame_codec_counts={},
                   frame_formats=[], frame_formats_overflow=0,
                   teardown_attempted=False, teardown_sent=False, tcp_closed=False)
        obs['output'] = self._output_observation
        assembler = qv.FrameAssembler()
        try:
            async with asyncio.timeout(START_TIMEOUT):
                self._reader, self._writer = await open_media_tls(
                    self._host, self._port, self._pin, obs)
                obs['stage'] = 'media_setup'
                await self._send(qv.build_setup_request())
                obs['setup_sent'] = True
                raw_setup = await self._read_exactly(qv.HEADER_SIZE, 'setup_header_read')
                obs['headers_received'] += 1
                obs['messages_received'] += 1
                obs['last_receive_stage'] = 'setup_header_decode'
                setup = qv.parse_setup_response(raw_setup)
                obs.update(setup_result=setup.result, encryption_mode=setup.encryption_mode,
                           sha_mode=setup.sha_mode)
                if setup.result != 0:
                    raise qv.MediaProtocolError('media_setup_rejected')
                obs['setup_accepted'] = True
                self._material = qv.CipherMaterial(self._stream_key, setup.encryption_mode, setup.sha_mode)
                obs['stage'] = 'media_play'
                packet = qv.build_play_request(self._material, username='adminapp2',
                    password=self._password, channel=1, stream=1,
                    timestamp_seconds=int(time.time()))
                self._play_attempted = True
                await self._send(packet)
                obs['play_sent'] = True
                del packet
            loop = asyncio.get_running_loop()
            last_data = loop.time()
            next_keepalive = last_data + KEEPALIVE_INTERVAL
            self._read_task = asyncio.create_task(self._read_packet(), name='welcomeeye-qv-reader')
            while True:
                now = loop.time()
                remaining = INACTIVITY_TIMEOUT - (now - last_data)
                if remaining <= 0:
                    raise TimeoutError('media_inactivity_timeout')
                read_task = self._read_task
                done, _ = await asyncio.wait({read_task},
                    timeout=max(0, min(remaining, next_keepalive - now)))
                if done:
                    packet = read_task.result()
                    if self._read_task is read_task:
                        self._read_task = None
                    last_data = loop.time()
                    if isinstance(packet, qv.ControlPacket):
                        # Count already received commands only. Unknown command
                        # parameters may contain alarms/identifiers; never export
                        # them or infer a ring from their mere presence.
                        key = str(packet.header.command)
                        counts = obs['control_command_counts']
                        counts[key] = counts.get(key, 0) + 1
                        obs['control_parameter_bytes'] += len(packet.parameters)
                        self._observe_output_response(packet)
                        if self.control_observer is not None:
                            try:
                                self.control_observer(packet)
                            except Exception:
                                # Diagnostic observation must not interrupt the
                                # sole reader or alter output acknowledgement.
                                obs['control_observer_errors'] = obs.get('control_observer_errors', 0) + 1
                        if packet.header.command == 1:
                            obs['play_result'] = packet.header.result
                            if packet.header.result != 0:
                                raise qv.MediaProtocolError('media_play_rejected')
                            if packet.header.action != 1:
                                raise qv.MediaProtocolError('unexpected_play_action')
                            obs.update(play_accepted=True, stage='waiting_video')
                        elif packet.header.command in (7, 8):
                            raise qv.MediaProtocolError('remote_teardown')
                    else:
                        if not obs['play_accepted']:
                            self._reject_media(obs['last_media_header'], 'media_before_play_acceptance')
                            raise qv.MediaProtocolError('media_before_play_acceptance')
                        obs['media_packets'] += 1
                        obs['media_packets_accepted'] += 1
                        obs['last_receive_stage'] = 'media_packet_accepted'
                        obs['stage'] = 'receiving_media'
                        for frame in assembler.feed(packet.data):
                            self._observe_frame(frame)
                            await on_frame(frame)
                    self._read_task = asyncio.create_task(self._read_packet(), name='welcomeeye-qv-reader')
                if loop.time() >= next_keepalive:
                    await self._send(qv.build_keepalive(self._material, timestamp_seconds=int(time.time())))
                    obs['keepalives_sent'] += 1
                    next_keepalive = loop.time() + KEEPALIVE_INTERVAL
        finally:
            assembler.reset()
            await self.close()

    def _observe_frame(self, frame):
        """Bounded format facts from the existing sole media reader, no payload."""
        obs = self.observation
        for field, value in (('frame_type_counts', frame.frame_type),
                             ('frame_codec_counts', frame.codec)):
            key = str(value)
            counts = obs[field]
            counts[key] = counts.get(key, 0) + 1
        # At most 12 frame types / 256 codecs can emerge from FrameAssembler.
        # Only established type/codec and size are exposed for non-video frames;
        # bytes 15..19 have media-specific meanings, not generic dimensions.
        sample = {'frame_type': frame.frame_type, 'codec': frame.codec,
                  'payload_bytes': len(frame.payload)}
        formats = obs['frame_formats']
        if sample not in formats:
            if len(formats) < 32:
                formats.append(sample)
            else:
                obs['frame_formats_overflow'] += 1

    async def close(self):
        if self._close_task is None:
            self._close_task = asyncio.create_task(self._close(), name='welcomeeye-qv-close')
        await _finish_task(self._close_task, cancel_on_cancel=False)

    async def _close(self):
        obs = self.observation
        if self._output_future is not None and not self._output_future.done():
            self._output_future.set_exception(self._output_failed('output_session_closed'))
        if self._read_task is not None:
            self._read_task.cancel()
            await asyncio.gather(self._read_task, return_exceptions=True)
            self._read_task = None
        try:
            if self._writer is not None and not self._writer.is_closing() and self._play_attempted:
                obs['teardown_attempted'] = True
                try:
                    await self._send(qv.build_teardown(self._material, timestamp_seconds=int(time.time())))
                    obs['teardown_sent'] = True
                except (OSError, TimeoutError, qv.MediaProtocolError, OutputFailure):
                    obs['cleanup_error_type'] = 'teardown_failed'
        finally:
            if self._writer is not None:
                await close_writer(self._writer)
            obs['tcp_closed'] = True
            self._reader = self._writer = self._material = None
            self._stream_key = self._password = self._pin = self._host = None
