# Connect 3 microphone: application evidence and implementation limits

This is static evidence from Philips Door Connect `1.0.123.3(2)`, followed by
synthetic tests. The owner subsequently confirmed the primary-panel microphone
on A331/TCP. A protocol acknowledgement alone is not physical validation.
The secondary-panel destination remains unproven; see the
[RC1 routing review](connect3-rc1-sdk-evidence.md).

## Provenance

XAPK SHA256:
`f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
ARM64 `liblive_player.so` SHA256:
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
Native addresses below are virtual addresses in that exact binary; DEX
addresses are `code_item` offsets. No APK binary or private key is packaged.

## Android microphone to its separate talk connection

`QvPlayerCore.buildTalkConnection()` (`classes2.dex`, `0x28cb80`) defaults to
channel 65535 and `mIsTalkListen=false`. The overload at `0x28cbdc` constructs
the LAN URL `quii://username:correctPassword@IP:streamPort/talk/idc=65535`.
The IP-added QV branch selects `ap=2`; TLS-capable devices add `tls=1`.
It passes the existing `mDataEncodeKey` to
`AudioPlayerManager.startTalking(url,key)` at `0x28cdca`.
`startTalk()` (`0x28eb6c`) calls `startSendVoice` at `0x28ebf6`.
The existing video stop path (`0x28ec00`) breaks talk before releasing video.

`AudioPlayerManager` (`classes4.dex`) initializes recording to 8000 Hz
(`0x221a8c`). `RecordInit` (`0x22144c`) constructs Android `AudioRecord` with
mono PCM16, a minimum buffer of 640 bytes and an Android-dependent buffer
size. Its recording loop (`0x221890`) reads PCM and calls `sendVoiceData`.
With the default listen=false, that loop calls `requestAudio(url,false)`
before recording and restores it after recording stops. `stopSendVoice`
(`0x221ff0`) stops recording; `stopTalking` (`0x222040`) stops the connection.

JNI `AudioPlayerManager_startTalk` (`0x462390`) calls
`QVTalkStart(url,key,key,key)`. `IQUIITalkDevice::Start` (`0x54de90`, allocation
at `0x54e104`) creates a **distinct CQUIITalk connection** for `ap=2`.
JNI `sendVoiceData` (`0x462410`) enters `IAudioCap::OnAudioData` (`0x469420`),
then `OnAudioCapture` (`0x469454`). `requestAudio` (`0x4625cc`) calls
`QVTalkRequestAudio`; `stopTalk` (`0x462648`) calls `QVTalkStop` and clears
the audio buffer.

This proves that an additional audio socket is part of the app's microphone
path. It is not a second video session or a second reader of the live socket.
The integration copies ephemeral key/password material only from its accepted
live session. It issues no second CGI request, discovery or video acquisition.
The new audio socket uses the existing media certificate-pin verification.

## Talk negotiation

All headers are 32 bytes. Integer fields below are little endian.

| Native function | Wire operation |
| --- | --- |
| `CQUIITalk::SendSetup`, `0x550a20` | command A9, selector 2 at byte 9; setup reply carries encryption and SHA modes |
| `SendOpen`, `0x550adc` | command 0B, seconds u64 at 1, encoded extension length u16 at 9, plaintext parameter length u16 at 11, channel 65535 u16 at 13 |
| `OnRecvOpen`, `0x551410` | result at byte 11, codec mask u16 at 12, outbound framing variant at 14 |
| `SendPlay`, `0x551620` | request transmit enabled, then request receive enabled |
| `OnSendRequest`, `0x5516a0` | 0C transmit / 0D receive; channel u16 at 11, enable byte at 13; transmit also carries codec index at 14 and 8000 u16 at 15 |
| `OnRecvPlay`, `0x551bb0` | rejects nonzero results; native 0D acceptance marks the talk object active |

Open uses the existing live credential string arrangement:
`username&&password`, NUL, custom ID, NUL, optional client ID.
The integration uses its existing app username and encoded local auth code,
with empty custom/client IDs. Command authentication/encryption uses the
existing QV helpers: negotiated SHA256, zero block alignment where required,
and independent AES-CBC initialization for header and extension.
The integration requires **both 0C and 0D acceptance** before emitting audio,
then sends 0D receive-disabled, matching Android's default listen=false.
The latter does not stop downstream audio on the video socket.

`OnRecvOpen` prefers mask bit 4 (AAC) at `0x5514ec`, otherwise the first set
bit in 0..5; `GetCodecType` (`0x551344`) maps index to index+4.
Supported, proven codecs are internal 4=G.711 A-law, 5=G.711 mu-law,
8=AAC and 9=PCM16 little endian. Indices selecting other codecs fail closed.
This selection comes from the **talk reply**, never from downstream audio.

