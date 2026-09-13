# Changelog

All notable changes to this project will be documented in this file.

## 0.3.1-beta.2 - 2026-09-13

Connect V1 fixes derived directly from the first beta 1 hardware diagnostics.

- Accept the real V1 terminal-video framing observed on hardware: TLV 99 may use a 12-byte metadata record and terminal TLV 100/101 may declare a short length (observed value: 1) while the complete Annex-B H.264 image occupies the remainder of the already-complete OWSP packet.
- Consume that terminal OWSP remainder only when the same packet first contains a recognized 12- or 16-byte V1 video metadata record, the remaining payload is within the 1-MiB media bound and begins with Annex-B H.264. No arbitrary prefix/header bytes are stripped.
- Keep the 16-byte metadata sequence/size validation when available; treat the observed 12-byte metadata form as opaque rather than inventing undocumented fields.
- Keep fragment TLVs 103/106/107/108 explicitly unsupported and discarded; no speculative reassembly was added.
- Add real-PyAV regression coverage reproducing the hardware shape (12-byte TLV 99 + short terminal TLV 100/101 + Annex-B OWSP remainder) and verify the complete I/P sequence reaches JPEG decoding.
- For V1 output control, keep the existing ring-listener pause/release barrier and add a bounded 1-second hardware-settle interval after confirmed listener release before opening the one-shot control session.
- The settle interval is not a retry: the door-strike/gate request is still sent at most once per accepted action. If the control session cannot be opened, `request_send_attempt_count` and `request_sent_count` remain zero.
- Add privacy-safe diagnostics for the V1 settle wait count/requested/elapsed duration and correct the internal diagnostic version to `0.3.1-beta.2`.
- Connect 2 media bytes, output behavior and the shared Home Assistant WebRTC path remain unchanged.
- Microphone / two-way audio remains **not validated** on hardware.
- Validate with **76 tests plus 3 subtests**, including real PyAV JPEG decoding, V1 transport/routing, V1 ring/control coordination and Connect 2 regressions. HACS and Hassfest validations are green.
- Physical validation on the real V1 is still required before considering the video and output-session fixes confirmed.

## 0.3.1-beta.1 - 2026-09-13

Connect V1 coordination, bounded OWSP reception and complete-video routing candidate.

- Serialize ring-session publication and control pauses: an output session can start only after the ring worker has closed and abandoned its session. An already disconnected listener yields immediately.
- Resume the listener from command cleanup after success, send/confirmation failures or pause timeout; keep the command mutex releasable even if diagnostics or cleanup fails.
- Prevent duplicate ring workers and stale connected-state callbacks during intentional pauses. Refused authentication and UID mismatches remain terminal until integration reload.
- Fail safely if socket release cannot be confirmed: do not send an output command or reconnect alongside an uncertain control session.
- Add count/boolean diagnostics for pause, release, resume, actual reconnection, active sessions and keepalives. Count attempted sends separately from completed sends.
- Retain beta 13 zero-padding/idle-timeout handling, beta 14 discovery caching and V1 Start/Stop AV.
- Add V1-only structural video diagnostics before TLV parsing, including incomplete OWSP reads, lengths, bounded H.264 signature positions and native terminal-boundary comparisons. No media payloads, raw timestamps or identifiers are exported.
- Do not introduce a speculative TLV 97 video parser: the examined native SDK uses 97 as eight-byte audio metadata. Connect 2 video decoding and output session selection remain unchanged.
- On authenticated V1 media sessions only, retain partial OWSP bytes across intermediate socket timeouts, maintain keepalives, and enforce 6-second inactivity / 20-second total / 1-MiB bounds. Never parse incomplete payloads or reuse a reader after a failed partial read.
- Route V1 video metadata separately from complete 100/101 images. Match the native terminal OWSP boundary only when same-packet metadata corroborates its size; require fresh metadata and exact image size before passing unchanged Annex-B bytes to the existing pipeline.
- Preserve native I/P flags and sequence handling; do not promote audio metadata, unknown TLVs or isolated video fragments to H.264. Fragment reassembly remains unsupported pending sufficient validation evidence.
- Validate with 71 offline tests, including the original 38, actual PyAV decoding to JPEG through the V1 worker, slow reception, byte-identical Connect 2 video/audio inputs, and malformed/incomplete data. See `docs/beta15-v1-video-fix.md` for evidence, limits and the pending hardware protocol.
- Physical output commands are still sent at most once per accepted user action, with the existing three-second cooldown. Hardware validation is pending; nothing is deployed by these tests.

## 0.3.0-beta.14 - 2026-09-13

Connect V1 output-control priority over the persistent doorbell listener.

