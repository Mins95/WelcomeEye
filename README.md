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

**v0.3.1-beta.4**

Beta 4 keeps the Connect V1 video path already validated on real hardware, keeps the V1 doorbell listener on standby, and restores the **pre-doorbell direct V1 output-control path**. V1 door/gate commands no longer go through the listener pause/resume barrier or the one-second control-slot settle handover introduced while doorbell listening was active.

This change is V1-only: **WelcomeEye Connect 2 keeps its existing media, doorbell and output behavior unchanged**. Physical output commands remain single-shot and are never automatically retried.

Video diagnostics are now deliberately compact: the large V1 frame-structure history and previous-session dumps have been removed because the V1 video framing is now hardware validated. Previous beta releases, including **v0.3.1-beta.2** and **v0.3.1-beta.3**, remain available on GitHub for rollback and comparison.

See [CHANGELOG.md](CHANGELOG.md) for release history and technical details.

## Features

- UI-based configuration and reauthentication.
- Direct local device authentication.
- On-demand H.264 video and G.711 A-law audio.
- Native Home Assistant WebRTC viewing.
- Remote WebRTC NAT traversal through Home Assistant's ICE/STUN/TURN configuration.
- Passive JPEG snapshot from the most recently decoded active stream.
- Doorbell / ring detection on **WelcomeEye Connect 2**, exposed as a **Sonnette** binary sensor and `welcomeeye_local.ring` event.
- Local controls for the **door strike** and **gate**.
- Privacy-safe downloadable diagnostics for media, transport, control, doorbell and WebRTC state.
- Media connections are opened only while required by an active consumer.

## Hardware status

| Device | Video / audio | Door strike / gate | Doorbell | Status |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2** | Validated | Validated | Supported | Main validated platform |
| **WelcomeEye Connect V1 / DES9900VDP** | **Video validated on real hardware** | **Beta 4: direct pre-doorbell control path under hardware re-test** | **Standby / temporarily disabled** | Experimental / active testing |

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## WelcomeEye Connect V1

The V1 uses a legacy LT protocol path that differs from Connect 2. The integration reproduces the native Start AV / Stop AV exchange and uses the vendor-app profile **channel 16 / stream 1 / mode 2**.

The V1 video path has now been validated on real hardware. The implementation follows both the native parser behavior and the real hardware trace:

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

On **Connect 2**, doorbell detection and output control keep the existing behavior unchanged.

On **Connect V1**, the persistent doorbell listener remains disabled. The **Sonnette** entity therefore remains unavailable for V1 while this feature is on standby. Beta 4 also removes the V1 listener-handover logic from the output path and returns door/gate control to the direct dedicated control-session behavior used before the doorbell listener was introduced.

## Known limitations

- This is a **beta release under active development**.
- WelcomeEye Connect V1 doorbell detection is temporarily disabled / on standby.
- V1 door-strike and gate control require real-hardware re-validation in beta 4.
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

Beta 4 keeps the beta 2 real-hardware video regression tests, replaces the obsolete V1 listener-handover control tests with direct V1 control tests, and keeps independent ring-listener plus Connect 2 regression coverage.

## License

MIT — see [LICENSE](LICENSE).