`IAudioCap::OnAudioCapture` has direct G.711/PCM branches and accumulates
at least 480 bytes (`0x469614`); Android buffer sizing determines actual
chunk sizes. AAC `CreateEncoder` (`0x57cedc`) uses FFmpeg AAC ID `0x15002`,
8000 Hz mono, float-planar input, 24000 bit/s and encoder frame size 1024.
`EncodeAudio` (`0x57d950`, packet copy `0x57dab8`) sends the AVPacket bytes
directly. There is no native ADTS insertion at that step; the integration
does not invent one. AAC exact firmware acceptance still needs hardware test.

## Outbound audio frames and stop

`CQUIITalk::OnSendData` (`0x5521c8`) sends command A2. Its 32-byte header has
seconds u64 at 1, encrypted extension length u16 at 9, body length u32 at 11,
media-encrypted byte at 15 and media offset u16 at 16.
The constructor (`0x55052c`) fixes extension=32 and media-encrypted=0.
Header and the first 32 body bytes are encrypted independently; subsequent
audio bytes are plaintext under that negotiated packet mode. Unencrypted
mode has extension zero. No extra media SHA or invented audio padding is added.
Frames shorter than the required encrypted prefix are rejected safely.

Variant zero uses an eight-byte F001 audio header: magic `00 00 01 f0`,
wire codec byte, sample-rate code byte and payload length u16. Wire codecs
are 14=A-law, 10=mu-law, 31=AAC, 12=PCM; sample-rate code 2 is 8000 Hz.
Nonzero variants use the existing 20-byte QV audio frame: magic
`00 00 01 e3`, payload length, packed QV time, milliseconds, internal codec,
mono channel count and 8000 Hz. `CQVTime::SetTime` (`0x88baa0`,
`gmtime_r` call `0x88bb04`) packs UTC calendar fields;
`GetTime` (`0x88be8c`) copies the packed word. `FrameSetTime` is `0x875214`.

`CQUIITalk::OnStop` (`0x550768`) invokes common stream teardown, clears
queued audio and closes the dedicated TCP connection. The integration sends
common teardown 7 at most once and closes only the talk socket. Microphone
OFF immediately gates sending; buffered encoder work settles without flushing
audio after OFF. Video close/unload also closes talk before its live session.

## Bounds, diagnostics and software validation

One explicit start attempt, no automatic retry/reconnect. Startup and receive
inactivity are bounded; each write is bounded. Only one task reads the talk
socket. Owner, live-session identity and microphone heartbeat are checked.
Codec/network failures disable and clean up microphone without terminating
the browser's inbound task or closing its video. Codec operations are bounded
for 8–192 kHz mono/stereo input, at most 200 ms per input frame; no AAC flush
is performed during stop.

Diagnostics distinguish TLS, setup, open, transmit/receive acceptance,
negotiated talk codec/framing, input frames, send attempts, transmitted frames,
byte counts, receive stage and cleanup. They contain no URL, address, key,
password, timestamp or audio bytes. `physically_verified` remains false.
`channels: 1` describes mono audio, not outdoor panel 1. RC1 also spells this
out as `audio_channel_count: 1`, separately from the protocol's
`talk_selector: 65535`; neither field proves the physical speaker destination.
Synthetic tests cover wire construction, fragmented negotiation, local codecs,
owner isolation, rejection, timeout, cancellation, media-loss gates, encoder
settlement, cleanup errors and reactivation. They are not device recordings.

## Local sonnerie investigation limits

The existing QV live reader can observe FE command metadata without adding
a socket or subscription. `QvPlayerCore.u` (`classes2.dex`, `0x28b2e0`)
dispatches order 23 to `onOtherDoorBellCall` (`0x28b3cc`), taking the first
parameter byte and the UTF-8 tail. The only concrete implementation found,
`DeviceCallBackImp.onOtherDoorBellCall` (`0x289110`), returns immediately.
The callback name is a candidate correlation clue, not proof that every
physical button press produces it. The potentially identifying UTF-8 tail
must not be exported. Order 1 also dispatches hang-up data.

A separate closed-player alarm path exists: `IQUIIDevice::OnAlarmListenStart`
(`0x4b0ae4`) and `CQVIIDevice::OnStartListen` (`0x4ac380`) call
`SDK_StartListenEx` (`0x67bad0`). `CAlarmDeal::StartListenEx` (`0x772608`)
requires an already logged-in SDK device and obtains alarm channel selector 6.
`IQUIIDevice::Login2` (`0x4aec24`, SDK login call `0x4aee40`) is a distinct
native SDK login path. Its exact compatibility with the existing CGI/live
credentials has not been established. `CDvrAlarmChannel::OnRespond`
(`0x7db2ac`) has B1 and 69 protocol branches; generic alarm-query helpers
are not proof of a Connect 3 subscription contract. No new alarm query,
closed-player listener, UDP alarm broadcast or ring detection is inferred.

The bounded tester observation therefore uses only an already active,
accepted live session and its sole reader. It does not acquire video itself.
An absent candidate cannot establish that local sonnerie is unsupported.
