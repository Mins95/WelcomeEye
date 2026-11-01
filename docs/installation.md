# Installation and upgrades

[Documentation](README.md) · [All settings](settings.md) · [Troubleshooting](troubleshooting.md)

This guide describes **WelcomeEye 0.4.5**. Existing installations can go directly to [Updating](#updating); keep their entries, cameras and cards.

To try the new card themes, compact mode and inline photo history, select **0.4.6-beta.1** in HACS's version/download dialog with prereleases enabled. Restart HA and reload the frontend; do not recreate the integration. [Beta instructions and rollback](release-046-beta1.md) · [Customize the card](card.md#change-the-theme-or-use-compact-mode).

## Before you start

- Have HACS installed, or use the manual installation below. The integration declares Home Assistant **2025.10.0 or newer** as its minimum; the current release is also checked against recent HA versions in CI.
- Identify the model and firmware: **Connect V1**, **Connect 2 R001**, **Connect 2 R002** or **Connect 3**. R001 and R002 use different connection paths.
- Find the intercom's local IPv4 address in your router. A DHCP reservation helps keep it stable.
- Have the local credentials ready. Use the table below: the cloud password and opening code are not interchangeable.
- Close Philips video and other HA camera viewers during the first connection check.

| Information | Used for |
| --- | --- |
| V1/R001 username and intercom password | Local connection; username is normally `admin`. The setup form refers to the password used in WelcomeEye when opening the gate/portal. |
| Connect 3/R002 local connection password (`authCode`) | Local HTTPS authentication; separate from the opening code and app account. |
| Connect 3/R002 opening code | Physical strike/gate commands, only if wanted. |
| Official app account email and password | Optional Connect 3/R002 cloud doorbells. Not needed to watch local video. |
| Compatible installation QR text | Alternative local credential import for Connect 3; does not replace the cloud account. |

Never put a password or QR code in an issue, dashboard YAML or screenshot.

## Install through HACS

1. In **HACS**, open the top-right menu → **Custom repositories**.
2. Add `https://github.com/Mins95/WelcomeEye`, choosing **Integration** as the type.
3. Find **Philips WelcomeEye** and download the stable release, **0.4.5**.
4. **Restart Home Assistant**. Reload the dashboard or reopen the Companion app afterwards.

These repository steps follow the [HACS custom repository guide](https://www.hacs.xyz/docs/faq/custom_repositories/).

For manual installation, download `welcomeeye_local.zip` from the [release](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.5) and extract its contents into `<config>/custom_components/welcomeeye_local/`. Check that `manifest.json` is directly inside that directory, without an extra nested `welcomeeye_local` folder. Restart HA.

## Add your intercom

Open **Settings → Devices & services → Add integration → Philips WelcomeEye**.

### Connect V1 and Connect 2 R001

1. Choose the V1/Connect 2 local connection path.
2. Enter the intercom's IPv4 address, username and intercom password.
3. Enable **Second outdoor panel** only if you have a second outdoor station. A second indoor monitor is not a second camera source.
4. Complete setup, then open **Reconfigure** to select opening commands and photo settings.

If setup identifies probable R002 hardware, follow the R002 path instead of repeatedly retrying the legacy connection.

### Connect 2 R002 and Connect 3

1. Select the correct family. For Connect 3, optional discovery can help; if nothing appears, choose manual IP setup immediately.
2. Enter the **primary monitor's IPv4 address** and **local connection password**. Leave connection mode on **Automatic** unless your network requires specific settings.
3. Keep **Video and sound** enabled. Select **Second outdoor panel** when installed.
4. If using openings, enter the **opening code**, authorize opening commands in Advanced options and select the required outputs. Secondary targets have separate confirmations.
5. Follow any certificate approval prompt. The integration fetches certificates and computes fingerprints automatically; no console, OpenSSL or certificate file is needed.
6. If media needs TCP rather than TLS, approve that connection explicitly when proposed. The HTTPS authentication connection remains verified.

Unknown local certificates use **trust on first use**: your approval remembers this certificate, but does not independently prove the device's identity. A changed certificate requires a new approval. Any legacy-certificate exception applies only to the specific device/certificate shown. [TLS details](connect3-auto-tls.md).

Manual IP setup uses direct connections and works across routed VLANs when permitted by the firewall; it does not require successful UDP discovery. Automatic mode checks the expected endpoints only. It does not silently switch to TCP following a certificate failure. [Network settings](settings.md#connection-and-tls).

For two outdoor panels on one system, enable the second panel **on the primary entry**. The second camera uses channel 2 of that same device. Do not add a second interior monitor as though it were the second outdoor station.

## Add the dashboard card

1. Edit your dashboard → **Add card** → **WelcomeEye — Interphone**.
2. Select the primary camera created by the integration.
3. Set the title and, if wanted, the entrance and opening-button names.
4. Save. Press Play to start video; sound begins muted.

The equivalent minimal YAML is:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

Replace the example entity with the actual camera from HA. The card finds the associated second camera automatically; no second entity field is required. [Card editor and full customization example](card.md).

For the microphone, use HA through trusted **HTTPS** and grant microphone permission in the browser or Companion app. Close the HA video before opening Philips video.

## Set up the doorbell and photos

| Model | Doorbell setup |
| --- | --- |
| Connect 2 R001 | Local ring detection; no cloud account needed. |
| Connect V1 | Reconfigure → enable V1 cloud notifications; no app-account login. [Guide](v1-cloud-doorbell.md). |
| Connect 2 R002 | Reconfigure → Cloud doorbell notifications → Configure, using the **WelcomeEye** app account. [Guide](qv-cloud-doorbell.md). |
| Connect 3 | Same cloud setup, using the **Philips Door Connect** account. [Guide](qv-cloud-doorbell.md). |

A detected ring activates the corresponding HA doorbell sensor for five seconds and emits an event. It does **not** automatically play a sound, open the dashboard or switch cameras. Use HA automations for those actions.

For automatic visitor photos, use the separate **Ring image capture** switch on the device page. It defaults ON on V1/R001 and OFF on Connect 3/R002; an existing saved choice is preserved. Set the delay, retention and sidebar visibility in **Reconfigure → Photo management**.

> **Known limitation:** acquiring a photo or opening live video during a call can stop the indoor monitor and outdoor station from ringing. The official app's notifications/ringing continue. A longer capture delay does not guarantee avoiding the interruption. Keep automatic capture OFF if preserving the full ringing cycle is the priority.

Manual photos are available from the card. Open the selected live video first on Connect 3/R002. View saved files in **Media → WelcomeEye**, or manage them in the **WelcomeEye** sidebar library as an administrator. [Photo guide](captures.md).

## Check the result

1. Open video, enable sound and check the microphone if needed.
2. With two panels, switch between them and check each picture.
3. Close HA video and check that Philips can regain access.
4. With viewers closed and automatic photos OFF, press each physical bell separately and check its HA sensor. Then enable photos if you accept the limitation above.
5. Only if wanted, deliberately test each authorized opening once while observing its actual target. Saving settings never opens a relay.

## Updating

Download the desired version through HACS, restart HA, then fully reload the dashboard/Companion frontend. **Do not delete and recreate the integration.** Existing entities, credentials, certificate approvals and custom card names are retained.

Use **Reconfigure** to change available settings; blank secret fields on Connect 3/R002 keep their saved values. The fields shown depend on the model. The clear-credentials option is an explicit reset, not an update step.

For rollback, take a HA backup first and follow the [release notes](release-045.md). Reinstalling an earlier version and restarting HA does not restore photos already deleted manually or by retention.
