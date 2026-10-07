# Connect 2 R002 — investigation in 0.4.3

**R002 remains experimental.** The firmware `V401.R002.A302.00.G0058.B002`
uses a different path from Connect 2 R001. Successful Connect 3 tests do not
validate this firmware. No complete working R002 intercom is claimed.

## Current tools and optional trial

- The default R002 entry exposes a diagnostic status sensor and starts no
  background device session.
- `r002_discover_qv` performs one bounded APK-compatible UDP discovery. A real
  IDS9417AW response was decoded, including its base R002 firmware. This is
  discovery evidence, not authentication or working video.
- `r002_check_qv_certificate` inspects HTTPS after discovery, without sending
  credentials. `r002_check_access` checks the configured local password over
  trusted/pinned HTTPS; a stream key is never returned or stored in diagnostics.
- An explicit experimental-video option enables the observed QV profile's
  TCP 34567 media trial. Outputs require a separate option and opening code.
  Audio, microphone and controls still need R002 hardware confirmation.
- `r002_probe` retains the bounded TCP 8765 investigation for types 14/15/26/28.
  Raw prefixes/responses require explicit administrator opt-in and must be reviewed
  before sharing. They never enter standard integration diagnostics.
- `r002_observe_doorbell` observes an already open live session. It is not a
  standby listener and emits no inferred ring event. Photos are unavailable.

