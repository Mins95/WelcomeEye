<p align="center">
  <img src="custom_components/welcomeeye_local/brand/logo.png" alt="Philips WelcomeEye" width="140">
</p>

# Philips WelcomeEye for Home Assistant

Unofficial Home Assistant custom integration for **Philips WelcomeEye Connect 2**.

> [!WARNING]
> **Beta — active development.** This integration is still under development. Features, compatibility, configuration and behavior may change between releases. Please report issues and test results on GitHub.

The integration communicates directly with the intercom on the local network. It does **not** use a vendor cloud API at runtime.

> This project is community maintained and is not affiliated with, endorsed by, or supported by Philips, Avidsen, Home Assistant, or HACS.

## Features

- UI-based configuration and reauthentication.
- Direct local device authentication.
- On-demand H.264 video.
- G.711 A-law audio decoding when exposed by the device.
- Home Assistant camera stream support.
- Native Home Assistant WebRTC viewing.
- Passive JPEG snapshot from the most recently decoded active video stream.
- Local output controls:
  - **Ouvrir la gâche** — output 1;
  - **Ouvrir le portail** — output 2.
- Diagnostic entities for:
  - active video-session connectivity;
  - negotiated video resolution;
  - negotiated frame rate.
- Media connection is opened only while a consumer is actively using the stream.

## Supported hardware

The current implementation targets the **Philips WelcomeEye Connect 2** protocol variant that returns a protected Goolink discovery response.

Other WelcomeEye models and firmware variants have not been validated and should be considered unsupported unless confirmed through testing.

## Known limitations

- This is a **beta release under active development**.
- Ring/button events are **not exposed** by this release.
- Microphone / two-way audio from Home Assistant to the intercom is not implemented yet.
- A snapshot does not wake or open the video session on its own. Until a live stream has produced a frame, the camera may have no still image available.
- The integration requires an **IPv4 address**; hostnames are intentionally not accepted.
- Home Assistant must be able to reach the intercom directly on the LAN.
- The device is contacted on UDP port `1500` for discovery, then on the TCP port advertised by the device.
- A DHCP reservation/static lease for the intercom is recommended so its configured IPv4 address does not change.

## Installation with HACS

This repository is structured as a HACS custom integration.

1. Open **HACS** in Home Assistant.
2. Open the menu and choose **Custom repositories**.
3. Add:
   `https://github.com/Mins95/WelcomeEye`
4. Select the category **Integration**.
5. Install **Philips WelcomeEye**.
6. Restart Home Assistant.
7. Go to **Settings → Devices & services → Add integration**.
8. Search for **Philips WelcomeEye**.

## Manual installation

Copy:

```text
custom_components/welcomeeye_local/
```

to:

```text
<your Home Assistant config>/custom_components/welcomeeye_local/
```

Restart Home Assistant, then add **Philips WelcomeEye** from **Settings → Devices & services**.

## Configuration

The setup form asks for:

- **Intercom IPv4 address**
- **Username** — the default username is `admin`.
- **Intercom password** — use the password you enter in the **WelcomeEye mobile app when opening the gate/portal**. This is not your WelcomeEye account password.

The integration validates the connection before creating the Home Assistant config entry.

## Door strike and gate controls

The tested WelcomeEye Connect 2 accepts an output command only after its media session has been initialized.

When you press **Ouvrir la gâche** or **Ouvrir le portail** while no Home Assistant video stream is active, the integration therefore:

1. opens a temporary local media session;
2. waits for the device to report that the video stream is ready;
3. sends the requested output command exactly once;
4. waits for the device acknowledgement;
5. immediately closes the temporary media session and releases the intercom stream.

This allows the buttons to work without opening the camera manually while avoiding a permanently occupied video stream. The stream remains free again for the WelcomeEye application and indoor monitor as soon as the command has completed.

On the tested WelcomeEye Connect 2, **output 1 is the door strike (gâche)** and **output 2 is the gate (portail)**.

For safety, an output command is sent only once and is not automatically retried if confirmation is not received.

## Security and privacy

The device password is stored in the Home Assistant config entry and is used for direct LAN authentication to the intercom.

Diagnostics deliberately omit:

- the password;
- the device UID;
- media payloads;
- the internal stream URL.

The internal MPEG-TS proxy listens only on `127.0.0.1` and uses a randomly generated path for each Home Assistant integration instance.

Do not post device passwords, device identifiers, private IP details, or packet captures in public GitHub issues.

## Local-only runtime

The integration itself communicates with the WelcomeEye device locally. Home Assistant may need Internet access during installation to obtain the Python dependency declared in `manifest.json`.

The WebRTC dependency is pinned to `aiortc==1.15.0`.

## Development and validation

The project is currently in **beta** and is being actively developed. Feedback from different WelcomeEye Connect 2 firmware versions is welcome.

The repository includes GitHub Actions for:

- HACS repository validation;
- Home Assistant Hassfest validation.

The integration domain is:

```text
welcomeeye_local
```

## License

MIT — see [LICENSE](LICENSE).
