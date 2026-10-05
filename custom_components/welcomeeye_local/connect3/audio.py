"""Bounded QV downstream audio; format comes from the native frame header.

IAudioPlay::OnDecode 0x46be78 maps 4/5/9 to PCMA/PCMU/native S16.
IAudioCodecFFMPEG::CreateDecoder 0x57d184 maps 8 to AAC; DecodeAudio
0x57d4e8 passes the frame payload directly to avcodec without extradata.
No guessed codec, audio configuration, capture, or device I/O is performed.
"""
from fractions import Fraction


MAX_AUDIO_PAYLOAD = 64 * 1024
MAX_PACKET_DURATION_MS = 200
MAX_DECODED_FRAMES = 16
SAMPLE_RATES = frozenset((8000, 11025, 12000, 16000, 22050, 24000, 32000,
                          44100, 48000))
CODECS = {4: 'pcm_alaw', 5: 'pcm_mulaw', 8: 'aac', 9: 'pcm_s16le'}


class UnsupportedAudioFormat(ValueError):
    """A fixed local reason for an audio format not supported here."""


class AudioDecodeError(ValueError):
    """A fixed local reason; never include packet bytes or decoder text."""


class AudioDecoder:
    def __init__(self):
        import av
        self._av = av
        self._decoder = None
        self._converter = None
        self._fifo = None
        self._format = None
        self._clock = Fraction(0)
        self.errors = 0
        self.frames = 0
        self.diagnostics = {
            'status': 'idle', 'codec_id': None, 'codec': None,
            'sample_rate': None, 'channels': None,
            'input_packets': 0, 'input_bytes': 0, 'decoded_frames': 0,
            'decoded_samples': 0, 'unsupported_packets': 0,
            'decode_errors': 0, 'last_error_reason': None, 'closed': False,
            'buffered_samples': 0,
        }

    def _fail(self, reason, *, unsupported=False):
        self.errors += 1
        self._decoder = self._converter = self._fifo = self._format = None
        self.diagnostics['buffered_samples'] = 0
        self.diagnostics['last_error_reason'] = reason
        if unsupported:
            self.diagnostics['status'] = 'unsupported'
            self.diagnostics['unsupported_packets'] += 1
            raise UnsupportedAudioFormat(reason) from None
        self.diagnostics['status'] = 'decode_error'
        self.diagnostics['decode_errors'] += 1
        raise AudioDecodeError(reason) from None

    def feed(self, packet):
        obs = self.diagnostics
        obs['input_packets'] += 1
        payload = packet.payload
        if isinstance(payload, bytes):
            obs['input_bytes'] += len(payload)
        codec, rate, channels = packet.codec, packet.sample_rate, packet.channels
        # Copy only scalar format metadata; never include arbitrary input text.
        obs.update(codec_id=codec if type(codec) is int else None,
                   codec=CODECS.get(codec) if type(codec) is int else None,
                   sample_rate=rate if type(rate) is int else None,
                   channels=channels if type(channels) is int else None,
                   last_error_reason=None)
        if obs['closed']:
            self._fail('audio_decoder_closed')
        if not packet.is_audio:
            self._fail('unsupported_audio_frame', unsupported=True)
        if type(codec) is not int or codec not in CODECS:
            self._fail('unsupported_audio_codec', unsupported=True)
        if (type(rate) is not int or rate not in SAMPLE_RATES
                or type(channels) is not int or channels not in (1, 2)):
            self._fail('unsupported_audio_format', unsupported=True)
        if not isinstance(payload, bytes) or not 0 < len(payload) <= MAX_AUDIO_PAYLOAD:
            self._fail('audio_packet_size')
        maximum_samples = rate * MAX_PACKET_DURATION_MS // 1000
        expected_samples = None
        if codec != 8:
            sample_width = (2 if codec == 9 else 1) * channels
            if len(payload) % sample_width:
                self._fail('audio_sample_alignment')
            expected_samples = len(payload) // sample_width
            if expected_samples > maximum_samples:
                self._fail('audio_packet_duration')
        format_key = (codec, rate, channels)
        try:
            if self._decoder is None or self._format != format_key:
                decoder = self._av.CodecContext.create(CODECS[codec], 'r')
                decoder.sample_rate = rate
                decoder.layout = 'mono' if channels == 1 else 'stereo'
                decoder.thread_count = 1
                # AAC reserves 2048 work samples even for a 1024-sample frame.
                # Keep that allocation bounded; enforce duration on output below.
                decoder.options = {'max_samples': str(max(2048, maximum_samples) * channels)}
                self._decoder, self._format = decoder, format_key
                # aiortc's Opus encoder requires packed S16 before its resampler.
                # Convert format only, preserving the bounded source rate/layout.
                self._converter = self._av.AudioResampler(format='s16',
                    layout=decoder.layout.name, rate=rate)
                self._fifo = self._av.AudioFifo()
                obs['buffered_samples'] = 0
            frames = self._decoder.decode(self._av.Packet(payload))
        except (self._av.FFmpegError, ValueError, OverflowError):
            self._fail('audio_decode_error')
        # Validate every output before publishing any frame or advancing time.
        if len(frames) > MAX_DECODED_FRAMES:
            self._fail('decoded_audio_limit')
        if any(frame.sample_rate != rate or len(frame.layout.channels) != channels
               or frame.samples <= 0 for frame in frames):
            self._fail('decoded_audio_format')
        samples = sum(frame.samples for frame in frames)
        if samples > maximum_samples or (expected_samples is not None and samples != expected_samples):
            self._fail('decoded_audio_limit')
        try:
            frames = [converted for frame in frames
                      for converted in (self._converter.resample(frame)
                          if frame.format.name != 's16' else [frame])]
        except (self._av.FFmpegError, ValueError, OverflowError):
            self._fail('audio_conversion_error')
        if (len(frames) > MAX_DECODED_FRAMES
                or sum(frame.samples for frame in frames) != samples
                or any(frame.format.name != 's16' or frame.sample_rate != rate
                       or len(frame.layout.channels) != channels for frame in frames)):
            self._fail('decoded_audio_format')
        # aiortc assigns one RTP timestamp to all packets returned from one
        # input frame. Large AAC frames would therefore concatenate several
        # Opus packets under the same timestamp. Publish at most 20 ms each,
        # retaining a sub-frame tail for the next packet at the source rate.
        chunk_samples = rate // 50
        if self._fifo.samples + samples > maximum_samples + chunk_samples:
            self._fail('audio_buffer_limit')
        try:
            for frame in frames:
                frame.pts = None
                self._fifo.write(frame)
            frames = []
            while self._fifo.samples >= chunk_samples:
                frames.append(self._fifo.read(chunk_samples))
        except (self._av.FFmpegError, ValueError, OverflowError):
            self._fail('audio_buffer_error')
        obs['buffered_samples'] = self._fifo.samples
        for frame in frames:
            frame.pts = round(self._clock * rate)
            frame.time_base = Fraction(1, rate)
            self._clock += Fraction(frame.samples, rate)
        self.frames += len(frames)
        obs['decoded_frames'] = self.frames
        obs['decoded_samples'] += samples
        obs['status'] = 'decoded' if frames else 'buffering'
        return frames

    def close(self):
        self._decoder = self._converter = self._fifo = self._format = None
        self.diagnostics.update(closed=True, status='closed', buffered_samples=0)
