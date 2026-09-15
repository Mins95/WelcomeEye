<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye** intercoms.

> [!WARNING]
> **Beta — active development.** Compatibility may vary by hardware and firmware. Please report test results and issues on GitHub.

The integration communicates directly with the intercom on the local network and does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Current release — 0.3.1-beta.8

**[v0.3.1-beta.8](https://github.com/Mins95/WelcomeEye/releases/tag/v0.3.1-beta.8)** is the current HACS-visible release.

Beta 8 hardens HTTP/PyAV/WebRTC cleanup, preserves uncertain physical-output reporting across the Home Assistant executor, records media-worker exit reasons, and improves V1 H.264 recovery by retaining a bounded SPS/PPS pair for an IDR that does not repeat those parameters.

Native analysis distinguishes stream Stop AV **5009** from session stop **5005**. Beta 8 keeps 5009 and adds the native zero-payload 5005 before TCP close on authenticated V1 media sessions. The native stop chain did **not** demonstrate a mandatory wait for 5010. The latest V1 hardware test before this release sent 5009, received no 5010 and left the display busy; whether 5005 clears that busy state still requires real-hardware confirmation. See [the beta 8 stabilization audit](docs/stabilization-beta8.md).

Connect 2 video/audio/outputs/doorbell and V1 login/Start AV/352x288 video were validated on earlier hardware versions. V1 physical relay actuation and real TLV 506 reception remain unvalidated; V1 doorbell stays disabled/standby and talkback is unchanged.

WebRTC direct/STUN behavior depends on the network. TURN settings supplied by Home Assistant are supported; no external TURN server is hardcoded. A real TURN relay path on the restrictive enterprise Wi-Fi has not yet been validated.

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
| **WelcomeEye Connect 2** | Validated | Validated | Local detection supported | Main validated platform |
| **WelcomeEye Connect V1 / DES9900VDP** | **Video validated on real hardware** | **Beta 8 software path validated; physical relay confirmation pending** | **Standby; local path not yet demonstrated** | Experimental / active testing |

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## WelcomeEye Connect V1

The V1 uses a legacy LT protocol path that differs from Connect 2. The integration reproduces the native Start AV / Stop AV exchange and uses the vendor-app profile **channel 16 / stream 1 / mode 2**.

The V1 video path is validated on real hardware. The implementation follows both the native parser behavior and the real hardware trace:

- partial OWSP packet bytes are retained while reception is still progressing;
- a packet is never parsed until its announced payload is complete;
- V1 media reads are bounded to **6 seconds without progress**, **20 seconds total per packet** and **1 MiB maximum**;
- 12-byte and 16-byte V1 video metadata forms are accepted without inventing fields for the shorter form;
- a terminal TLV 100/101 with a short declared length may consume the complete OWSP remainder only when same-packet V1 video metadata is present and the remainder itself begins as Annex-B H.264;
- Annex-B H.264 media framing is preserved; during V1 decoder recovery only, cached SPS/PPS may be prepended to the decoder input without changing TS media bytes;
- a PyAV `InvalidDataError` drops only the invalid access unit and waits for a fresh keyframe instead of closing the V1 session;
- after a decoder reset, beta 8 can reuse a bounded cached SPS/PPS pair if the next genuine IDR omits those parameter sets;
- incomplete, oversized or structurally invalid transport packets are still rejected rather than silently reused.

Fragmented V1 video carried through TLVs 103/106/107/108 is **not reassembled yet**. Those packets are counted and ignored until the native ordering/reassembly rules are sufficiently demonstrated.

## Doorbell and output controls

Output commands are sent **at most once per accepted user action** and are never automatically retried after the physical command may have been sent.

On **Connect 2**, local doorbell detection and output control keep the existing validated behavior unchanged.

On **Connect V1**, output 0 (door strike) and output 1 (gate) are routed through the active V1 media worker on `16/1/2`, matching the official application's protected-output path. The media worker remains the only reader/writer for its socket and observes TLV 506 itself.

A command is bound to the exact media session that accepted it. During the interval after TLV 505 has been sent and before confirmation resolves, beta 8 can tolerate a clean V1 media-header idle timeout on that same authenticated session so a delayed TLV 506 can still be accepted. Regression tests cover confirmations delayed by 0, 1, 5 and 9 seconds. This does **not** create a new connection and cannot generate another TLV 505.

If the original TCP/media session genuinely closes after TLV 505 was sent, the result remains uncertain and the integration returns an error that preserves the on-site verification warning. It does not reconnect and replay the action.

Stale-endpoint recovery remains bounded: if a cached TCP endpoint actively refuses a connection, discovery may be refreshed and TCP retried **once before login**. At that point no physical output packet exists, so this transport recovery is not an output retry.

V1 media teardown now sends the existing protected Stop AV 5009 and then the native zero-payload session-stop 5005 before TCP close. No mandatory wait for 5010 is inferred from the native implementation. Whether this clears the real V1 **busy** display is still a hardware test item.

The V1 **Sonnette** entity remains unavailable while its local event path is unresolved. The official LT application contains a cloud push subscription path, but that finding does not prove that every V1 firmware lacks a parallel local mechanism. The integration does not enable vendor-cloud runtime communication.

## Known limitations

- This is a **beta release under active development**.
- WelcomeEye Connect V1 doorbell detection is disabled / on standby while a reliable local path is investigated.
- V1 door-strike and gate control use the native live-channel path but still require **physical relay validation on real hardware**.
- A TLV 506 `result=1` acknowledgement is protocol confirmation only and is not treated as proof of physical activation.
- V1 busy-state clearance after the new 5005 teardown still requires real-hardware validation.
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

Diagnostics deliberately omit credentials, device UID, private device IP, raw media payloads, alarm payloads, FCM tokens, SDP, ICE candidate values, ICE server URLs, TURN credentials and internal stream URLs.

Discovery-recovery diagnostics contain aggregate counters only; they do not export the refreshed address, TCP port, UID or raw discovery packet. Beta 8 adds lifecycle and codec counters without adding raw media, DNS, SDP, ICE-address or manufacturer payload data to diagnostics.

Opening a WebRTC viewer may contact the STUN/TURN servers configured by Home Assistant. STUN is used only for NAT traversal; when a TURN relay is required, WebRTC media remains protected by the WebRTC transport encryption.

The internal MPEG-TS proxy listens only on `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

## Development and validation

The repository includes GitHub Actions for the full pytest suite, HACS repository validation and Home Assistant Hassfest validation.

Beta 8 was validated with **135 tests plus 3 subtests** on Python **3.12.14** and **3.14.7**, with compilation, HACS and Hassfest all green. Coverage includes real aiortc/PyAV paths, exact aioice mDNS SRV preload behavior, 50 media acquire/release cycles per model, 50 pipeline create/destroy cycles per resolution, 50 successive WebRTC viewers, delayed TLV 506 handling, shutdown/cleanup races and V1-only H.264 SPS/PPS recovery.

Software tests do not replace the remaining physical V1 checks: door-strike actuation, gate actuation, real TLV 506 reception, disappearance of the busy state, repeated video session reopening, video continuity during the command and clean hand-back to the official application after the media session closes. Real TURN relay traversal on a restrictive network also remains to be validated.

See [CHANGELOG.md](CHANGELOG.md), [docs/README.md](docs/README.md), [docs/stabilization-beta8.md](docs/stabilization-beta8.md), [docs/v1-stability-beta7.md](docs/v1-stability-beta7.md), [docs/v1-control-events-beta5.md](docs/v1-control-events-beta5.md) and [docs/v1-doorbell-beta6.md](docs/v1-doorbell-beta6.md).
