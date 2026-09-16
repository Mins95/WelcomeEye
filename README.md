<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/header.svg" alt="Philips WelcomeEye for Home Assistant" width="100%">
</p>

<h1 align="center">Philips WelcomeEye for Home Assistant</h1>

<p align="center">
  <a href="https://www.home-assistant.io/"><img src="https://img.shields.io/badge/Home%20Assistant-Integration-41BDF5?style=for-the-badge&logo=home-assistant&logoColor=white" alt="Home Assistant"></a>
  <a href="https://github.com/hacs/integration"><img src="https://img.shields.io/badge/HACS-Custom-orange.svg?style=for-the-badge" alt="HACS"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/v/release/Mins95/WelcomeEye?display_name=tag&style=for-the-badge&label=Stable%20release" alt="Latest stable release"></a>
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
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/v/release/Mins95/WelcomeEye?display_name=tag&label=Stable%20release&color=0080ff" alt="Latest stable release"></a>
  <a href="https://github.com/Mins95/WelcomeEye/releases/latest"><img src="https://img.shields.io/github/release-date/Mins95/WelcomeEye?display_date=published_at&label=Stable%20release%20date&color=0080ff" alt="Stable release date"></a>
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
> **0.4.1 — stable release.** WelcomeEye Connect 2 and WelcomeEye Connect V1 / DES9900VDP have hardware-validated live video and two-way audio. Connect 2 door strike, gate and local doorbell are validated. On V1, the physical door strike is validated; gate validation is still pending and the local doorbell remains disabled.

> [!NOTE]
> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant or HACS.

## ✨ Features

### Prerelease — `0.4.2-beta.1`

**Automatic dashboard resource registration.** The integration now creates the WelcomeEye module in the dashboard resource list and updates its URL when the integration version changes. An existing manual entry is reused; duplicates of this integration's relative card URL are removed. Other resources are preserved.

Select **0.4.2-beta.1** in HACS prerelease versions, restart Home Assistant, then fully reload the browser or Companion app frontend. **No manual resource addition is needed when resources are managed through the HA interface.** If resources are managed in YAML, keep the manual configuration. Version **0.4.1 remains stable**; this beta does not change video, microphone, doorbell or physical-command behavior.

### Current release — `0.4.1`

This release promotes the tested beta.4 implementation, fixing V1 startup disconnections and frozen JPEG/video processing. **V1 live video, two-way audio / microphone and physical door strike are now hardware-confirmed.** The bundled **WelcomeEye — Interphone** dashboard card provides WebRTC video, speaker audio, microphone on/off, strike and gate buttons. Connect 2 live video, two-way audio, door strike, gate and local doorbell are also hardware-validated.

