<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye** intercoms.

> [!WARNING]
> **Beta — active development.** Compatibility may vary by hardware and firmware. Please report test results and issues on GitHub.

The integration communicates directly with the intercom on the local network and does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Current release

**v0.3.1-beta.1**

This release introduces the current WelcomeEye Connect V1 video candidate based on the native LT/OWSP transport behavior found in the vendor SDK. V1 media reception now preserves partial OWSP payloads across intermediate socket timeouts, applies strict progress/size limits, separates video metadata from complete TLV 100/101 images, and forwards Annex-B H.264 image bytes unchanged to the existing media pipeline.

The candidate passed **71 offline tests**, including real PyAV decoding to JPEG and regression checks for Connect 2 media/audio behavior. **Physical validation of V1 video is still pending.**

See [CHANGELOG.md](CHANGELOG.md) for release history and technical details.

## Features

- UI-based configuration and reauthentication.
- Direct local device authentication.
- On-demand H.264 video and G.711 A-law audio.
- Native Home Assistant WebRTC viewing.
- Remote WebRTC NAT traversal through Home Assistant's ICE/STUN/TURN configuration.
- Passive JPEG snapshot from the most recently decoded active stream.
- Doorbell / ring detection exposed as a **Sonnette** binary sensor and `welcomeeye_local.ring` event.
- Local controls for the **door strike** and **gate**.
- Privacy-safe downloadable diagnostics for media, transport, control, doorbell and WebRTC state.
- Media connections are opened only while required by an active consumer.

## Hardware status

| Device | Video / audio | Door strike / gate | Doorbell | Status |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2** | Validated | Validated | Supported | Main validated platform |
| **WelcomeEye Connect V1 / DES9900VDP** | **v0.3.1-beta.1 candidate — physical video validation pending** | Door strike validated on community hardware; gate path implemented | Implemented; physical validation still pending | Experimental / active testing |

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## WelcomeEye Connect V1

The V1 uses a legacy LT protocol path that differs from Connect 2. The integration reproduces the native Start AV / Stop AV exchange and uses the vendor-app profile **channel 16 / stream 1 / mode 2**.

For video, the current implementation follows the native behavior established from the supplied APK and `libglnkio.so`:

- partial OWSP packet bytes are retained while reception is still progressing;
- a packet is never parsed until its announced payload is complete;
- V1 media reads are bounded to **6 seconds without progress**, **20 seconds total per packet** and **1 MiB maximum**;
- video metadata is handled separately from complete **TLV 100 / 101** I/P images;
- Annex-B H.264 image bytes are passed unchanged to the existing decoder;
- incomplete or structurally invalid packets are rejected rather than reused.

Fragmented V1 video carried through the separate fragment TLVs is **not reassembled yet**. Those packets are counted and ignored until the native ordering/reassembly rules are sufficiently demonstrated.

## Doorbell and output controls

Output commands are sent **at most once per accepted user action** and are never automatically retried.

On V1, the persistent doorbell listener temporarily releases its control session before an explicit door-strike or gate command. The command then uses a one-shot control session, after which the listener reconnects. The existing UID checks and three-second output cooldown remain in place.

## Known limitations

- This is a **beta release under active development**.
- WelcomeEye Connect V1 video still requires physical validation on real hardware for image stability, metadata behavior, audio synchronization and clean session release.
- V1 fragmented-video reassembly is not implemented yet.
- Microphone / two-way audio: **Not validated** on hardware.
- A snapshot does not wake or open the video session on its own. Until a live stream has produced a frame, the camera may have no still image available.
- Remote WebRTC across restrictive or symmetric NAT may require a TURN relay; STUN alone cannot guarantee connectivity on every network.
- The integration requires an **IPv4 address**; hostnames are intentionally not accepted.
- Home Assistant must be able to reach the intercom directly on the LAN.
- The device is contacted on UDP port `1500` for discovery and then on the TCP port advertised by the device.
- A DHCP reservation or static lease for the intercom is recommended.

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

The setup form asks for the intercom IPv4 address, username (default `admin`) and the device password used by the WelcomeEye app.

## Security and privacy

Diagnostics deliberately omit credentials, device UID, private device IP, raw media payloads, SDP, ICE candidate values, ICE server URLs, TURN credentials and internal stream URLs.

Opening a WebRTC viewer may contact the STUN/TURN servers configured by Home Assistant. STUN is used only for NAT traversal; when a TURN relay is required, WebRTC media remains protected by the WebRTC transport encryption.

The internal MPEG-TS proxy listens only on `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

## Development and validation

The repository includes GitHub Actions for HACS repository validation and Home Assistant Hassfest validation. The integration domain is `welcomeeye_local`.

The v0.3.1-beta.1 V1 candidate was validated with **71 offline tests**, including transport edge cases, command/ring coordination, A-law audio, real PyAV H.264 decoding and Connect 2 regression coverage.

## License

MIT — see [LICENSE](LICENSE).
