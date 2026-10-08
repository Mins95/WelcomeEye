# 0.4.4-beta.1 — Connect 3 automatic TLS and explicit TCP video trial

Prerelease candidate. Stable remains **0.4.3**. This beta adds automatic
certificate setup and an explicitly selected Connect 3 **QV TCP 34567** video
trial. The new TCP path has **not been validated on real hardware**. Earlier
Connect 3 video/audio/output reports describe the existing TLS media path.

## Transport choices

| Choice | Connections | Available media and controls |
| --- | --- | --- |
| **Media TLS (8443 by default)**, default mode | Verified HTTPS 443 and separately verified media TLS, normally 8443 | Existing video, sound, microphone and opted-in strike/gate controls |
| **QV TCP 34567 (experimental)** | Verified HTTPS 443 and fixed TCP 34567 | Experimental video only; no sound, microphone, strike or gate |

Select the mode in **Reconfigure** or new Connect 3 setup. TCP is never selected
because TLS fails, and it does not scan ports or use an advertised port. The
advanced media-port field still applies to TLS; it cannot change TCP 34567.
Manually entered IP/password setup needs no QV discovery. Imported QR credentials
retain their existing discovery-based device identity check in either mode.

HTTPS and media certificates are inspected separately in TLS mode. In TCP mode,
only the HTTPS certificate is inspected. An unknown local HTTPS certificate
requires one confirmation that combines first-use certificate trust and TCP
consent; a trusted HTTPS endpoint still requires explicit TCP consent when
switching to this mode. No credentials are sent during certificate inspection
or confirmation. Read-only certificate details show only the HTTPS fingerprint
for TCP. [Automatic TLS setup and Repairs](connect3-auto-tls.md).

## TCP protection and limits

The stream key is retrieved over **verified HTTPS 443**. TCP PLAY is permitted
only after a successful SDK setup response selects **AES-256 mode 2** and
**SHA-256 (selector 1)**. Mode 0, mode 1, SHA selector 0, or a rejected setup stops the attempt
before PLAY. The complete PLAY header and body, including credentials, are
encrypted with the 32-byte HTTPS stream key; they are never sent as plaintext.

This protocol uses a fixed IV and an unkeyed SHA-256 digest. The setup response is
not authenticated, and the TCP path does not provide TLS server authentication
or authenticated media integrity. Encryption does not make it equivalent to
TLS. Use this experiment only on a trusted local network and approve the
displayed limits before enabling it. An error closes the attempt without
retrying a weaker mode or falling back to another transport.

TCP sound, microphone, strike and gate remain unavailable regardless of saved
feature choices. Changing the media mode does not enable those functions. The
default TLS path keeps its existing audio and deliberate opening controls;
Connect V1, Connect 2 R001/R002 and the native CRC32C dependency retain their
existing behavior.

## Tester procedure

1. In HACS, enable beta versions, choose **Redownload → 0.4.4-beta.1**, then
   restart Home Assistant and reload the frontend. Keep the existing entries
   and credentials. If there are two Connect 3 entries, choose only one for
   this trial and leave the other unchanged. Close the Philips player.
2. Before changing transport, run `welcomeeye_local.connect3_check_access`
   **once** on the chosen entry's Connect 3 status sensor in **Developer Tools →
   Actions**. Confirm HTTPS access succeeds on port **443**. If it fails,
   download diagnostics and stop before attempting TCP video.
3. Open **Reconfigure**, keep the known IP and local connection password, select
   **QV TCP 34567 (experimental)** and enable live video. Use the default HTTPS
   port 443. Leave opening controls off; no microphone or physical-output test
   belongs to this TCP trial.
4. Review and accept the TCP confirmation. If HTTPS has an unknown local
   certificate, the same screen asks whether to trust it. Cancel if uncertain;
   no console command or manual fingerprint entry is needed.
5. Make **one video attempt** in the chosen camera's WelcomeEye card. If it
   opens, watch for **15 seconds** and check whether the picture moves.
   Immediately download integration diagnostics after success or failure,
   then close the viewer with **X**. Do not repeat the attempt for this trial.
6. Confirm the Philips app can open video afterwards. Send the diagnostics and
   report moving picture or exact displayed failure, approximate startup time,
   closure, and whether Philips resumed. Include the device model, firmware
   and HA version.

Diagnostics contain fixed reasons, stages, counters and relative timings, not
passwords, opening codes, keys, certificate fingerprints or media payloads.
Do not post credentials or detailed private action responses publicly.

Software checks cover protocol gates, encrypted PLAY, video-only capabilities,
shared-session cleanup and config-flow trust handling. They do not prove a
real-device picture or successful coexistence with the Philips player. Owner
results are required before changing this mode's experimental status.

## Rollback

Reinstall **0.4.3** and restart HA, keeping the entry. Stable 0.4.3 does not
support this new TCP choice. Disable a TCP-selected Connect 3 entry before
downgrading, and re-enable it only after configuring a working TLS endpoint.
Do not delete an existing working entry or its credentials.
