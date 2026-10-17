<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/header.svg" alt="Philips WelcomeEye for Home Assistant" width="100%">
</p>

<h1 align="center">Philips WelcomeEye for Home Assistant</h1>

<p align="center">
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.4"><img src="https://img.shields.io/badge/Stable-v0.4.4-0080ff?style=for-the-badge" alt="Stable release v0.4.4"></a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="License"></a>
  <a href="https://github.com/Mins95/WelcomeEye/commits/main"><img src="https://img.shields.io/github/last-commit/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Last commit"></a>
  <a href="https://github.com/Mins95/WelcomeEye"><img src="https://img.shields.io/github/languages/top/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Top language"></a>
  <a href="https://github.com/Mins95/WelcomeEye"><img src="https://img.shields.io/github/languages/count/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Language count"></a>
  <a href="https://github.com/Mins95/WelcomeEye/stargazers"><img src="https://img.shields.io/github/stars/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Stars"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases"><img src="https://img.shields.io/github/downloads/Mins95/WelcomeEye/total?style=flat-square&label=Downloads&color=0080ff" alt="Total downloads"></a>
</p>

<p align="center">
  <a href="https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.4"><img src="https://img.shields.io/badge/Version-v0.4.4-0080ff?style=flat-square" alt="Current stable version v0.4.4"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.4"><img src="https://img.shields.io/badge/Release%20date-17%20October%202026-0080ff?style=flat-square" alt="Stable release date: 17 October 2026"></a>
  <a href="https://github.com/Mins95/WelcomeEye/issues"><img src="https://img.shields.io/github/issues/Mins95/WelcomeEye?style=flat-square&label=Open%20issues&color=0080ff" alt="Open issues"></a>
  <a href="https://github.com/Mins95/WelcomeEye/issues?q=is%3Aissue%20is%3Aopen%20label%3Abug"><img src="https://img.shields.io/github/issues-search/Mins95/WelcomeEye?query=label%3Abug%20is%3Aopen&style=flat-square&label=Open%20bugs&color=0080ff" alt="Open bugs"></a>
  <a href="https://github.com/Mins95/WelcomeEye/pulls"><img src="https://img.shields.io/github/issues-pr/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Open pull requests"></a>
</p>

<p align="center">
  <a href="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml"><img src="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml/badge.svg?branch=main" alt="Validation"></a>
  <a href="https://github.com/Mins95/WelcomeEye/actions/workflows/release.yml"><img src="https://github.com/Mins95/WelcomeEye/actions/workflows/release.yml/badge.svg?branch=main" alt="Release workflow"></a>
</p>

<p align="center">
  Live video, sound, microphone, door/gate control and visitor photos in Home Assistant.<br>
  Media and opening commands stay local. Optional cloud notifications are available for V1, Connect 3 and R002; Connect 2 R001 detects rings locally.
</p>

---

## ✨ Features

- 🎥 **Live video** in the WelcomeEye card, with the standard HA camera path also available where supported.
- 🔊 **Sound and microphone** to hear visitors and speak from the dashboard.
- 🚪 **Strike and gate controls** from dedicated HA buttons or the camera card.
- 🔔 **Doorbell events** locally on Connect 2 R001 and through the optional cloud route on V1. 0.4.4 adds cloud enrollment for Connect 3/R002, with field delivery still to verify.
- 📸 **Visitor photos** and manual snapshots, saved privately in **Media → WelcomeEye** on supported models.
- 🏠 **Local media and opening commands**, shared media sessions and privacy-safe diagnostics.

Features vary by model; automatic photos retain the ringing limitation below.

## 📦 Supported devices

**0.4.4 is the stable release.** It includes the hardware confirmations below; support depends on the model, firmware and selected panel:

| Device | Live video / sound | Microphone | Strike / gate | Doorbell | Photos |
| --- | --- | --- | --- | --- | --- |
| **Connect V1 / DES9900VDP** | ✅ Validated | ✅ Validated | ✅ Validated | ✅ **Cloud validated**| ✅ Manual & Automatic |
| **Connect 2 — R001 firmware** | ✅ Validated | ✅ Validated | ✅ Validated | ✅ **Local detection** | ✅ Manual & Automatic |
| **Connect 3 — A350 / TLS** | ✅ Validated | Available | ✅ Validated | ☁️ 0.4.4 · field check pending | Manual with live video |
| **Connect 3 — A331 / TCP, entry 1** | ✅ Validated | ✅ Validated | ✅ Validated | ☁️ 0.4.4 · field check pending | Manual with live video |
| **Connect 2 — R002 firmware** | ✅ Validated | ✅ Validated | ✅ Validated | ☁️ 0.4.4 · field check pending | Unavailable |