- The V1 doorbell listener now temporarily releases its authenticated `0/3/0` control session before an explicit door/gate command.
- Output control waits for the listener to acknowledge that it has yielded the channel, opens its one-shot control session, sends the requested unlock packet exactly once, then closes that session before the doorbell listener reconnects.
- The persistent listener reconnects automatically after control completes, including when the control attempt fails before sending a command.
- Added privacy-safe diagnostics for control-triggered listener pauses and pause timeouts.
- No unlock or gate command is automatically retried.
- Video transport/decoding is unchanged from beta 13; beta 12 Stop AV and discovery caching are retained.

## 0.3.0-beta.13 - 2026-09-13

Connect V1 doorbell listener stability fix.

- Keep the authenticated V1 doorbell control session alive when `Session.read()` times out after consuming native zero-length OWSP padding frames.
- Treat those zero-padding timeouts as idle activity instead of a broken connection, so the listener can reach its normal keepalive cycle instead of reconnecting every few seconds.
- Preserve the existing 35-second liveness guard for a genuinely silent/dead connection.
- Added privacy-safe diagnostics for listener timeouts and timeouts that followed zero-padding activity.
- Retains beta 12 Stop AV/discovery-cache behavior and all existing no-retry safety rules for unlock/gate commands.

## 0.3.0-beta.12 - 2026-09-13

Graceful Connect V1 media teardown and discovery reuse.

- Reverse-engineered the native `stopGetVideoStream()` path in `libglnkio.so` and added protected **Stop AV** TLV **5009** / response **5010**.
- Any V1 media session that successfully sent Start AV now sends Stop AV before its TCP connection is closed, including failed WebRTC/media startups. This prevents the V1 from remaining busy after a failed preview.
- Media shutdown now wakes a blocked reader without destroying the socket write side, giving the worker a chance to send Stop AV cleanly.
- Successful UDP discovery results are cached for the Home Assistant process and shared by media, doorbell and output-control sessions. Ring/media/control workers no longer rediscover the same configured device for every connection.
- Discovery is serialized so concurrent workers cannot send overlapping UDP discovery bursts to a fragile V1.
- Added privacy-safe Stop AV and discovery cache/request diagnostics.
- No unlock/gate command is retried automatically.

## 0.3.0-beta.10 - 2026-09-13

Native Connect V1 / LT Start AV implementation.

- Reverse-engineered `libglnkio.so` `DataChannelIOCtrl::startGetVideoStream()` and implemented the protected native **Start AV** request TLV **5007** and response TLV **5008**.
- The V1 Start AV request now reproduces the SDK's 60-byte protected payload: three LT profile nonces, AES-128-CFB encryption of device time plus `channel/stream/mode/reserved`, RC4 with the device UID, and OWSP framing.
- Added validation and decoding of the protected 5008 response, including the native device-time/result fields and LT nonce checks.
- Identified Connect V1 devices now send Start AV on the APK-confirmed `16/1/2` live profile before waiting for video, while retaining the stream-mode query used by the Java LT player.
- Removed the beta 9 preview-time I-frame request: APK analysis shows `needIFrame` belongs to video recording rather than normal live preview.
- Added privacy-safe diagnostics for Start AV request/response counts, decode failures and result code. Raw protocol/media payloads remain excluded.
- Connect 2 media handling and beta 8 Home Assistant ICE/STUN/TURN WebRTC behavior are unchanged.

## 0.3.0-beta.9 - 2026-09-12

Targeted Connect V1 / LT protocol compatibility pass.

- Re-analyzed the supplied APK's dedicated `QvLtPlayerCore` path and confirmed the legacy live profile is logical channel 1 mapped to wire channel 16, stream 1, mode 2.
- Reproduced the APK's immediate post-authorization `sendManuData(initTCGetBitStrMode())` request (`01 04 03 00`) for identified V1 devices.
- Added one media-only I-frame request (`01 04 0B 00`) after a V1 announces its 352×288 H.264 format. No door/gate command is sent or retried.
- Once a V1 is recognized, stop cycling speculative media profiles and keep the APK-confirmed 16/1/2 path for a longer startup window.
- Inspect every non-audio top-level TLV for structural H.264 and allow an unknown V1 TLV to become video only when it contains valid H.264 framing.
- Added privacy-safe counters for every top-level media TLV and all H.264-bearing TLV IDs.
- Added V1 manufacturer-query diagnostics including response command/subcommand and stream-mode mapping without retaining manufacturer payloads.
- Added transport framing diagnostics for media and doorbell sessions: read/keepalive counts, invalid big-/little-endian length interpretations and whether the invalid frame followed a keepalive. Raw frame bytes are never retained or exported.

