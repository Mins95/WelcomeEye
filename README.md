<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye** intercoms.

> [!WARNING]
> **Beta — active development.** Compatibility may vary by hardware and firmware. Please report test results and issues on GitHub.

The integration communicates directly with the intercom on the local network and does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Current release candidate — 0.3.1-beta.9

Beta 9 changes the **Home Assistant frontend transport** after a real restrictive enterprise-Wi-Fi test. Under beta 8 the WelcomeEye-to-Home-Assistant media path remained healthy, H.264 decoded correctly and the SDP answer was produced, but WebRTC stayed in ICE `checking` with STUN available and no TURN relay. Temporarily disabling the native WebRTC advertisement made the same camera work through Home Assistant's existing Stream/HLS path on that same network.

Beta 9 therefore stops advertising the integration's native WebRTC handler and lets Home Assistant consume the existing `stream_source()` through its Stream/HLS transport. It requires **no TURN server, no extra container, no external relay and no firewall change**. Initial playback can need a few seconds of buffering before it stabilizes; beta 9 intentionally favors broad network compatibility over the lowest possible latency.

The native WebRTC implementation remains in the codebase but dormant so a future release can re-enable it once an automatic transport-selection approach is proven without regressing the HLS path.

All device-side behavior from beta 8 is retained: Connect 2 media/control/doorbell paths are unchanged, and V1 keeps the `16/1/2` media profile, single-shot output safety, delayed 506 handling, 5009 + native 5005 teardown, bounded H.264 SPS/PPS recovery and doorbell standby.

See [CHANGELOG.md](CHANGELOG.md), [docs/README.md](docs/README.md) and [the beta 8 stabilization audit](docs/stabilization-beta8.md).

## Features

- UI-based configuration and reauthentication.
- Direct local device authentication.
- On-demand H.264 video and G.711 A-law audio.
- Home Assistant Stream/HLS viewing through the integration's local MPEG-TS source.
- No TURN service or additional container required for beta 9 remote viewing through Home Assistant.
- Passive JPEG snapshot from the most recently decoded active stream.
- Doorbell / ring detection on **WelcomeEye Connect 2**, exposed as a **Sonnette** binary sensor and `welcomeeye_local.ring` event.
- Local controls for the **door strike** and **gate**.
- Privacy-safe downloadable diagnostics for media, transport, control, doorbell and retained WebRTC state.
- Media connections are opened only while required by an active consumer.

## Hardware status

| Device | Video / audio | Door strike / gate | Doorbell | Status |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2** | Validated | Validated | Local detection supported | Main validated platform |
| **WelcomeEye Connect V1 / DES9900VDP** | **Video validated on real hardware** | **Beta 8/9 software path validated; physical relay confirmation pending** | **Standby; local path not yet demonstrated** | Experimental / active testing |

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
- after a decoder reset, beta 8/9 can reuse a bounded cached SPS/PPS pair if the next genuine IDR omits those parameter sets;
- incomplete, oversized or structurally invalid transport packets are still rejected rather than silently reused.

Fragmented V1 video carried through TLVs 103/106/107/108 is **not reassembled yet**. Those packets are counted and ignored until the native ordering/reassembly rules are sufficiently demonstrated.

## Doorbell and output controls

Output commands are sent **at most once per accepted user action** and are never automatically retried after the physical command may have been sent.

On **Connect 2**, local doorbell detection and output control keep the existing validated behavior unchanged.

On **Connect V1**, output 0 (door strike) and output 1 (gate) are routed through the active V1 media worker on `16/1/2`, matching the official application's protected-output path. The media worker remains the only reader/writer for its socket and observes TLV 506 itself.

A command is bound to the exact media session that accepted it. During the interval after TLV 505 has been sent and before confirmation resolves, the integration can tolerate a clean V1 media-header idle timeout on that same authenticated session so a delayed TLV 506 can still be accepted. Regression tests cover confirmations delayed by 0, 1, 5 and 9 seconds. This does **not** create a new connection and cannot generate another TLV 505.

If the original TCP/media session genuinely closes after TLV 505 was sent, the result remains uncertain and the integration returns an error that preserves the on-site verification warning. It does not reconnect and replay the action.

Stale-endpoint recovery remains bounded: if a cached TCP endpoint actively refuses a connection, discovery may be refreshed and TCP retried **once before login**. At that point no physical output packet exists, so this transport recovery is not an output retry.

V1 media teardown sends the existing protected Stop AV 5009 and then the native zero-payload session-stop 5005 before TCP close. No mandatory wait for 5010 is inferred from the native implementation. Whether this clears the real V1 **busy** display is still a hardware test item.

The V1 **Sonnette** entity remains unavailable while its local event path is unresolved. The official LT application contains a cloud push subscription path, but that finding does not prove that every V1 firmware lacks a parallel local mechanism. The integration does not enable vendor-cloud runtime communication.

## Known limitations

- This is a **beta release under active development**.
- Beta 9 uses Home Assistant Stream/HLS rather than the integration's native WebRTC path; startup can take a few seconds while the stream buffers and latency can be higher than WebRTC.
- Native WelcomeEye WebRTC code is retained but deliberately not advertised by the camera in beta 9.
- WelcomeEye Connect V1 doorbell detection is disabled / on standby while a reliable local path is investigated.
- V1 door-strike and gate control use the native live-channel path but still require **physical relay validation on real hardware**.
- A TLV 506 `result=1` acknowledgement is protocol confirmation only and is not treated as proof of physical activation.
- V1 busy-state clearance after the new 5005 teardown still requires real-hardware validation.
- V1 fragmented-video reassembly is not implemented yet.
- Microphone / two-way audio: **Not validated** on hardware.
- A snapshot does not wake or open the video session on its own. Until a live stream has produced a frame, the camera may have no still image available.
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

Beta 9 diagnostics explicitly identify the frontend transport policy without exporting the internal loopback stream URL. The MPEG-TS proxy remains bound only to `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

The native WebRTC implementation remains present but is not advertised by the camera in beta 9, so ordinary beta 9 camera playback uses Home Assistant Stream/HLS instead of opening a WelcomeEye WebRTC peer connection.

## Development and validation

The repository includes GitHub Actions for the full pytest suite, HACS repository validation and Home Assistant Hassfest validation.

Beta 8 was validated with **135 tests plus 3 subtests** on Python **3.12.14** and **3.14.7**, with compilation, HACS and Hassfest all green. Beta 9 adds focused regression checks for the transport policy, retained MPEG-TS/HTTP lifecycle and manifest/diagnostics version alignment without changing device protocol handling.

The beta 9 transport choice was also field-tested on the restrictive enterprise Wi-Fi that left beta 8 WebRTC stuck in ICE `checking`: the Home Assistant Stream/HLS path successfully produced live video there, with a short unstable/buffering phase at startup before stabilizing.

Software tests do not replace the remaining physical V1 checks: door-strike actuation, gate actuation, real TLV 506 reception, disappearance of the busy state, repeated video session reopening, video continuity during the command and clean hand-back to the official application after the media session closes.

See [CHANGELOG.md](CHANGELOG.md), [docs/README.md](docs/README.md), [docs/stabilization-beta8.md](docs/stabilization-beta8.md), [docs/v1-stability-beta7.md](docs/v1-stability-beta7.md), [docs/v1-control-events-beta5.md](docs/v1-control-events-beta5.md) and [docs/v1-doorbell-beta6.md](docs/v1-doorbell-beta6.md).