**✅ Hardware validated · 🧪 Physical confirmation pending · 🔎 Under investigation**

Use the **WelcomeEye card** for Connect 3 sound, microphone and controls. Physical outputs require explicit activation and their separate opening code. Confirmations apply to the tested firmware and source, not every installation. [Quick Connect 3 setup](docs/connect3-auto-tls.md).

**0.4.4:** presents validated media and opening controls without experimental labels. Existing output choices remain under **Reconfigure → Opening commands**, with fixed physical targets and explicit authorizations. Secondary microphone routing and bell 1/2 mapping still need confirmation. [0.4.4 notes and rollback](docs/release-044.md).

**R002 video, sound, microphone and openings are hardware-confirmed**, including the RC4 report of 16 October 2026. [R002 setup](docs/r002-investigation.md) · [Tester confirmation](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-6216554333).

For **Connect 3/R002 doorbells**, open **Reconfigure → Cloud doorbell notifications** and select the app account's device. A local password/QR alone is insufficient. HA registers independently from the phone; no video or snapshot is started by an alert. [Short setup and test](docs/qv-cloud-doorbell.md).

## ⚠️ Known limitations

> [!WARNING]
> **Automatic photos can stop the intercom's ongoing call/ringing around five seconds after the bell press**, on the indoor monitor and outdoor station. This happens when HA acquires the image through the media session. **Smartphone notifications/ringing through the official Philips app continue.** The HA photo is retained, but this interruption is not fixed. This limitation remains; turn **Capture sur sonnerie / Ring image capture** OFF if you prefer to preserve the monitor's full ringing cycle.

- HA takes its **own fresh photo** from T+4 seconds; it does not retrieve the photo stored by the monitor. An existing video session is reused. Opening live video or refreshing a camera thumbnail can also take media during a call, even with automatic capture OFF.
- Connect V1 requires the optional cloud connection for doorbell events. Its video, microphone, photos and opening commands remain local.
- Connect 3/R002 cloud delivery and bell channel attribution need a field check. The local alarm diagnostic has not identified a usable bell event. Native stored-photo retrieval remains unavailable.
- Only one device media session is available at a time. Close the HA player before using Philips; a brief release delay can occur.
- Microphone access requires **HTTPS with a trusted certificate** and browser/app permission. Restrictive networks can also block WebRTC.
- Saved photos are kept indefinitely by default. An optional retention period is available under **Reconfigure → Photo management**.

## 📥 Installation

[Quick start: installation, card, commands and photos](docs/installation.md)

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Mins95&repository=WelcomeEye&category=integration)

1. Add `https://github.com/Mins95/WelcomeEye` as a custom **Integration** repository in HACS, then download **0.4.4**.
2. Restart Home Assistant and fully reload the browser or Companion app frontend.
3. Go to **Settings → Devices & services → Add integration → Philips WelcomeEye**.
4. For Connect V1/Connect 2 R001, enter the intercom's IPv4 address, username (normally `admin`) and local opening code. This is **not the cloud account password**.

For Connect 3, select its dedicated setup, use the local connection password and configure TLS trust as described in the [Connect 3 guide](docs/connect3-audio-controls.md). For R002, use the [R002 guide](docs/r002-investigation.md). Keep existing entries and credentials when upgrading.

**0.4.4:** update through HACS, restart HA and reload the dashboard. Existing connections, permissions and entity IDs are retained. [Setup](docs/connect3-auto-tls.md) · [0.4.4 notes](docs/release-044.md).

