# V1 startup query: field investigation

The tester's Connect V1 / DES9900VDP runs Home Assistant 2026.9.2 and
WelcomeEye 0.4.1-beta.3. The original card reproduced a frozen image while
its WebRTC connection still reported connected. Earlier device diagnostics
showed remote TCP EOF after approximately 4.3 seconds, with decoded video
and no H264 decoding error.

## Isolated device comparison

A bounded probe ran in the test instance's terminal add-on, independently
of Home Assistant's media worker and codecs. It loaded the installed
Session and packet parser, refused to run while HA owned media, used a
single connection, and blocked physical-output and microphone commands.
Credentials stayed on the test instance. Only timings, TLV types, lengths
and counts were recorded.

| Sequence | Result |
| --- | --- |
| Protected login, Start AV 16/1/2, normal keepalive; no stream-mode query | 270 video packets in the 15-second observation window; no EOF |
| Same sequence, with the existing stream-mode query TLV 509 | Start AV accepted; 27 video packets; remote EOF at 4,214 ms after probe start |
| Minimal sequence repeated after recovery | 269 video packets in 15 seconds; no EOF |

An earlier probe failed during discovery and sent no TCP command; it is
not evidence for or against the query hypothesis. Each completed probe
attempted the existing Stop AV and session-stop sequence, then closed TCP.
No TLV 505 was attempted in any hardware test.

## Minimal correction

Omit the optional `QUERY_STREAM_MODE` request in both V1 startup paths:
already identified V1, and V1 identified by the first 352x288 format.
TLV 203 already supplies the format used by the decoder. Keep profile
16/1/2, login, codecs, keepalive, Start/Stop AV, session release, encryption,
password encoding, output mapping and the single-attempt output path
unchanged. Diagnostics explicitly report the query policy and send count.

The APK does call `getFps()` through `QvLtPlayerCore.getDeviceInfo()` after
authorization. These results establish a failure triggered by this
integration's particular request sequence on this unit, not that all V1
firmware rejects every equivalent request from the official application.
The exact firmware behavior or envelope/timing difference remains unproven.

## Independent snapshot stall

After omitting the query, the real HA card still froze. The worker remained
alive in `decode_video`, with zero H264 errors, and shutdown timed out after
15 seconds. A privacy-safe Python stack identified `media.py:234`, the
`frame.to_image().save(...)` call. One reproduction stopped after 407 video
packets / 406 decoded frames; an earlier one stopped after 56 packets.
This is separate from the device's query-triggered EOF. The stack identifies
the JPEG call, not the exact native instruction or lock responsible.

Snapshot conversion now uses one reusable, worker-owned VideoReformatter
with one scaling thread, and finishes before publishing the frame to WebRTC.
It no longer relies on each shared decoded frame's implicit converter and
automatic scaling thread allocation. No H264 bitstream or decoder change.
Diagnostics report only whitelisted module basenames and line numbers for
the media worker, never source text, local variables or full paths.

Real PyAV tests produce JPEG snapshots for V1 and Connect 2 dimensions while
a separate consumer converts published frames; they verify publication
occurs after snapshot completion and all images have the expected dimensions.

## Regression suite

Two regression cases model the observed query-triggered EOF, using the real
worker, Session, crypto and PyAV with an in-memory socket. Both fail on beta.3
and pass after the correction, including model discovery from the first
format. An explicitly requested simulated output uses the original media
session and sends TLV 505 exactly once. Full suite: 198 tests and 3 subtests
pass. Tests are in the workspace's `work/v1-stalled-validation` directory.

The test instance has backups under `/config/welcomeeye_dev/`; no Docker
protection setting, external port exposure or authentication was changed.

## On-device verification after both corrections

The first HA card session ran for 133,306 ms and decoded 2,608 video frames
with zero decoding errors. Screenshots at 13, 60 and 128 seconds showed the
device clock advancing by the corresponding intervals. Closing the card
sent the existing Stop AV and session-stop, closed TCP, and stopped the
worker in 19 ms. No media consumer/session/worker remained; WebRTC cleanup
reported no error. The device-release confirmation field remained null,
so this does not establish a received Stop AV acknowledgment.

A second independent session ran for 59,774 ms, decoded 1,139 video frames,
and stopped in 17 ms with the same clean HA state and no decoding or cleanup
error. Both sessions recorded zero physical-output send attempts.

A third session also kept its on-screen clock moving, ran for 64,597 ms,
decoded 1,231 frames and stopped in 11 ms. Final state: zero consumers,
no media worker or session, TCP closed, zero physical-output attempts and
no decoding or WebRTC cleanup error. The bounded observation process ended.

No gate, strike, microphone or official-app physical test was performed.
The test instance contains these patches on top of beta.3; the published
beta.3 tag and release asset have not been replaced.
