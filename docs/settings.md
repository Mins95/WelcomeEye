# Settings reference — 0.4.5

[Documentation](README.md) · [Installation](installation.md) · [Card](card.md) · [Troubleshooting](troubleshooting.md)

Open **Settings → Devices & services → Philips WelcomeEye**, then the entry's menu → **Reconfigure**. Labels follow your HA profile language. Sections depend on the model, existing configuration and enabled features; not every field appears on every entry.

Use the UI rather than editing HA's private storage. Existing preferences are preserved during updates. Saving or enabling a control never sends an opening command.

## Device

| Setting | Default / choices | What it changes |
| --- | --- | --- |
| WelcomeEye language | Home Assistant / French / English / Spanish | Language of WelcomeEye's own interface/card. HA configuration forms continue to follow the HA profile language. |
| IP address | Supplied by you | Device endpoint. Available during legacy setup and in Connect 3/R002 connection configuration; legacy reconfiguration does not expose every connection field. |
| Username | `admin` for V1/R001 | Local legacy login. |
| Intercom password | Required for V1/R001 | Password described in that model's setup form; not the Philips account password. |
| Local connection password | Required for Connect 3/R002, unless a supported alternative is used | `authCode` for verified local HTTPS. A blank field during reconfiguration keeps the saved value. |
| Installation QR text | Optional, Connect 3 Advanced options | Alternative to typing the local connection password. Identity consistency is still checked. Not a cloud-account credential. |
| Video and sound | ON for new manual Connect 3/R002 entries | Enables local media. Existing entries retain their previous setting. |
| Second outdoor panel | OFF | Makes the second camera available under the same device and enables secondary settings. Does not mean a second indoor monitor. |
| Philips strike/gate opening code | Optional, Connect 3/R002 | Required for physical commands; separate from the connection password. Blank keeps the saved code. |

Two outdoor panels use the primary device's connection and separate camera channels. No second monitor IP is needed just to access channel 2. Playback remains on demand; selecting a second panel does not start video at HA startup.

## Opening commands

Choose only the outputs you use. These choices are independent of the camera currently displayed.

| Setting | Fixed target | Availability |
| --- | --- | --- |
| Enable wicket gate 1 / Portillon 1 | Channel 1, output 1 | Supported primary strike. |
| Enable vehicle gate 1 / Portail 1 | Channel 1, output 2 | Supported primary gate. |
| Enable wicket gate 2 / Portillon 2 | Channel 2, output 1 | Second outdoor panel and its own authorization required. |
| Enable vehicle gate 2 / Portail 2 | Channel 2, output 2 | Second outdoor panel and its own authorization required. |

Primary preferences default enabled where no saved preference exists; this does not override a model's capabilities or required authorization. Secondary choices start disabled unless a previous explicit approval was migrated. There is no assumed shared gate.

For example, disable wicket gate 1 and leave vehicle gate 1 enabled to show only the portal button. With three authorized outputs, all three buttons remain visible under both camera views.

The following permissions may also appear in **Advanced options**:

| Permission | Purpose |
| --- | --- |
| Authorize opening commands | Enables Connect 3/R002 physical controls with the configured opening code. OFF by default. |
| Authorize wicket gate 2 | Separate permission for channel 2, output 1. OFF by default. |
| Authorize vehicle gate 2 | Separate permission for channel 2, output 2. OFF by default. |
| Allow microphone and controls over TCP | Separate approval for these functions on the non-TLS QV media transport. |
| Second-panel microphone | Separate secondary microphone option where supported. OFF by default; requires the relevant confirmation. |

Disabling the second panel blocks secondary commands and revokes their approvals. Stored preferences can be retained, but re-enabling the panel does not silently restore authorization. Changes to connection trust or credentials can also require renewed consent.

Each opening is one explicit action, with at most one physical attempt and no automatic retry. A native acknowledgement is not an open/closed position sensor. An ambiguous result blocks further use of that session. [Output routing](connect3-four-outputs.md).

## Photo management

These settings are available across supported models.

| Setting | Default | Choices and effect |
| --- | --- | --- |
| Photo retention | Unlimited | Unlimited, 7, 30 or 90 days. Finite retention deletes older WelcomeEye files for this device, at most once daily. Deletion is permanent. |
| Show photo library in sidebar | ON | Shows the shared administrator photo-management panel. With multiple entries, it stays visible while any loaded entry enables it. |
| Ring image capture delay | 4 seconds | Whole seconds from 0 to 120. Measured after HA accepts the ring, not from the exact physical press. Does not enable capture by itself. |

Hiding the sidebar neither deletes photos nor removes **Media → WelcomeEye**. To hide the shared sidebar library, disable it on every device entry.

**Ring image capture is a separate device switch**, not the delay field: ON by default on V1/R001, OFF on Connect 3/R002, with saved choices preserved. It requires a working ring source. V1's cloud option itself remains OFF until enabled.

