<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/header.svg" alt="Philips WelcomeEye for Home Assistant" width="100%">
</p>

<h1 align="center">Philips WelcomeEye for Home Assistant</h1>

<p align="center">
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/v/release/Mins95/WelcomeEye?display_name=tag&style=for-the-badge&label=Stable%20release&cacheSeconds=300&release=0.4.2" alt="Latest stable release"></a>
</p>

<p align="center">
  <a href="LICENSE"><img src="https://img.shields.io/github/license/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="License"></a>
  <a href="https://github.com/Mins95/WelcomeEye/commits/main"><img src="https://img.shields.io/github/last-commit/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Last Commit"></a>
  <a href="https://github.com/Mins95/WelcomeEye"><img src="https://img.shields.io/github/languages/top/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Top Language"></a>
  <a href="https://github.com/Mins95/WelcomeEye"><img src="https://img.shields.io/github/languages/count/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Language Count"></a>
  <a href="https://github.com/Mins95/WelcomeEye/stargazers"><img src="https://img.shields.io/github/stars/Mins95/WelcomeEye?style=flat-square&color=0080ff" alt="Stars"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases"><img src="https://img.shields.io/github/downloads/Mins95/WelcomeEye/total?style=flat-square&label=Downloads&color=0080ff" alt="Downloads"></a>
</p>

<p align="center">
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/v/release/Mins95/WelcomeEye?display_name=tag&label=Stable%20release&color=0080ff&cacheSeconds=300&release=0.4.2" alt="Latest stable release"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/release-date/Mins95/WelcomeEye?display_date=published_at&label=Stable%20release%20date&color=0080ff&cacheSeconds=300&release=0.4.2" alt="Stable release date"></a>
  <a href="https://github.com/Mins95/WelcomeEye/issues?q=is%3Aissue%20state%3Aopen%20label%3Abug"><img src="https://img.shields.io/github/issues-search/Mins95/WelcomeEye?query=label%3Abug%20is%3Aopen&label=Open%20Bugs&color=0080ff" alt="Open Bugs"></a>
  <a href="https://github.com/Mins95/WelcomeEye/pulls"><img src="https://img.shields.io/github/issues-pr/Mins95/WelcomeEye?color=0080ff" alt="Open PRs"></a>
</p>

<p align="center">
  <a href="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml"><img src="https://github.com/Mins95/WelcomeEye/actions/workflows/validate.yml/badge.svg" alt="Validation"></a>
  <a href="https://github.com/Mins95/WelcomeEye/actions/workflows/release.yml"><img src="https://github.com/Mins95/WelcomeEye/actions/workflows/release.yml/badge.svg" alt="Release workflow"></a>
</p>

<p align="center">
 <a href="https://ko-fi.com/mins95"><img src="https://img.shields.io/badge/Buy%20me%20a%20coffee-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white" alt="Buy me a coffee"></a>
</p> 
<p align="center">
  A local Home Assistant integration for <b>Philips WelcomeEye</b> video intercoms.<br>
  Live video, two-way audio, doorbell events and door/gate control — directly over your LAN, without the vendor cloud at runtime.
</p>

---

> [!WARNING]
> **0.4.2 is the stable release.** WelcomeEye Connect 2 and Connect V1 / DES9900VDP have hardware-validated live video, two-way audio, door strike and gate control. Local doorbell detection is validated on Connect 2. **V1 local doorbell remains unsupported** and its unsuccessful listener is disabled. Automatic ring photos remain experimental and opt-in.

> [!NOTE]
> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant or HACS.

## ✨ Features

### Current release — `0.4.2`

**Stable promotion of the corrected beta.4, with the same device behavior.** This version includes fresh captures, authenticated Media storage and frontend cleanup. The owner confirmed a successful automatic ring image and saved JPEG after enabling the capture switch. See the [promotion evidence and remaining limits](docs/stable-042.md).

**Automatic ring photos are now opt-in.** The persistent `switch.<device>_ring_image_capture` starts **OFF**, including upgrades without a saved preference. OFF leaves immediate ring events and the five-second `Sonnette` pulse working on Connect 2 and schedules no photo. ON starts at most one acquisition at **T+4 seconds**, reuses an active media session and releases a temporary lease afterward. No photo retry loop is added. Successful captures update `image.<device>_last_ring` and attempt an authenticated HA Media save.

