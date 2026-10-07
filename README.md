<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/header.svg" alt="Philips WelcomeEye for Home Assistant" width="100%">
</p>

<h1 align="center">Philips WelcomeEye for Home Assistant</h1>

<p align="center">
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.3"><img src="https://img.shields.io/badge/Stable-v0.4.3-0080ff?style=for-the-badge" alt="Stable release v0.4.3"></a>
  <a href="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml"><img src="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml/badge.svg?branch=main" alt="Validation"></a>
</p>

<p align="center">
  Live video, sound, microphone, door/gate control and visitor photos in Home Assistant.<br>
  Media and opening commands stay local. Connect V1 doorbell notifications use an optional cloud connection.
</p>

## Compatibility

**0.4.3 is the stable release.** Support depends on the model and firmware:

| Device | Live video / sound | Microphone | Strike / gate | Doorbell | Photos |
| --- | --- | --- | --- | --- | --- |
| **Connect V1 / DES9900VDP** | Validated | Validated | Validated | **Validated via optional cloud**; no local detection | Manual + experimental ring capture |
| **Connect 2 — R001 firmware** | Validated | Validated | Validated | **Validated locally** | Manual + experimental ring capture |
| **Connect 3 — IDS94E6SW** | Validated | Available; physical confirmation pending | Validated | Under investigation | Not available |
| **Connect 2 — R002 firmware** | Experimental trial | Experimental trial | Experimental trial | Under investigation | Not available |

Connect 3 video, sound and both opening commands have been confirmed by testers. Use the **WelcomeEye card** for the microphone and controls. Experimental video and outputs still require explicit activation in configuration. [Connect 3 guide](docs/connect3-audio-controls.md).