Install **0.4.1** as the stable version in HACS, or download [welcomeeye_local.zip](https://github.com/Mins95/WelcomeEye/releases/download/v0.4.1/welcomeeye_local.zip). Restart Home Assistant, then **add the dashboard JavaScript resource as described under Intercom card configuration below**. Reload the app and add the WelcomeEye card, selecting your camera. It also accepts:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

The JavaScript file is bundled with the integration, but **register it explicitly in dashboard resources** to avoid `Custom element doesn't exist: welcomeeye-card` on a fresh browser session. Reload the Companion app frontend after upgrading. Open the video, then enable the microphone explicitly. **For microphone use, open Home Assistant over HTTPS with a trusted certificate and grant microphone permission. A local HTTP address blocks microphone access, including in the Companion app dashboard.** Check that the app does not switch to an internal HTTP URL on home Wi-Fi. Closing the card or putting the app in the background stops the microphone and releases that viewer.

The regular HA camera/HLS path remains available. The new controls live in the bundled card, not in Home Assistant's built-in camera dialog. The new card uses WebRTC and can require working ICE/TURN on restrictive networks; the existing HLS compatibility does not make two-way audio work through HLS.

See [intercom instructions and native protocol evidence](docs/intercom-beta1.md) for installation, verification and remaining limitations.

- **Local communication** — the intercom is contacted directly on your LAN.
- **Live H.264 video + G.711 audio** — exposed through Home Assistant Stream/HLS.
- **Two-way audio / microphone** — hardware-validated on Connect 2 and Connect V1 / DES9900VDP.
- **Doorbell detection** — supported locally on **WelcomeEye Connect 2**.
- **Door & gate control** — dedicated Home Assistant buttons.
- **On-demand media sessions** — the video session is opened only while required.
- **Passive snapshots** — the latest decoded frame can be exposed as a still image.
- **Privacy-safe diagnostics** — no password, UID, private IP, raw media, SDP or TURN credential export.
- **HACS-ready releases** — HACS downloads the dedicated `welcomeeye_local.zip` release asset.
- **Stream/HLS compatibility path** — the standard camera can use Home Assistant's HTTP stream path on restrictive networks without requiring an external relay or additional container.

---

## 📦 Supported devices

| Device | Video / audio in/out | Door strike / gate | Doorbell | Status |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2** | ✅ Validated | ✅ Strike + gate validated | ✅ Local detection | **Validated**, including two-way audio |
| **WelcomeEye Connect V1 / DES9900VDP** | ✅ Validated | ✅ Strike + gate validated | ⏸️ Standby | **Video + audio in/out validated**; local doorbell pending |

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

## 🎙️ Intercom card configuration — `0.4.1`

**0.4.2-beta.1:** the module is added and updated automatically in **Manage resources**, reusing an existing manual entry. Restart HA, fully reload the frontend, then add the card to your dashboard. YAML-managed resources still need manual configuration, using `v=0.4.2-beta.1`.

**For stable 0.4.1**, or as a fallback if automatic registration fails, **add the resource before adding the card** (use the installed version in `v=`):

1. Open your dashboard → **Edit dashboard → ⋮ → Manage resources**. You can also use **Settings → Dashboards → ⋮ → Resources**. If Resources is hidden, enable Advanced mode in your HA profile.
2. Choose **Add resource / Ajouter une ressource** and enter:

   ```text
   /welcomeeye_local/welcomeeye-card.js?v=0.4.1
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

---

## 🎮 Home Assistant entities

Depending on the device model and current validation status, the integration exposes:

| Entity | Purpose |
| --- | --- |
| **Camera** | Live WelcomeEye video through Home Assistant Stream/HLS |
| **Sonnette** | Local ring state on supported Connect 2 hardware |
| **Open output 1** | Door strike command |
| **Open output 2** | Gate command |
| **Session vidéo** | Diagnostic connectivity/session state |

Output commands are **single-shot**: once a physical TLV 505 may have been sent, the integration never automatically retries that action.

---

## 🔔 Doorbell and output controls

### WelcomeEye Connect 2

Local doorbell detection, door strike control and gate control are validated and keep the existing protocol behavior unchanged.

### WelcomeEye Connect V1 / DES9900VDP

The V1 uses a different legacy LT protocol. Live video and two-way audio are hardware-validated on real hardware using the vendor-app media profile **channel 16 / stream 1 / mode 2**. The physical door strike is also confirmed by the tester.

Door/gate commands are routed through that active media session and remain strictly single-shot. **Gate actuation remains to be physically validated on V1.**

The V1 doorbell entity remains unavailable while a reliable local event path is unresolved. The integration does not introduce vendor-cloud runtime communication as a workaround.

For detailed V1 framing, H.264 recovery, Stop AV/session-stop behavior and validation notes, see [docs/README.md](docs/README.md).

---

## 🌐 Video transport

The standard camera exposes video through Home Assistant's **Stream/HLS** path using the integration's existing local MPEG-TS source.

This design was selected after field testing showed that native WebRTC could remain blocked by restrictive network ICE/firewall policies even while WelcomeEye media itself was healthy. Stream/HLS uses the normal Home Assistant HTTP path and proved more compatible in that environment.

The bundled **WelcomeEye — Interphone** card uses WebRTC for its interactive video/audio session and microphone support. On restrictive networks, that interactive path can require suitable ICE/TURN connectivity.

The internal MPEG-TS proxy remains bound to `127.0.0.1` with a random path. It is consumed by Home Assistant and is not exposed as a raw LAN service.

Initial Stream/HLS playback may take a few seconds to buffer before stabilizing, and latency can be higher than WebRTC.

---

## ⚠️ Known limitations

- WelcomeEye Connect V1 local doorbell detection is currently disabled / on standby.
- V1 gate control still needs **physical relay validation on real hardware**; the door strike has been confirmed by the tester.
- A TLV 506 `result=1` acknowledgement confirms the protocol reply only; it is not treated as proof that a physical relay moved.
- V1 busy-state clearance after session teardown still requires real-hardware validation.
- V1 fragmented-video reassembly for TLVs 103/106/107/108 is not implemented yet.
- Two-way audio / microphone is **hardware-confirmed on Connect 2 and Connect V1 / DES9900VDP**; V1 validation applies from `0.4.1`.
- A snapshot does not wake the camera on its own; no still may exist until a live stream has produced a frame.
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
- **HACS** repository validation;
- Home Assistant **Hassfest** validation.

The beta 8 stabilization candidate was separately validated with **135 tests plus 3 subtests** on Python 3.12.14 and 3.14.7 before publication. The detailed evidence and historical stress-test matrix are preserved in the technical documentation rather than shipping a test tree in the public repository.

Useful technical references:

- [Current technical notes](docs/README.md)
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