For manual installation, extract [welcomeeye_local.zip](https://github.com/Mins95/WelcomeEye/releases/download/v0.4.4/welcomeeye_local.zip) into `config/custom_components/welcomeeye_local/`, then restart HA.

## 🎙️ Camera card and controls

Add a **WelcomeEye — Interphone** card, or use a Manual card with your actual camera entity:

```yaml
type: custom:welcomeeye-card
entity: camera.welcomeeye_connect_2
```

<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/welcomeeye-intercom-connect2.png" alt="WelcomeEye card with video, sound, microphone, strike and gate controls" width="515">
</p>

The card provides live WebRTC video, sound, microphone on/off, strike, gate and **Photo** where supported. Enable **Second outdoor panel** to select both sources in one card. Rename the two entrances, strikes and gates in the graphical card editor. Choose each required opening under **Reconfigure → Opening commands**; enabled buttons retain their physical target under both camera views. These controls are in this custom card; HA's standard camera player is separate. Close the viewer with its cross to release it.

**Dashboard resource:** it is registered automatically when resources are managed through HA's interface. If the card reports a configuration error, check **Settings → Dashboards → Resources**, add or update the following **JavaScript module**, then fully reload the frontend:

```text
/welcomeeye_local/welcomeeye-card.js?v=0.4.4
```

YAML-managed resources require this entry manually. Edit an existing WelcomeEye resource rather than adding a duplicate. Open HA over HTTPS and grant microphone permission, including in the Companion app.

## 🔔 Doorbell and visitor photos

**Connect 2 R001:** local detection starts automatically.

**Connect V1:** enable **Enable V1 cloud doorbell notifications** through the integration's **Reconfigure** form. It is OFF by default and does not request your Philips cloud account login. HA registers its own notification receiver for this device. Internet access and the vendor/FCM services are required. [Setup and privacy](docs/v1-cloud-doorbell.md).

**Connect 3 and R002:** configure **Cloud doorbell notifications** in Reconfigure with the official app account. Choose the correct account device; separate sensors identify each configured entrance. The first real delivery and channel mapping still need a field check. [Setup and test](docs/qv-cloud-doorbell.md).

The separate native Connect 3 listening action remains an advanced, voluntary diagnostic. It has not established a local bell event and is not required for the cloud route. [Protocol findings](docs/connect3-native-alarm-evidence.md).

On supported entries, **Capture sur sonnerie / Ring image capture** controls automatic photos. It defaults ON unless an OFF preference was saved; on V1 it is available only when cloud notifications are enabled. Remember the ringing interruption described above.

| Entity / event | Purpose |
| --- | --- |
| 🔔 `Sonnette` | Five-second HA state pulse after a detected ring; this display duration is separate from the monitor interruption |
| ⚡ `welcomeeye_local.ring` | Immediate detected-ring event |
| 📸 `image.<device>_last_ring` | Latest successful automatic photo |
| ⚡ `welcomeeye_local.ring_image` | Event after a new ring image is ready |
| 📷 `image.<device>_last_snapshot` | Latest successful manual photo, separate from the ring image |
| 🚪 Strike / gate buttons | One opening command per deliberate action, without automatic replay |

Use the card's **Photo** button or `welcomeeye_local.capture_snapshot` for a manual image. Successful saved photos appear in **Media → WelcomeEye**. Storage is private and authenticated; the integration does not publish them in `/config/www`. Use the admin sidebar **Photos WelcomeEye** to select and delete photos by selection, date or device, and remove empty folders, after confirmation. **Reconfigure → Photo management** offers unlimited retention (default), 7, 30 or 90 days. See [photos, actions and automation examples](docs/captures.md).

## 🛠️ Troubleshooting and ongoing work

- **No microphone button:** use the custom WelcomeEye card, check its resource and fully reload the frontend.
- **Black video / connection error:** close other players and download fresh integration diagnostics before reporting the model, firmware and HA version.
- **CRC32C:** the native backend correction is included. Update through HACS and restart HA. [Technical details](tools/crc32c/AIORTC-DERIVATIVE.md).
- **Current research:** Connect 3/R002 doorbell and panel mapping, native monitor-photo retrieval and capture without interrupting the monitor call.

Diagnostics omit credentials, UID, private IP, raw media/alarm content, FCM tokens, SDP and ICE addresses. Review explicit diagnostic action responses before sharing them publicly. See [security and privacy](SECURITY.md).

[Documentation](docs/README.md) · [0.4.4 notes and rollback](docs/release-044.md) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/Mins95/WelcomeEye/issues)

## 🤝 Community

Community maintained; not affiliated with Philips, Avidsen, Home Assistant or HACS. Thanks to Carter-13, tinymop21, dirksleegers-web, Yohan J. and everyone contributing hardware feedback. Distributed under the [MIT License](LICENSE).

<p align="center">
  <a href="https://ko-fi.com/mins95"><img src="https://img.shields.io/badge/Support%20on%20Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white" alt="Support on Ko-fi"></a>
</p>