**R002 is still under investigation**, particularly firmware `V401.R002.A302.00.G0058.B002`. Discovery and diagnostic tools are available; a complete working intercom is not yet confirmed. [R002 investigation](docs/r002-investigation.md) · [Tester issue](https://github.com/Mins95/WelcomeEye/issues/7).

## Known limitations

> [!WARNING]
> **Automatic photos can stop the intercom's ongoing call/ringing around five seconds after the bell press**, on the indoor monitor and outdoor station. This happens when HA acquires the image through the media session. **Smartphone notifications/ringing through the official Philips app continue.** The photo is retained, but this interruption is not fixed. Automatic ring capture remains experimental; turn **Capture sur sonnerie / Ring image capture** OFF if you prefer to preserve the monitor's full ringing cycle.

- HA takes its **own fresh photo** from T+4 seconds; it does not retrieve the photo stored by the monitor. An existing video session is reused. Opening live video or refreshing a camera thumbnail can also take media during a call, even with automatic capture OFF.
- Connect V1 requires the optional cloud connection for doorbell events. Its video, microphone, photos and opening commands remain local.
- Connect 3 standby doorbell detection and stored-photo retrieval remain under investigation. R002 support is experimental.
- Only one device media session is available at a time. Close the HA player before using Philips; a brief release delay can occur.
- Microphone access requires **HTTPS with a trusted certificate** and browser/app permission. Restrictive networks can also block WebRTC.
- Saved photos have no automatic retention policy. They remain in Home Assistant Media until you delete them.

## Installation

[![Open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Mins95&repository=WelcomeEye&category=integration)

1. Add `https://github.com/Mins95/WelcomeEye` as a custom **Integration** repository in HACS, then download **0.4.3**.
2. Restart Home Assistant and fully reload the browser or Companion app frontend.
3. Go to **Settings → Devices & services → Add integration → Philips WelcomeEye**.
4. For Connect V1/Connect 2 R001, enter the intercom's IPv4 address, username (normally `admin`) and local opening code. This is **not the cloud account password**.

For Connect 3, select its dedicated setup, use the local connection password and configure TLS trust as described in the [Connect 3 guide](docs/connect3-audio-controls.md). For R002, use the [investigation guide](docs/r002-investigation.md). Keep existing entries and credentials when upgrading.

For manual installation, extract [welcomeeye_local.zip](https://github.com/Mins95/WelcomeEye/releases/download/v0.4.3/welcomeeye_local.zip) into `config/custom_components/welcomeeye_local/`, then restart HA.

## Camera card

Add a **WelcomeEye — Interphone** card, or use a Manual card with your actual camera entity:

```yaml
type: custom:welcomeeye-card
entity: camera.welcomeeye_connect_2
```

<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/welcomeeye-intercom-connect2.png" alt="WelcomeEye card with video, sound, microphone, strike and gate controls" width="515">
</p>

The card provides live WebRTC video, sound, microphone on/off, strike, gate and **Photo** where supported. These controls are in this custom card; HA's standard camera player is separate. Close the viewer with its cross to release it.

**Dashboard resource:** it is registered automatically when resources are managed through HA's interface. If the card reports a configuration error, check **Settings → Dashboards → Resources**, add or update the following **JavaScript module**, then fully reload the frontend:

```text
/welcomeeye_local/welcomeeye-card.js?v=0.4.3
```

YAML-managed resources require this entry manually. Edit an existing WelcomeEye resource rather than adding a duplicate. Open HA over HTTPS and grant microphone permission, including in the Companion app.

## Doorbell and photos

**Connect 2 R001:** local detection starts automatically.

**Connect V1:** enable **Connect V1 cloud doorbell notifications (experimental)** through the integration's **Reconfigure** form. It is OFF by default and does not request your Philips cloud account login. HA registers its own notification receiver for this device. Internet access and the vendor/FCM services are required. [Setup and privacy](docs/v1-cloud-doorbell.md).

On supported entries, **Capture sur sonnerie / Ring image capture** controls automatic photos. It defaults ON unless an OFF preference was saved; on V1 it is available only when cloud notifications are enabled. Remember the ringing interruption described above.

| Entity / event | Purpose |
| --- | --- |
| `Sonnette` | Five-second HA state pulse after a detected ring; this display duration is separate from the monitor interruption |
| `welcomeeye_local.ring` | Immediate detected-ring event |
| `image.<device>_last_ring` | Latest successful automatic photo |
| `welcomeeye_local.ring_image` | Event after a new ring image is ready |
| `image.<device>_last_snapshot` | Latest successful manual photo, separate from the ring image |
| Strike / gate buttons | One opening command per deliberate action, without automatic replay |

Use the card's **Photo** button or `welcomeeye_local.capture_snapshot` for a manual image. Successful saved photos appear in **Media → WelcomeEye**. Storage is private and authenticated; the integration does not publish them in `/config/www`. See [photos, actions and automation examples](docs/captures.md).

## Troubleshooting and ongoing work

- **No microphone button:** use the custom WelcomeEye card, check its resource and fully reload the frontend.
- **Black video / connection error:** close other players and download fresh integration diagnostics before reporting the model, firmware and HA version.
- **CRC32C:** the native backend correction is included. Update through HACS and restart HA. [Technical details](tools/crc32c/AIORTC-DERIVATIVE.md).
- **Current research:** Connect 3 doorbell, R002 authentication/media validation, native monitor-photo retrieval and capture without interrupting the monitor call.

Diagnostics omit credentials, UID, private IP, raw media/alarm content, FCM tokens, SDP and ICE addresses. Review explicit diagnostic action responses before sharing them publicly. See [security and privacy](SECURITY.md).

[Documentation](docs/README.md) · [0.4.3 validation and rollback](docs/stable-043.md) · [Changelog](CHANGELOG.md) · [Issues](https://github.com/Mins95/WelcomeEye/issues)

## Community

Community maintained; not affiliated with Philips, Avidsen, Home Assistant or HACS. Thanks to Carter-13, tinymop21, dirksleegers-web and everyone contributing hardware feedback. Distributed under the [MIT License](LICENSE).

<p align="center">
  <a href="https://ko-fi.com/mins95"><img src="https://img.shields.io/badge/Support%20on%20Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white" alt="Support on Ko-fi"></a>
</p>
