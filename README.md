<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye** intercoms.

> [!WARNING]
> **Beta — active development.** Compatibility may vary by hardware and firmware. Please report test results and issues on GitHub.

The integration communicates directly with the intercom on the local network and does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Current release candidate

**v0.3.1-beta.2**

Beta 2 is based on the first real-hardware diagnostics from the V1 beta 1 test. It keeps the bounded V1 OWSP receive path, and additionally handles the real terminal-video form observed on hardware: TLV 99 may use a 12-byte metadata record and terminal TLV 100/101 may declare a short length while the native parser consumes the complete remaining Annex-B H.264 payload from the already-complete OWSP packet. The integration does not strip arbitrary bytes and still does not reassemble fragment TLVs 103/106/107/108.

For V1 output control, the doorbell listener is still fully released before a one-shot control session is opened. Beta 2 adds a bounded 1-second hardware-settle interval before that new session is opened. This is **not a retry**: the physical output request is still sent at most once per accepted user action.

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
| **WelcomeEye Connect V1 / DES9900VDP** | **v0.3.1-beta.2 candidate — hardware re-test required** | Door strike previously validated; beta 2 adjusts V1 session handover timing | Implemented; physical validation still pending | Experimental / active testing |

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## WelcomeEye Connect V1

The V1 uses a legacy LT protocol path that differs from Connect 2. The integration reproduces the native Start AV / Stop AV exchange and uses the vendor-app profile **channel 16 / stream 1 / mode 2**.

For video, the implementation now follows both the native parser behavior and the real beta 1 hardware trace:

- partial OWSP packet bytes are retained while reception is still progressing;
- a packet is never parsed until its announced payload is complete;
- V1 media reads are bounded to **6 seconds without progress**, **20 seconds total per packet** and **1 MiB maximum**;
- 12-byte and 16-byte V1 video metadata forms are accepted without inventing fields for the shorter form;
- a terminal TLV 100/101 with a short declared length may consume the complete OWSP remainder only when same-packet V1 video metadata is present and the remainder itself begins as Annex-B H.264;
- Annex-B H.264 bytes are passed unchanged to the existing decoder;
- incomplete or structurally invalid packets are rejected rather than reused.

Fragmented V1 video carried through TLVs 103/106/107/108 is **not reassembled yet**. Those packets are counted and ignored until the native ordering/reassembly rules are sufficiently demonstrated.

## Doorbell and output controls

Output commands are sent **at most once per accepted user action** and are never automatically retried.

On V1, the persistent doorbell listener temporarily releases its control session before an explicit door-strike or gate command. Beta 2 waits a bounded **1 second** after confirmed listener release before opening the one-shot V1 control session, then resumes the listener after the command session is closed. UID checks and the three-second output cooldown remain in place.

## Known limitations

- This is a **beta release under active development**.
- WelcomeEye Connect V1 beta 2 still requires physical validation for image stability, metadata behavior, audio synchronization, clean session release and output-session handover.
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
9. Enter the intercom IPv4 address, username and the **local unlock code used to open the gate/door from the WelcomeEye app**.

> [!IMPORTANT]
> The password/code requested by the integration is **not your Philips/WelcomeEye cloud account password**. Use the **local code that unlocks the gate/door on the intercom**.

## Configuration

The setup form asks for the intercom IPv4 address, username (default `admin`) and the **local gate/door unlock code used by the WelcomeEye app**. Do **not** enter the Philips/WelcomeEye cloud account password.

## Security and privacy

Diagnostics deliberately omit credentials, device UID, private device IP, raw media payloads, SDP, ICE candidate values, ICE server URLs, TURN credentials and internal stream URLs.

Opening a WebRTC viewer may contact the STUN/TURN servers configured by Home Assistant. STUN is used only for NAT traversal; when a TURN relay is required, WebRTC media remains protected by the WebRTC transport encryption.

The internal MPEG-TS proxy listens only on `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

## Development and validation

The repository includes GitHub Actions for HACS repository validation and Home Assistant Hassfest validation. The integration domain is `welcomeeye_local`.

Beta 2 adds a regression test based directly on the V1 beta 1 hardware trace: 12-byte TLV 99 metadata followed by terminal TLV 100 with declared length 1 and an Annex-B image occupying the rest of the complete OWSP packet.

## License

MIT — see [LICENSE](LICENSE).
