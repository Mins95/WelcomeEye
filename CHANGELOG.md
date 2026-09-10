# Changelog

All notable changes to this project will be documented in this file.

## 0.2.0-beta.1 - 2026-09-10

First public beta release, intended for testing while development is still in progress.

- Direct local connection to Philips WelcomeEye Connect 2.
- On-demand H.264 video and G.711 A-law audio handling.
- Home Assistant camera streaming and native WebRTC viewing.
- Passive JPEG snapshots from the most recently decoded active video stream.
- Diagnostic video-session connectivity, resolution and frame-rate entities.
- Configuration and reauthentication through the Home Assistant UI.
- Default username is `admin`.
- The password is the device password entered in the WelcomeEye app to open the gate.
- Public-release hardening: private development addresses and credentials are excluded from the repository and diagnostics omit credentials and device UID.
