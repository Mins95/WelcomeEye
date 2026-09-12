# Changelog

All notable changes to this project will be documented in this file.

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
