# Changelog

All notable changes to this project will be documented in this file.

## 0.3.0-beta.4 - 2026-09-11

Support diagnostics update.

- Expanded the Home Assistant downloadable diagnostics payload.
- Added privacy-safe runtime media state, worker/session state, negotiated stream format, buffer state and last error type.
- Added control-session state useful for troubleshooting output commands.
- Diagnostics explicitly omit the device IP, username, password, UID, internal stream URL and media payloads.
- Functional video/audio and output-control behavior is unchanged from beta 3.

## 0.3.0-beta.3 - 2026-09-11

Branding update.

- Added the official Philips WelcomeEye app icon to the integration branding assets.
- Added matching light/dark integration icon assets for Home Assistant.
- Added a local integration logo for Home Assistant 2026.3+.
- Added the WelcomeEye logo to the GitHub README.

## 0.3.0-beta.2 - 2026-09-11

Second public beta with local output controls.

- Added **Ouvrir la gâche** (output 1).
- Added **Ouvrir le portail** (output 2).
- Added protected output-command support and device acknowledgement handling.
- When no media session is active, the integration briefly initializes a temporary video session before sending the command, then releases it immediately.
- Output commands are sent exactly once and are never automatically retried.
- The existing on-demand media behavior remains unchanged while idle.
- Ring/button event detection and microphone/two-way audio are still under development.

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
