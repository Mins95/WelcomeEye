# 📖 WelcomeEye documentation

User guides for **0.4.7 stable**, covering Connect V1, Connect 2 R001/R002 and Connect 3. Start here to install, configure or customize your integration without reading protocol research.

**New installation?** [Follow the installation guide](installation.md).

**Already installed?** [Find a setting](settings.md) or [edit your card](card.md).

**Testing 0.4.8?** [Beta 2 photo stabilization, validation and rollback](release-048-beta2.md).

**New in 0.4.7:** [installation, changes and rollback](release-047.md),
[doorbell events, photo viewer, microphone preferences, stream statistics
and device/SD information](development-options.md).

## 🧭 Choose a guide

| I want to… | Guide |
| --- | --- |
| Install with HACS or update an existing installation | [Installation and upgrades](installation.md) |
| Understand every configuration option and its default | [Settings reference](settings.md) |
| Add one card, rename entrances/buttons, use two cameras | [Dashboard card](card.md) |
| Change card colors, use compact mode or configure a popup | [Theme, layout and Browser Mod examples](card.md#change-the-theme-or-use-compact-mode) |
| View saved photos in the card, set autoplay or automatic closing | [Card options](card.md#presentation-and-playback-options) |
| Choose individual portillon/portail buttons | [Opening settings](settings.md#opening-commands) and [physical routing](connect3-four-outputs.md) |
| Enable Connect 3 or R002 doorbells | [Cloud setup with the official app account](qv-cloud-doorbell.md) |
| Enable V1 doorbells | [V1 cloud setup without an app-account login](v1-cloud-doorbell.md) |
| Save, browse or delete visitor photos | [Photos and library](captures.md) |
| Change capture delay, retention or sidebar visibility | [Photo settings](settings.md#photo-management) |
| Add the camera to Apple Home | [HomeKit Bridge](card.md#homekit-bridge) |
| Resolve a connection, card, sound or photo issue | [Troubleshooting](troubleshooting.md) |
| Update to stable or roll back | [Installation and rollback](release-047.md) |
| Send a saved ring photo to the Companion app | [Import and configure the notification blueprint](release-047.md#import-the-photo-notification-blueprint) |
| Read Connect 3 / R002 history, badges or device information | [Cloud history](cloud-history.md), [badge catalogue](badge-catalog.md) and [device/SD information](development-options.md#device-information-and-sd-card) |

## ⚙️ Where to change what

- **Integration → Reconfigure:** connection, second panel, language, enabled openings, cloud doorbell and photo management.
- **Device page → Ring image capture switch:** turn automatic visitor photos on or off.
- **Edit dashboard → Edit the WelcomeEye card:** title, camera, custom names, appearance, layout, saved-photo preview/history and playback options.
- **WelcomeEye sidebar:** administrator photo selection/deletion. **Media → WelcomeEye** remains the browsing location even if the sidebar is hidden.

> **Known limitation:** opening media for a photo or live view can stop the physical monitor's ongoing ringing. Official app notifications continue. Changing the automatic-photo delay does not guarantee avoiding this. [Details and choices](captures.md).

## Model and release details

- [0.4.8-beta.1 network recovery and field checks](release-048-beta1.md) — optional prerelease; stable 0.4.7 remains available.
- [Compatibility table](../README.md#-supported-devices) — Connect 3 support is validated, including cloud doorbells.
- [Connect 3 automatic connection and TLS](connect3-auto-tls.md) · [Connect 3 functions](connect3-audio-controls.md).
- [Connect 2 R002 setup](r002-investigation.md).
- [Four-output guide in French](connect3-four-outputs.fr.md).
- [0.4.7 release notes and rollback](release-047.md) · [Changelog](../CHANGELOG.md) · [Security and privacy](../SECURITY.md).
- [0.4.7-beta.3 video recovery fix](release-047-beta3.md).
- [0.4.7-beta.2 notes](release-047-beta2.md) · [reliability and HA compatibility](development-hardening.md).
- [0.4.7-beta.1 auxiliary-camera contribution](release-047-beta1.md), retaining attribution to [@rpouetpouet and PR #16](https://github.com/Mins95/WelcomeEye/pull/16).
- [Cloud connection and subscription status](settings.md#cloud-diagnostics).
- [Older V1 setup and firmware information](installation.md#older-connect-v1-devices).

## Protocol evidence and research

- Connect 3: [APK analysis](connect3-analysis.md), [video](connect3-beta7-video-evidence.md), [audio](connect3-audio-evidence.md), [microphone](connect3-talk-evidence.md), [outputs](connect3-control-evidence.md), [multichannel evidence and limits](connect3-multichannel-evidence.md), [native alarm and routing research](connect3-rc1-sdk-evidence.md).
- R002: [APK analysis](r002-apk-analysis.md), [QV transport evidence](r002-beta11-apk-evidence.md).
- V1: [media/control stability](v1-stability-beta7.md), [startup investigation](v1-startup-query-field-test.md), [local doorbell audit](v1-doorbell-reinvestigation-beta11.md).
- Photos: [passive observation](ring-passive-observation.md), [earlier delayed-capture records](ring-image-delayed-candidate.md).

These research records describe the implementation and observations at their date. The homepage and user guides describe current support; older validation statuses and terminology belong to the archived records, not the current interface.

## Release history

Previous release notes and protocol reports are preserved as dated historical evidence, not current setup instructions. See the [changelog](../CHANGELOG.md) for the release sequence.
