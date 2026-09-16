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
  Live video, doorbell events and door/gate control — directly over your LAN, without the vendor cloud at runtime.
</p>

---

> [!WARNING]
> **0.4.1 — stable release.** WelcomeEye Connect 2 remains supported, including microphone and local doorbell. On Connect V1 / DES9900VDP, live video and the physical door strike are now confirmed by the tester. V1 gate and microphone operation remain unverified; its local doorbell remains disabled.

> [!NOTE]
> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant or HACS.

## ✨ Features

### Current release — `0.4.1`

This release promotes the tested beta.4 implementation, fixing V1 startup disconnections and frozen JPEG/video processing. **V1 video and door strike operation are confirmed by the tester.** The bundled **WelcomeEye — Interphone** dashboard card provides WebRTC video, speaker audio, microphone on/off, strike and gate buttons. **Connect 2 microphone operation is confirmed; V1 microphone and gate operation still require hardware validation.**

Install **0.4.1** as the stable version in HACS, or download [welcomeeye_local.zip](https://github.com/Mins95/WelcomeEye/releases/download/v0.4.1/welcomeeye_local.zip). Restart Home Assistant, then **add the dashboard JavaScript resource as described under Intercom card configuration below**. Reload the app and add the WelcomeEye card, selecting your camera. It also accepts:

```yaml
type: custom:welcomeeye-card
entity: camera.your_welcomeeye
```

The JavaScript file is bundled with the integration, but **register it explicitly in dashboard resources** to avoid `Custom element doesn't exist: welcomeeye-card` on a fresh browser session. Reload the Companion app frontend after upgrading. Open the video, then enable the microphone explicitly. **For microphone use, open Home Assistant over HTTPS with a trusted certificate and grant microphone permission. A local HTTP address blocks microphone access, including in the Companion app dashboard.** Check that the app does not switch to an internal HTTP URL on home Wi-Fi. Closing the card or putting the app in the background stops the microphone and releases that viewer.

The regular HA camera/HLS path remains available. The new controls live in the bundled card, not in Home Assistant's built-in camera dialog. The new card uses WebRTC and can require working ICE/TURN on restrictive networks; the existing HLS compatibility does not make two-way audio work through HLS.

See [intercom instructions and native protocol evidence](docs/intercom-beta1.md) for installation, verification and remaining limitations. The published RC2 history below remains applicable to the existing HLS camera.

- **Local communication** — the intercom is contacted directly on your LAN.
- **Live H.264 video + G.711 audio** — exposed through Home Assistant Stream/HLS.
- **Doorbell detection** — supported locally on **WelcomeEye Connect 2**.
- **Door & gate control** — dedicated Home Assistant buttons.
- **On-demand media sessions** — the video session is opened only while required.
- **Passive snapshots** — the latest decoded frame can be exposed as a still image.
- **Privacy-safe diagnostics** — no password, UID, private IP, raw media, SDP or TURN credential export.
- **HACS-ready releases** — HACS downloads the dedicated `welcomeeye_local.zip` release asset.
- **No TURN server required for RC2** — remote Home Assistant viewing uses the Stream/HLS path validated on a restrictive enterprise Wi-Fi network.

---

## 📦 Supported devices

| Device | Video / audio | Door strike / gate | Doorbell | Status |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2** | ✅ Validated | ✅ Validated | ✅ Local detection | **Stable**, including microphone |
| **WelcomeEye Connect V1 / DES9900VDP** | ✅ Live video confirmed | ✅ Strike confirmed; gate unverified | ⏸️ Standby | Video and strike validated; microphone unverified |

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

After installing 0.4.1 and restarting Home Assistant, **add the resource before adding the card**:

1. Open your dashboard → **Edit dashboard → ⋮ → Manage resources** (French: **Modifier le tableau de bord → ⋮ → Gérer les ressources**). You can also use **Settings → Dashboards → ⋮ → Resources**. If Resources is hidden, enable Advanced mode in your HA profile.
2. Choose **Add resource / Ajouter une ressource** and enter:

   ```text
   /welcomeeye_local/welcomeeye-card.js?v=0.4.1
   ```

3. Select **JavaScript module / Module JavaScript**, then **Create / Créer**. If a WelcomeEye resource already exists, edit its URL instead of adding a duplicate.
4. Fully reload the browser page or close and reopen the Companion app frontend. After a future update, change the `v=` value to the installed integration version and reload again.
5. On your dashboard, choose **Edit dashboard → Add card → WelcomeEye — Interphone**, then select your camera.

**En français : la ressource ci-dessus doit être ajoutée au tableau de bord en tant que Module JavaScript.** Si la carte affiche « Custom element doesn't exist: welcomeeye-card », vérifier cette ressource puis recharger complètement l’interface.

For a manual card, use this working Connect 2 configuration (adapt the entity if yours has a different name):

```yaml
type: custom:welcomeeye-card
entity: camera.welcomeeye_connect_2
```

<p align="center">
  <img src="https://raw.githubusercontent.com/Mins95/WelcomeEye/main/images/welcomeeye-intercom-connect2.png" alt="WelcomeEye Connect 2 live intercom card with speaker, microphone, strike and gate controls" width="515">
</p>

**Connect 2 microphone confirmed working on real hardware (2026-09-15).** The screenshot shows the microphone switched off at the time of capture. Connect V1 microphone validation remains pending.

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

The V1 uses a different legacy LT protocol. Live video is validated on real hardware using the vendor-app profile **channel 16 / stream 1 / mode 2**.

Door/gate commands are routed through that active media session and remain strictly single-shot. The software path is validated, but **physical relay actuation still needs confirmation on real V1 hardware**.

The V1 doorbell entity remains unavailable while a reliable local event path is unresolved. The integration does not introduce vendor-cloud runtime communication as a workaround.

For detailed V1 framing, H.264 recovery, Stop AV/session-stop behavior and validation notes, see [docs/README.md](docs/README.md).

---

## 🌐 Video transport

RC2 exposes the camera through Home Assistant's **Stream/HLS** path using the integration's existing local MPEG-TS source.

This design was selected after field testing showed that native WebRTC could remain blocked by restrictive network ICE/firewall policies even while WelcomeEye media itself was healthy. Stream/HLS uses the normal Home Assistant HTTP path and proved more compatible in that environment.

The internal MPEG-TS proxy remains bound to `127.0.0.1` with a random path. It is consumed by Home Assistant and is not exposed as a raw LAN service.

Initial playback may take a few seconds to buffer before stabilizing, and latency can be higher than native WebRTC.

---

## ⚠️ Known limitations

- **Connect 2 is supported; V1 video and physical strike are tester-confirmed. V1 microphone, gate and local doorbell are not confirmed.**
- Stream/HLS startup may need a few seconds before playback stabilizes.
- Native WelcomeEye WebRTC code is retained but intentionally not advertised in RC2.
- WelcomeEye Connect V1 doorbell detection is currently disabled / on standby.
- V1 gate control still needs **physical relay validation on real hardware**; the door strike has been confirmed by the tester.
- A TLV 506 `result=1` acknowledgement confirms the protocol reply only; it is not treated as proof that a physical relay moved.
- V1 busy-state clearance after session teardown still requires real-hardware validation.
- V1 fragmented-video reassembly for TLVs 103/106/107/108 is not implemented yet.
- Microphone / two-way audio is **confirmed on Connect 2** since `0.4.0-beta.1`, promoted unchanged to `0.4.0`; Connect V1 remains unverified.
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

The beta 8 stabilization candidate was separately validated with **135 tests plus 3 subtests** on Python 3.12.14 and 3.14.7 before publication. The detailed evidence and historical stress-test matrix are preserved in the technical documentation rather than shipping a test tree in the public RC repository.

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