## 0.3.0-beta.8 - 2026-09-12

Remote/mobile WebRTC connectivity fix.

- Fixed server-side WebRTC explicitly disabling all ICE servers with `iceServers=[]`, which could leave remote iPhone/Safari viewers stuck in `connecting` while LAN viewers worked.
- WebRTC now reuses Home Assistant's current `web_rtc` ICE configuration for every viewer, including the default Home Assistant STUN servers and any user- or integration-provided TURN relay.
- ICE server information is refreshed for every new viewer so short-lived relay credentials can be used without being cached by the integration.
- Increased the unconnected viewer grace period from 25 to 45 seconds and the initial WebRTC negotiation timeout from 22 to 30 seconds for mobile networks.
- Added privacy-safe ICE diagnostics: ICE connection/gathering/signaling state, STUN/TURN availability, and local/remote candidate types and protocols.
- ICE candidate addresses, ports, server URLs and TURN credentials are never included in downloadable diagnostics.
- WelcomeEye media decoding, output controls, doorbell support and beta 7 V1 compatibility logic are unchanged.

## 0.3.0-beta.7 - 2026-09-12

Legacy media, doorbell and device-model compatibility update.

- Added privacy-safe H.264 detection on media TLVs 97/99/100/101, including Annex-B and AVCC framing diagnostics.
- Legacy TLVs 97/99 can now be promoted to video when their payload is structurally identified as H.264; Connect 2 TLVs 100/101 remain supported unchanged.
- Added diagnostics for H.264 NAL types, IDR detection, framing, TLV 97/99 counters and the last announced stream format.
- Doorbell listener now tolerates legacy media TLVs instead of disconnecting.
- Added vendor-confirmed ring/call alarm types 7 (doorbell), 19 (call) and 47 (call from), while retaining type 14 observed on Connect 2.
- Doorbell diagnostics now include exact safe error message/stage, top-level and inner TLV counters, alarm-type counters and decode failures.
- Added automatic device-model recognition: 352×288 legacy media is identified as **WelcomeEye Connect V1** and the validated 720×576 signature as **WelcomeEye Connect 2**. The detected model is persisted and updates Home Assistant's device registry.
- Media payloads, alarm payloads, credentials, UID, private IP, SDP and ICE candidate values remain excluded from diagnostics.

## 0.3.0-beta.6 - 2026-09-12

Doorbell support and experimental multi-profile media compatibility.

- Added local doorbell/ring detection through a dedicated control-session listener.
- Added the **Sonnette** binary sensor and `welcomeeye_local.ring` Home Assistant event.
- Added automatic media-profile probing for devices that return TLV 203 but no usable video TLV 100/101.
- Added compatibility profiles derived from the WelcomeEye/Qv SDK while keeping the validated Connect 2 profile first.
- A working media profile is preferred for subsequent sessions.
- Added `selected_media_profile`, current profile, profile attempts and video packet counts to downloadable diagnostics.
- Added safe protocol error details to media diagnostics and output-control errors.
- Media-profile probing never sends door/gate output commands.
- Intended to test video support on WelcomeEye Connect V1 / DES9900VDP.

## 0.3.0-beta.5 - 2026-09-11

WebRTC and media diagnostics update for compatibility testing.

- Added privacy-safe WebRTC stage tracking from offer receipt through answer delivery and peer connection state changes.
- Added the WebRTC failure stage and exception type to downloadable diagnostics.
- Added requested and created media-track summaries without storing SDP or ICE candidate values.
- Added counters for WelcomeEye media TLVs 203, 98, 100 and 101.
- Intended to help diagnose WelcomeEye Connect V1 compatibility where output control works but live video fails.

## 0.3.0-beta.4 - 2026-09-11

Support diagnostics update.

- Expanded downloadable diagnostics with runtime media, worker/session, stream format, buffer and control state.
- Diagnostics explicitly omit private connection data and media payloads.

## 0.3.0-beta.3 - 2026-09-11

Branding update.

- Added Philips WelcomeEye app-style branding assets and README logo.

## 0.3.0-beta.2 - 2026-09-11

Second public beta with local output controls.

- Added **Ouvrir la gâche** (output 1) and **Ouvrir le portail** (output 2).
- Added temporary media-session warmup before output commands.
- Output commands are sent exactly once and never automatically retried.

## 0.2.0-beta.1 - 2026-09-10

First public beta.

- Direct local connection to Philips WelcomeEye Connect 2.
- On-demand H.264 video and G.711 A-law audio handling.
- Home Assistant camera streaming and native WebRTC viewing.
- Configuration and reauthentication through the Home Assistant UI.
