# Connect 3 downstream audio evidence

Static source: Door Connect 1.0.123.3(2), ARM64 `liblive_player.so`, SHA256
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
The original local binary was checked. Addresses are ELF virtual addresses.
This establishes the app's format handling, not the codec actually emitted by
the tester's intercom. No device request or hardware audio test was performed.

## Native frame metadata and playback

| Native function | Address | Result |
| --- | --- | --- |
| `CFramePack::PackFrame` | `0x4aafa4` | Shared 20-byte frame header; payload length LE u32 at +4 |
| `CPacket::FrameIsAudio` | `0x87488c` | Excludes frame types 7 and 8, then recognizes codecs 4, 5, 6, 7, 8, 9, 12, 13 |
| `CPacket::FrameGetCodec` | `0x874240` | Codec byte at +14 |
| `CPacket::FrameGetChnNum` | `0x8750c4` | Channel count byte at +15, which is the video fps-times-four slot |
| `CPacket::FrameGetFreq` | `0x875080` | Sample rate LE u16 at +16, which is the video width slot |
| `ITDKDecoder::AudioPlay` | `0x825160` | Validates audio; routes codecs 8 and 12 through its audio decoder and other audio to its player |
| `IAudioPlay::OnDecode` | `0x46be78` | Codec 4 calls PCMA; 5 calls PCMU; 9 copies raw PCM |
| `IAudioDevice::DecodeAudioPCMA` | `0x469f84` | One A-law byte becomes one native 16-bit sample |
| `IAudioDevice::DecodeAudioPCMU` | `0x469e90` | One mu-law byte becomes one native 16-bit sample |
| `IAudioCodec::CheckCodec` | `0x57f14c` | Reads codec, frequency and channel count from the frame and reopens on a format change |
| `IAudioCodecFFMPEG::CreateDecoder` | `0x57d184` | Codec 8 selects FFmpeg AAC ID `0x15002`; initializes decoder without AAC extradata |
| `IAudioCodecFFMPEG::DecodeAudio` | `0x57d4e8` | Passes `FrameGetData` and `FrameGetLength` directly to avcodec; it adds no ADTS header or other configuration |

The PCM branch copies 960 bytes where each G.711 branch decodes 480 samples.
The decoder stores native 16-bit shorts on this little-endian ARM64 target,
which establishes the signed 16-bit little-endian PCM output format. AAC is
decoded directly; the implementation does not guess missing AudioSpecificConfig
or prepend an invented transport header. Self-describing synthetic AAC and
malformed packets are tested separately from G.711 and PCM.

The native FFmpeg wrapper initializes and resamples its output to 8000 Hz mono.
That application playback choice is not evidence that all incoming frame
headers advertise those values. The integration preserves valid declared
source rate and channels, converts the sample representation to packed S16
required by aiortc, and leaves browser-track rate conversion to WebRTC. It does
not replace a missing rate or channel count with defaults.

The local WebRTC loop exposed another transport constraint: aiortc gives all
Opus payloads encoded from one input frame the same RTP timestamp. Sending a
whole 1024-sample AAC frame at 8000 Hz this way stalled the receiving audio
jitter buffer. A bounded FIFO now emits chunks of `floor(rate / 50)` samples
(at most 20 ms), retaining the incomplete tail for the next input packet. This
preserves the source rate and channels while producing at most one Opus packet
per input frame. A real aiortc loopback now receives AAC and G.711 audio during
video, microphone start/stop/restart and simulated output actions.

Codecs 6 and 7 have no supported mapping here. Native codec 12 selects old
FFmpeg ID `0x11804` with five coded bits and 40000 bit/s; codec 13 selects
`0x1100b` with four bits and 32000 bit/s. No independent G.726 bitstream
verification has been completed, and those formats remain unsupported.

## Local decoder limits and diagnostic meaning

These are implementation limits, not claims about the device's capabilities:

- Supported codecs: 4 / A-law, 5 / mu-law, 9 / S16LE PCM, 8 / direct AAC.
- Declared rates: 8000, 11025, 12000, 16000, 22050, 24000, 32000, 44100, 48000 Hz.
- One or two channels, at most 64 KiB per payload and 200 ms decoded audio per
  packet. PCM and G.711 duration/alignment are checked before decoder use.
- At most 16 output frames; decoded rates and channel counts must match the
  declared format. Format conversion preserves that rate and channel count;
  no rate upsampling or additional network session occurs in this decoder.
- An incomplete output chunk remains buffered for the next packet; format
  changes, errors and close discard that tail without flushing after stop.
- Unknown formats and decode failures have separate counters and fixed local
  reasons. Callers catch the audio-specific exceptions and keep video running.

Diagnostics contain only codec identifiers/names, rate, channels, counts,
status and fixed reasons. Packet data, PCM samples, AAC bytes and remote
timestamps are never copied into diagnostics. Received audio packets/bytes
and decoded frames/samples are separate; frames count the emitted chunks,
samples count decoded source samples, and `buffered_samples` reports the tail.
Receiving a packet does not prove
audible playback. `close()` drops the decoder context and keeps only these
diagnostic values. Audio hardware validation and the device's actual selected
format remain pending.