Acquiring a photo during ringing can stop the monitor's call. Even zero delay starts after notification delivery; a longer delay only postpones acquisition. [Capture behavior, storage, deletion and events](captures.md).

## Cloud doorbell notifications

| Model | Setting | Credentials |
| --- | --- | --- |
| V1 | Enable V1 cloud doorbell notifications; OFF by default | No app-account login; HA registers its own receiver. |
| R001 | No cloud setup required for local rings | Local connection. |
| R002 / Connect 3 | Cloud doorbell notifications: Keep / Configure / Disable | Official **WelcomeEye** account for R002; **Philips Door Connect** account for Connect 3. |

For R002/Connect 3, **Configure** opens the account form and device selection. Approve the subscription and choose the device corresponding to this HA entry. Leaving an existing password blank keeps it. **Keep** leaves the subscription unchanged; **Disable** stops HA's cloud route without disabling local video or commands.

Cloud delivery does not automatically open a camera, play audio or send a user notification. The device's bell sensor pulses for five seconds, and `welcomeeye_local.ring` is available for automations. [Connect 3/R002 cloud guide](qv-cloud-doorbell.md) · [V1 cloud guide](v1-cloud-doorbell.md).

## Connection and TLS

These advanced settings apply to Connect 3 and the manual-IP R002 setup. Normal installations can leave the connection on **Automatic**.

| Setting | Default / choices | Purpose |
| --- | --- | --- |
| Connection | Automatic for a new manual setup; TLS or QV TCP can be selected | Automatic checks the expected TLS endpoint first. When the standard TLS port refuses and QV responds on 34567, TCP is proposed with approval. Certificate failures are not a reason to downgrade. |
| HTTPS port | 443 | Verified CGI authentication and stream-key retrieval. |
| TLS media port | 8443 | Applies to TLS media only; TCP QV uses 34567 and has no media TLS certificate. |
| Manual HTTPS fingerprint | Optional | Advanced alternative to automatic certificate approval. Existing manually supplied pins are preserved. |
| Manual media fingerprint | Optional | Separate media TLS pin when needed. No media pin is required for QV TCP. |
| Clear credentials and TLS trust; disable video and openings | OFF | Explicit reset of local credentials/trust and dependent features. Do not select for an ordinary update. |

Ports accept values from 1 to 65535, but changing a number does not make the device listen there. Do not use a media-port change to bypass a certificate error. For R002 installations created with discovery, the **Connection mode** choice provides a **Manual** route to IP-based setup without recreating the entry.

HA must be able to reach the configured CGI endpoint and selected media endpoint. Manual Connect 3/R002 setup uses unicast and does not require the device to share HA's VLAN. Discovery and HomeKit network discovery have their own network requirements.

### Certificate approvals

Certificates and SHA-256 fingerprints are collected automatically without sending credentials. System-trusted certificates with valid server identity need no manual trust step. For an unknown local certificate, confirm trust on first use in HA; details are available without copying anything.

Approved fingerprints are stored privately and checked before secrets are sent. CGI and TLS media certificates can differ. A replacement certificate is blocked until approved; address or port changes do not silently transfer trust to an unrelated endpoint.

Some firmware certificates require an additional **zero-duration validity** or **legacy RSA 1024-bit key** exception. These are explicit, certificate-specific decisions. They do not repair the certificate or strengthen its key. HTTPS verification is never globally disabled. [Detailed TLS guide](connect3-auto-tls.md).

### TCP media confirmation

QV TCP media on 34567 is a separate, explicitly approved mode. CGI authentication remains on verified HTTPS. QV protects credentials according to its negotiated application protocol, but media TCP does not provide TLS-equivalent peer authentication, integrity or replay protection. Microphone/control permissions are separate from the initial media choice.

### Advanced support diagnostics

Certificate-check actions remain available for support; normal installation does not require calling them. A Connect 3 option for **native v2 alarm listening over TCP** authorizes only a voluntary diagnostic, up to 90 seconds. It is OFF by default, has its own warning about a reusable login hash sent without TLS, and saving it starts nothing. It is not the normal cloud doorbell setup or a confirmed independent local bell detector.

## Dashboard presentation

Card labels are changed in **Edit dashboard → Edit the WelcomeEye card**, not in Reconfigure.

| Card field | Default label |
| --- | --- |
| `channel_1_name` | Entrance 1 |
| `channel_2_name` | Entrance 2 |
| `strike_1_name` | Wicket gate 1 |
| `strike_2_name` | Wicket gate 2 |
| `gate_1_name` | Vehicle gate 1 |
| `gate_2_name` | Vehicle gate 2 |

Each custom label allows up to 64 characters. Secondary fields appear only with a second panel. Labels do not rename HA entities or change relay routing. See the [complete card YAML and graphical-editor guide](card.md).
