<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye** intercoms.

> [!WARNING]
> **Beta — active development.** Features, compatibility, configuration and behavior may change between releases. Please report issues and test results on GitHub.

The integration communicates directly with the intercom on the local network. It does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Features

- UI-based configuration and reauthentication.
- Direct local device authentication.
- On-demand H.264 video and G.711 A-law audio.
- Native Home Assistant WebRTC viewing.
- Remote WebRTC NAT traversal through Home Assistant's ICE/STUN/TURN configuration.
- Passive JPEG snapshot from the most recently decoded active video stream.
- **Doorbell / ring detection** exposed as a `Sonnette` binary sensor and `welcomeeye_local.ring` event.
- Local output controls:
  - **Ouvrir la gâche** — output 1;
  - **Ouvrir le portail** — output 2.
- Automatic media-profile probing when a device announces a stream but does not deliver usable video packets.
- Downloadable privacy-safe diagnostics including media profile, TLV counters and WebRTC state.
- Media connection is opened only while a consumer is actively using the stream.

## Supported hardware

- **WelcomeEye Connect 2** — validated for video/audio and output control.
- **WelcomeEye Connect V1 / DES9900VDP** — output control works on a community test device; beta 9 reproduces the APK LT media startup sequence and adds deep privacy-safe protocol diagnostics.

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## Beta 9 V1 LT compatibility

A deeper review of the supplied WelcomeEye APK identified the dedicated `QvLtPlayerCore` path used by legacy LT devices. The APK maps logical channel 1 to wire channel **16**, stream **1**, mode **2** (`channel + 15, 1, 2`). After authorization succeeds, it immediately calls `sendManuData(TCRequestBean.initTCGetBitStrMode())`, whose manufacturer payload is `01 04 03 00`.

Beta 9 reproduces that read-only post-authentication query on an identified Connect V1 and sends one media-only I-frame request (`01 04 0B 00`) after the V1 announces its 352×288 H.264 format. Once a V1 is recognized, the integration stays on the APK-confirmed 16/1/2 profile instead of cycling speculative channel/mode combinations. Neither request operates the door strike or gate.

The V1 diagnostics now count **all** top-level TLV types, structurally inspect every non-audio payload for H.264, report manufacturer command/subcommand metadata without payload bytes, and record transport framing failures as numeric big-/little-endian length interpretations plus whether the failure immediately followed a keepalive. Raw packet bytes remain excluded. This is intended to make a single tester run sufficient to distinguish a missing LT startup command, an unknown video TLV, or a legacy outer-framing difference.

## Known limitations

- This is a **beta release under active development**.
- Microphone / two-way audio from Home Assistant to the intercom is not implemented yet.
- A snapshot does not wake or open the video session on its own. Until a live stream has produced a frame, the camera may have no still image available.
- Remote WebRTC across restrictive/symmetric NAT may require a TURN relay; STUN alone cannot guarantee connectivity on every network.
- The integration requires an **IPv4 address**; hostnames are intentionally not accepted.
- Home Assistant must be able to reach the intercom directly on the LAN.
- The device is contacted on UDP port `1500` for discovery, then on the TCP port advertised by the device.
- A DHCP reservation/static lease for the intercom is recommended so its configured IPv4 address does not change.

## Installation with HACS

1. Open **HACS** in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add `https://github.com/Mins95/WelcomeEye`.
4. Select **Integration**.
5. Install **Philips WelcomeEye**.
6. Restart Home Assistant.
7. Go to **Settings → Devices & services → Add integration**.
8. Search for **Philips WelcomeEye**.

## Configuration

The setup form asks for the intercom IPv4 address, username (default `admin`) and the device password used by the WelcomeEye app to open the gate/portal.

## Door strike and gate controls

The tested WelcomeEye devices require a media session to be initialized before accepting an output command. If no Home Assistant media session is active, the integration briefly initializes one, sends the requested output command exactly once, waits for acknowledgement, and then releases the session.

On the tested Connect 2, output 1 is the door strike (gâche) and output 2 is the gate (portail). For safety, output commands are never automatically retried.

## Security and privacy

Diagnostics deliberately omit the password, username, device UID, private device IP, media payloads, SDP, ICE candidate values, candidate addresses, ICE server URLs, TURN credentials and internal stream URL.

Opening a WebRTC viewer may contact the STUN/TURN servers configured by Home Assistant. STUN is used only for NAT traversal; when a TURN relay is required, the WebRTC media remains protected by the WebRTC transport encryption.

The internal MPEG-TS proxy listens only on `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

## Development and validation

The repository includes GitHub Actions for HACS repository validation and Home Assistant Hassfest validation. The integration domain is `welcomeeye_local`.

## License

MIT — see [LICENSE](LICENSE).
