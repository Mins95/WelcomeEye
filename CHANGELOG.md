# Changelog

All notable changes to this project will be documented in this file.

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