Keep the existing entry. Install **0.4.3**, restart HA, and follow the
[QV configuration and ordered test procedure](release-043-beta11.md#configure-the-existing-entry)
for an agreed hardware trial. Its beta.11 protocol procedure remains applicable;
use the current version and retain existing passwords/pins. If access fails,
download fresh diagnostics before further tests; do not guess credentials.

Report results in [issue #7](https://github.com/Mins95/WelcomeEye/issues/7).
There is no automatic port scan, UDT fallback, physical-command replay or
periodic investigation. Native CRC32C and the R001/V1 paths are unchanged.

## Historical TCP 8765 investigation

The material below preserves the beta.3 interpretation and test plan. Its entity
matrix and rollback instructions describe that historical release, not 0.4.3.

<details>
<summary>Earlier parser observations and setup evidence</summary>

## What is known

The tester reports no legacy UDP 1500 response, sometimes ICMP port unreachable. TCP 8765 answers eight-byte requests with a prefix currently interpreted as a twelve-byte header. TCP 443 presents a self-signed certificate with CN `eziotest`. Ports 21, 6987 and 34567 were also observed by the tester; this integration does not scan them. Legacy OWSP login attempts did not establish a useful session on those services.

The beta.1 parser assumes `<HHHHI` (little-endian type, version, flags, body length, then an unknown field at offsets 8–11). This is a candidate interpretation, not a confirmed header/payload boundary. Beta.2 retains this strict parser and makes its rejection observable. Empty requests of types 14, 15, 26, 28 reportedly return 6, 14, 20, 0 bytes respectively. **Their meaning is unknown.** They are not proven configuration reads; no R002 output or media implementation is inferred from them. Four exact hardware prefixes are now preserved in tests with their issue source; complete responses remain explicitly synthetic. The 14/15 nonzero values at offsets 8–11 disprove the zero requirement for those replies, but do not establish a 10- or 12-byte boundary.

## Setup and identity

Normal legacy authentication is attempted first. Only a specific discovery-no-response exception enables the alternative fingerprint; TCP/login/authentication failures do not. After UDP discovery absence, setup makes one bounded TCP connection to 8765 without application bytes, then one connection/TLS handshake to 443 without HTTP or credentials. Each step has a three-second deadline plus at most one second for closing. A reachable 8765 **and** successful TLS with CN `eziotest` yield **probable**, never certain, R002 identification.

The user must accept the experimental confirmation screen. Setup stores the IPv4 address and safe fingerprint metadata, not the unused legacy credentials. The provisional configuration ID is `r002-<sha256(initial_host)[:16]>`; it is not a hardware UID, and hashing an IPv4 address is not strong anonymization. It is omitted from public diagnostics. Reconfigure preserves this ID when changing address. Discovery of a hardware identity will require an explicit future migration; delete/re-add with a different address is not such a migration.

The loaded R002 hub starts no device connection, listener, media server, controller, capture or WebRTC session. Its only entity is a diagnostic protocol-status sensor. No background probes run.

## Entity matrix

| Entity/function | R001 | V1 | Authenticated legacy, not classified | R002 beta.3 |
| --- | --- | --- | --- | --- |
| Camera, live/audio/talkback | Created / retained | Created / retained | Created / retained | Not created |
| Strike and gate buttons | Created | Created | Created | Not created |
| Manual snapshot / last_snapshot image | Retained / created | Retained / created | Retained / created | Not created |
| Ring binary sensor | Created | Not created | Not created | Not created |
| Ring-capture switch / last_ring image | Created | Not created | Not created | Not created |
| Video-session binary sensor, resolution, fps | Created | Created | Created | Not created |
| Protocol-status sensor / probe | Not created | Not created | Not created | Created / available |

R001 has ten entities; V1 and unknown legacy seven; R002 one. Talkback and manual capture use the camera/card, not extra entities. Known unsupported registry entries are removed by exact domain, integration, config entry and unique-ID suffix. Renaming an entity does not evade this cleanup; unrelated entities are left alone. Reclassification from an existing media signature is persisted; one reload runs after current consumers release, so identification does not cut a viewer. V1 stays on 16/1/2. No packet builder, encryption, output mapping, H264, Start/Stop AV or physical-command retry policy is changed.

## Next R002 tester procedure

Use the [single beta.3 action and response format](release-043-beta3.md). Only one execution of types 14/15/26/28, intercom at rest; no ring, media, output or second certificate check. The beta.3 candidate is not yet a published release. Existing users can remain on beta.2 until publication.

Normal mode remains beta.2's strict `<HHHHI` parser with no raw export by default and the existing 4096-byte body limit. `include_header` remains the explicit administrator-only 12-byte prefix option. `include_response` selects a different, bounded observation mode on the same one request/connection/reader: it collects up to 64 bytes until server EOF, the absolute three-second deadline, or the cap, without waiting for an assumed body length. It includes prefix details automatically and does not execute both read paths.

An observation deadline is not a proven message boundary; client close is not server EOF. A size cap does not prove response completeness. Layout comparison is performed only after collection, without another request. Responses/candidate fields never enter standard diagnostics or logs; explicit HA action/script responses may be retained by HA and must be reviewed before public sharing.

## Historical background: passive app capture (not part of the beta.3 test)

Capture at a router/AP or mirrored switch port that can actually see phone↔intercom traffic; HA on another switch port usually cannot. No interception certificate or active protocol injection is needed. Keep WelcomeEye HA viewers closed and do not run probes during the capture.

Start a capture limited to the phone and intercom (capture filter `host PHONE_IP or host INTERCOM_IP`). Force-close Philips first; start capture; open Philips; wait a few seconds; open live for about ten seconds; close live; ring once; allow the notification/call to appear; stop. Aim for 45–60 seconds and record relative action times. Do not operate outputs. Keep the PCAP private. First share only a sanitized conversation summary: relative times, TCP/UDP, ports, direction and sizes; aliases for addresses and no TLS secrets. A phone-wide filter also retains cloud DNS/rendezvous, which a device-only filter would miss.

This can distinguish TCP 8765 side-channel traffic from CGI/TLS or direct UDP/P2P media. APK evidence is in [r002-apk-analysis.md](r002-apk-analysis.md). No cloud dependency was added to WelcomeEye.

## Validation and rollback

Run `python -m unittest discover -s tests -p 'test_*.py'`, Node frontend tests, compile and `tools/build_release.py`. CI additionally tests Python 3.12/3.14, Hassfest, HACS and actual HA 2026.9.3 registry/service APIs with network mocks. These prove software behavior, not R002 hardware support. This task sends no requests to a real intercom and actuates no relay.

Rollback to beta.2: select **0.4.3-beta.2**, redownload and restart; keep the existing entry. For a separate downgrade all the way to stable 0.4.2, remove the R002 investigation entry first (the stable hub does not understand this family); redownload 0.4.2 and restart. Legacy entries remain valid. Removed unsupported legacy registry entries can be recreated by the older version. No firmware or persistent device setting is modified by the integration setup.

</details>