**CRC32C native correction included in the republished 0.4.2.** A hash-pinned aiortc 1.15.0 derivative replaces only its CRC dependency with the native `crc32c` backend. HA installs the native package normally; no warning filter or embedded binary is used. Validated on x86_64/aarch64 musl and glibc, and in an isolated QNAP container. **If 0.4.2 is already installed, Redownload it in HACS and restart HA.** See [scope, evidence and maintenance](tools/crc32c/AIORTC-DERIVATIVE.md).

**Manual photos and card controls.** `welcomeeye_local.capture_snapshot` targets a WelcomeEye camera and updates the separate `image.<device>_last_snapshot`. The card's **Photo** button saves a fresh image while preserving live video, sound and an active microphone. Automatic capture is configured separately with the **Ring image capture** switch in Home Assistant. The five controls stay on one row, including on narrow displays. Photos are saved in the **WelcomeEye** folder inside Home Assistant's local Media directory. See [capture setup, service examples and Media storage](docs/captures.md).

**Automatic ring snapshots remain experimental.** Two consecutive idle-ring trials preserved the monitor photo while producing a fresh HA image. A missing native monitor photo after opening/closing HA video was also reproduced without automatic snapshots and remains unresolved. The integration takes its own new local image; it does not retrieve the monitor's stored photo. See the [historical hardware results](docs/ring-image-delayed-candidate.md).

The automatic dashboard resource registration introduced in 0.4.2-beta.1 remains included: the integration creates or updates the WelcomeEye module in dashboard resources, reuses an existing manual entry and removes only duplicates of this integration's relative card URL.

Install **0.4.2** in HACS, restart Home Assistant, then fully reload the browser or Companion app frontend. **No manual resource addition is needed when resources are managed through the HA interface.** YAML-managed resources must use the installed version in their module URL.


### Live video and intercom controls

The bundled **WelcomeEye — Interphone** card provides WebRTC video, speaker audio, microphone on/off, strike, gate and Photo controls. V1 startup and video-processing fixes from 0.4.1 remain included.

