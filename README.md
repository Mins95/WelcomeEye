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
- **WelcomeEye Connect V1 / DES9900VDP** — output control works on a community test device; video compatibility is experimental in beta 7/8 with legacy H.264 packet detection and automatic media-profile detection.

Other WelcomeEye models and firmware variants should be considered experimental unless confirmed through testing.

## Beta 8 remote WebRTC

Beta 7 explicitly created the server-side WebRTC peer with `iceServers=[]`. That works well when the viewer can reach Home Assistant directly on the LAN, but can leave remote mobile/Safari viewers stuck in ICE `connecting` because Home Assistant cannot advertise a server-reflexive or relay candidate.

Beta 8 now asks Home Assistant's built-in `web_rtc` subsystem for the current ICE server list for every new viewer. This includes Home Assistant's default STUN servers and also supports custom or integration-provided TURN relays when available. Fetching the list per viewer also allows short-lived TURN credentials to be used without caching them inside WelcomeEye.

The downloadable diagnostics now include ICE connection/gathering/signaling state, STUN/TURN availability and candidate classes (`host`, `srflx`, `relay`) plus transport protocol. Candidate addresses, ports, ICE server URLs and relay credentials are deliberately excluded.

STUN improves direct remote connectivity but cannot guarantee every NAT/firewall combination. Networks that require a relay still need a TURN server supplied through Home Assistant's WebRTC configuration or another Home Assistant integration that registers one.

## Beta 7 compatibility auto-detection

A Connect V1 test device announced H.264 at 352×288 / 20 fps and delivered audio TLV 98, but no video TLV 100/101 on the Connect 2 profile. Beta 7 tries the media profiles observed in the WelcomeEye/Qv SDK and also inspects TLVs 97/99/100/101 for H.264 framing without retaining media payloads.

The integration starts with the normal Connect 2 profile. If a format TLV 203 is received but no usable video keyframe follows, it tries compatibility channel/mode variants. Legacy TLVs 97/99 are accepted as video only when their payload is structurally detected as H.264. Once a profile produces video, that profile is preferred for later sessions. These probes only open media sessions and **never send an output/open command**.

Beta 7 also recognizes the intercom model from the validated media signature and updates the Home Assistant device model instead of always displaying Connect 2.

The downloadable diagnostics expose the selected profile, profile attempts and video packet count without including credentials, device UID, IP address, SDP, ICE candidate values or media payloads.

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