Download [welcomeeye_local.zip](https://github.com/Mins95/WelcomeEye/releases/download/v0.4.2/welcomeeye_local.zip) for manual installation. After restarting HA and reloading the frontend, add the WelcomeEye card and select your camera. It also accepts:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

The JavaScript module is bundled and registered automatically for UI-managed dashboard resources. See the manual resource configuration below for YAML dashboards or if registration fails. **For microphone use, open Home Assistant over HTTPS with a trusted certificate and grant microphone permission. A local HTTP address blocks microphone access, including in the Companion app dashboard.** Check that the app does not switch to an internal HTTP URL on home Wi-Fi. Closing the card or putting the app in the background stops the microphone and releases that viewer.

The regular HA camera/HLS path remains available. The new controls live in the bundled card, not in Home Assistant's built-in camera dialog. The new card uses WebRTC and can require working ICE/TURN on restrictive networks; the existing HLS compatibility does not make two-way audio work through HLS.

See [intercom instructions and native protocol evidence](docs/intercom-beta1.md) for installation, verification and remaining limitations.

- **Local communication** — the intercom is contacted directly on your LAN.
- **Live H.264 video + G.711 audio** — exposed through Home Assistant Stream/HLS.
- **Two-way audio / microphone** — hardware-validated on Connect 2 and Connect V1 / DES9900VDP.
- **Doorbell detection** — validated locally on **WelcomeEye Connect 2**; **not currently supported on Connect V1 / DES9900VDP**.
- **Door & gate control** — dedicated Home Assistant buttons.
- **On-demand media sessions** — the video session is opened only while required.
- **Fresh snapshots** — a still request reuses the shared media worker and waits for a newly decoded frame; it never answers a new request with an older cached JPEG.
- **Privacy-safe diagnostics** — no password, UID, private IP, raw media, SDP or TURN credential export.
- **HACS-ready releases** — HACS downloads the dedicated `welcomeeye_local.zip` release asset.
- **Stream/HLS compatibility path** — the standard camera can use Home Assistant's HTTP stream path on restrictive networks without requiring an external relay or additional container.

---

## 📦 Supported devices

| Device | Video / audio in/out | Door strike | Gate | Doorbell | Status |
| --- | --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2, validated firmware (V401.R001.XXX)** | ✅ Validated | ✅ Validated | ✅ Validated | ✅ Local detection | **Validated baseline** |
| **WelcomeEye Connect V1 / DES9900VDP** | ✅ Validated | ✅ Validated | ✅ Validated | ❌ Not supported / no local ring detected | Media, strike and gate validated |
| **Connect 2 / DES9901VDP, V401.R002.A302.00.G0058.B002 without UDP 1500** | ❌ Not supported | Not validated | Not validated | Not validated | Separate investigation |

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

---

## 📥 Installation

### HACS — recommended

[![Open your Home Assistant instance and open this repository in HACS](https://my.home-assistant.io/badges/hacs_repository.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Mins95&repository=WelcomeEye&category=integration)

1. Click the button above, or open **HACS → Integrations → ⋮ → Custom repositories**.
2. Add `https://github.com/Mins95/WelcomeEye` as **Integration**.
3. Search for **Philips WelcomeEye** and click **Download**.
4. Restart Home Assistant.
5. Go to **Settings → Devices & services → Add integration**.
6. Search for **Philips WelcomeEye**.

### Manual installation

1. Download the latest `welcomeeye_local.zip` from [Releases](https://github.com/Mins95/WelcomeEye/releases).
2. Extract it to:

   ```text
   config/custom_components/welcomeeye_local/
   ```

3. Restart Home Assistant.
4. Add the integration from **Settings → Devices & services**.

> [!TIP]
> HACS is recommended because it provides update notifications and handles future upgrades automatically.

---

## ➕ Setup

[![Open your Home Assistant instance and start setting up Philips WelcomeEye](https://my.home-assistant.io/badges/config_flow_start.svg)](https://my.home-assistant.io/redirect/config_flow_start/?domain=welcomeeye_local)

The setup form asks for:

- the intercom **IPv4 address**;
- the username, normally `admin`;
- the **local unlock code used to open the gate/door from the WelcomeEye app**.

> [!IMPORTANT]
> The requested password/code is **not your Philips/WelcomeEye cloud account password**. Use the **local door/gate unlock code** configured for the intercom.

A DHCP reservation or static lease is recommended so the intercom keeps the same IPv4 address.

---

## 🎙️ Intercom card configuration — `0.4.2`

**Automatic registration:** the module is added and updated in **Manage resources**, reusing an existing manual entry. Restart HA, fully reload the frontend, then add the card to your dashboard. YAML-managed resources still need manual configuration with `v=0.4.2`.

**For YAML-managed resources, or if automatic registration fails**, configure the resource before adding the card. For a manual entry through the interface:

1. Open your dashboard → **Edit dashboard → ⋮ → Manage resources**. You can also use **Settings → Dashboards → ⋮ → Resources**. If Resources is hidden, enable Advanced mode in your HA profile.
2. Choose **Add resource / Ajouter une ressource** and enter:

   ```text
   /welcomeeye_local/welcomeeye-card.js?v=0.4.2
   ```

3. Select **JavaScript module / Module JavaScript**, then **Create / Créer**. If a WelcomeEye resource already exists, edit its URL instead of adding a duplicate.
4. Fully reload the browser page or close and reopen the Companion app frontend. After a future update, change the `v=` value to the installed integration version and reload again.
5. On your dashboard, choose **Edit dashboard → Add card → WelcomeEye — Interphone**, then select your camera.

For a manual card, use this configuration (adapt the entity if yours has a different name):

```yaml
type: custom:welcomeeye-card
entity: camera.welcomeeye_connect_2
```

<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/welcomeeye-intercom-connect2.png" alt="WelcomeEye Connect 2 live intercom card with speaker, microphone, strike and gate controls" width="515">
</p>

**Two-way audio / microphone is hardware-confirmed on both Connect 2 and Connect V1 / DES9900VDP.** On V1, this validation applies from version **0.4.1**. The screenshot above shows the Connect 2 microphone switched off at the time of capture.

Open the video, then use the microphone button to speak and press it again to stop. The **Gâche** and **Portail** buttons call the existing output services. These controls are available in this card; the standard Home Assistant camera dialog remains unchanged.

**Photo** also works with the viewer closed and uses the same fresh-capture backend. Automatic capture is controlled by the separate **Ring image capture** HA switch. A Media write failure displays a capture-with-save-error message rather than claiming “Photo enregistrée”. See [captures](docs/captures.md).

---

## 🎮 Home Assistant entities

Depending on the device model and current validation status, the integration exposes:

| Entity | Purpose |
| --- | --- |
| **Camera** | Live WelcomeEye video through Home Assistant Stream/HLS |
| **Last ring** | Latest successful automatic photo in memory; `image.<device>_last_ring`; Media reference when saved |
| **Last snapshot** | Latest successful manual photo in memory; `image.<device>_last_snapshot`; separate from the visitor photo |
| **Ring image capture / Capture sur sonnerie** | Persistent opt-in switch for automatic photos, OFF by default |
| **Sonnette** | Local ring state validated on Connect 2; unsupported on V1. A distinct decoded ring remains active for 5 seconds. |
| **Open output 1** | Door strike command |
| **Open output 2** | Gate command |
| **Session vidéo** | Diagnostic connectivity/session state |

Output commands are **single-shot**: once a physical TLV 505 may have been sent, the integration never automatically retries that action.

---

## 🔔 Doorbell and output controls

### WelcomeEye Connect 2

Local doorbell detection, door strike control and gate control are validated and keep the existing protocol behavior unchanged. The visible `Sonnette` state is held for 5 seconds after each distinct decoded ring; immediate ring events are unchanged.

### WelcomeEye Connect V1 / DES9900VDP

The V1 uses a different legacy LT protocol. Live video and two-way audio are hardware-validated on real hardware using the vendor-app media profile **channel 16 / stream 1 / mode 2**. The physical door strike and gate are also confirmed by the tester.

Door/gate commands are routed through that active media session and remain strictly single-shot. **V1 gate actuation was confirmed by the tester on 2026-09-24.** This confirmation does not certify every capture or lifecycle scenario.

**❌ Local doorbell is not currently functional.** Several hardware trials authenticated the experimental `0/3/0` listener and observed a stable session, keepalives and TLV traffic, but no usable local ring event. No reliable local doorbell path was identified in the current trials, so no V1 local support is claimed. A cloud path is possible but has not been demonstrated. Version 0.4.2 stops opening this unproductive listener by default; V1 video, microphone, strike and gate keep their existing paths.

For detailed V1 framing, H.264 recovery, Stop AV/session-stop behavior and validation notes, see [docs/README.md](docs/README.md). The [Connect 2-path trial](docs/v1-doorbell-connect2-trial.md) is retained as historical investigation evidence, not a statement of current support.

---

## 🌐 Video transport

The standard camera exposes video through Home Assistant's **Stream/HLS** path using the integration's existing local MPEG-TS source.

This design was selected after field testing showed that native WebRTC could remain blocked by restrictive network ICE/firewall policies even while WelcomeEye media itself was healthy. Stream/HLS uses the normal Home Assistant HTTP path and proved more compatible in that environment.

The bundled **WelcomeEye — Interphone** card uses WebRTC for its interactive video/audio session and microphone support. On restrictive networks, that interactive path can require suitable ICE/TURN connectivity.

The internal MPEG-TS proxy remains bound to `127.0.0.1` with a random path. It is consumed by Home Assistant and is not exposed as a raw LAN service.

Initial Stream/HLS playback may take a few seconds to buffer before stabilizing, and latency can be higher than WebRTC.

---

## ⚠️ Known limitations

- WelcomeEye Connect V1 local doorbell detection is **not supported**: no reliable local ring path was identified in the current hardware trials. A cloud path is possible but unproven.
- Connect 2 / DES9901VDP firmware **V401.R002.A302.00.G0058.B002 without UDP 1500** is unsupported and requires a separate investigation. This beta adds no speculative port 8765 transport or OWSP port probing.
- A TLV 506 `result=1` acknowledgement confirms the protocol reply only; it is not treated as proof that a physical relay moved.
- V1 busy-state clearance after session teardown still requires real-hardware validation.
- V1 fragmented-video reassembly for TLVs 103/106/107/108 is not implemented yet.
- Two-way audio / microphone is **hardware-confirmed on Connect 2 and Connect V1 / DES9900VDP**; V1 validation applies from `0.4.1`.
- A snapshot uses the same on-demand media lease as the live stream. It may take a few seconds while the worker starts, and returns no image if no new frame arrives before the bounded timeout.
- Home Assistant also uses the camera image API for thumbnails: refreshing a thumbnail can temporarily acquire media. Only an enabled capture switch schedules a ring photo at T+4. It does not retrieve the monitor's stored photo. Two idle Connect 2 rings passed in beta.3; after-video native-photo loss remains unresolved. See the [historical snapshot report](docs/ring-image-delayed-candidate.md).
- Saved captures persist in the configured Media directory and have **no automatic retention/deletion policy**. Plan storage and backups; a Container installation needs persistent storage mounted at its Media directory. See [capture storage](docs/captures.md#media-storage-and-retention).
- The native CRC32C packaging correction is prepared and validated in isolated musl environments, but not deployed upstream. The installed Python backend and its warning may remain after this beta update.
- The integration accepts an **IPv4 address**, not a hostname.
- Home Assistant must be able to reach the intercom directly on the LAN.
- Discovery uses UDP port `1500`, followed by the TCP port advertised by the device.

---

## 🔐 Security & privacy

Diagnostics deliberately omit credentials, device UID, private device IP, raw media payloads, alarm payloads, FCM tokens, SDP, ICE candidate values, ICE server URLs, TURN credentials and internal stream URLs.

The integration communicates with the intercom locally and does **not** use the Philips/WelcomeEye vendor cloud API at runtime.

Please do not post passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public GitHub issues.

See [SECURITY.md](SECURITY.md) for more information.

---

## 🧪 Development & validation

The public repository is intentionally kept lean. GitHub Actions currently check:

- Python **3.12 / 3.14** compilation of the integration;
- manifest JSON validity;
- the integration's Python regression suite and dependency-free Node frontend tests;
- **HACS** repository validation;
- Home Assistant **Hassfest** validation.

The regression suite covers captures, persistence, storage, lifecycle and frontend controls. The [beta.4 audit](docs/audit-beta4.md) and [stable promotion report](docs/stable-042.md) distinguish software checks from physical observations. Automated tests never open a physical strike or gate. Earlier V1 listener trials did not establish local ring support.

The beta 8 stabilization candidate was separately validated with **135 tests plus 3 subtests** on Python 3.12.14 and 3.14.7 before publication. The detailed evidence and historical stress-test matrix are preserved in the technical documentation rather than shipping a full historical test tree in the public repository.

Useful technical references:

- [Current technical notes](docs/README.md)
- [Capture setup and private Media storage](docs/captures.md)
- [Beta.4 consolidation audit](docs/audit-beta4.md)
- [V1 doorbell Connect 2-path trial](docs/v1-doorbell-connect2-trial.md)
- [Beta 8 stabilization audit](docs/stabilization-beta8.md)
- [V1 media/control stability](docs/v1-stability-beta7.md)
- [V1 output-control investigation](docs/v1-control-events-beta5.md)
- [V1 doorbell investigation](docs/v1-doorbell-beta6.md)
- [Changelog](CHANGELOG.md)

---

## 🤝 Contributing

Hardware feedback is especially useful. If you have another WelcomeEye model or firmware revision, please open an [issue](https://github.com/Mins95/WelcomeEye/issues) with the model, Home Assistant version and privacy-safe diagnostics.

If the integration is useful to you, a ⭐ on the repository helps other Home Assistant users discover the project.

<p align="center">
  <a href="https://ko-fi.com/mins95"><img src="https://img.shields.io/badge/Support%20the%20project%20on%20Ko--fi-FF5E5B?style=for-the-badge&logo=kofi&logoColor=white" alt="Support on Ko-fi"></a>
</p>

---

## 📜 License

Distributed under the [MIT License](LICENSE).
