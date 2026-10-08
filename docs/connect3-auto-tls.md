# Connect 3 — automatic local TLS (0.4.4-beta.1)

TLS media remains the default. This beta also offers an explicit **QV TCP 34567
(experimental)** video-only mode. Its protection is not equivalent to TLS and
its hardware behavior remains unvalidated. [TCP scope and tester procedure](release-044-beta1.md).

## Installation

1. Install **v0.4.4-beta.1** through HACS (show beta versions), then restart Home
   Assistant. Keep an existing entry; use **Reconfigure** to adopt automatic TLS.
2. Choose **WelcomeEye Connect 3**, enter its IPv4 address and **local connection
   password**, and enable the wanted features. The separate **Philips opening
   code** is needed only when enabling strike/gate controls.
3. Keep **Media TLS (8443 by default)** for the existing video/audio/microphone/output path.
   Home Assistant checks HTTPS and media certificates automatically. If system
   trust and the server IP identity are valid, setup continues automatically.
   Otherwise review the single **Trust this device** confirmation. Optional
   read-only SHA-256 fingerprints are in **Advanced certificate details**.
4. For an explicit TCP video trial, select **QV TCP 34567 (experimental)**.
   HTTPS 443 is still verified, but no media TLS certificate is inspected.
   Accept the separate TCP consent, or the combined HTTPS trust/TCP consent if
   the HTTPS certificate is unknown. Sound, microphone, strike and gate are
   unavailable in this mode, even if they were previously enabled.
5. Use the existing WelcomeEye camera card. No OpenSSL, PEM file, certificate
   action, or manually entered fingerprint is required.

Approval is **trust on first use (TOFU)**. It identifies the certificate you
observed and approved; it is not independent proof of the intercom's identity.
Cancel if you are unsure about the endpoint or network. No credential is sent
during inspection or approval.

## Existing installations and changed certificates

Existing entries, entity IDs, QR identity bindings and manually configured pins
are preserved. No network discovery or certificate inspection is added at
startup. Reconfigure checks both ports in TLS mode and HTTPS only in TCP mode,
retaining a matching existing pin.
Blank password/opening-code fields keep their values.

An approved endpoint or certificate change blocks credential-bearing operations
and raises a Home Assistant **Repairs** issue. Open it to reconfigure, inspect,
and explicitly approve the new certificates. On older HA versions, follow the
issue's device-settings link and choose **Reconfigure**. Neither dismissal nor a
restart approves a replacement. A certificate changing while confirmation is
open requires another review. Expired, future, malformed or unsupported
certificates are refused; fix the device certificate/clock before approval.

In TLS mode, CGI and media endpoints are checked independently, even if they
present the same certificate. In TCP mode only CGI has certificate trust.
Pins, endpoint binding and expiry dates are stored only in
private config-entry data. Diagnostics and entity attributes contain no pins,
certificate bytes, passwords, opening codes or media keys.

## Network requirements

Manual setup uses direct unicast connections to the supplied IP: HTTPS **443**
and media TLS **8443** by default, or HTTPS **443** and fixed TCP **34567** when
the TCP experiment is explicitly selected. Different VLANs are supported when
routing and firewall rules allow the selected ports. No UDP broadcast discovery
is required for a manually entered password. A QR-imported password retains the existing
discovery-based device identity protection; it does not bypass that check.

**Device unreachable** means TCP could not be established; trusting a certificate
cannot open a refused/blocked port. The advanced inspection actions remain
available and now report the configured `certificate_port` and a fixed
`last_error_reason`, such as `connection_refused`, `network_unreachable`,
`timeout` or `tls_handshake_failed`. No port scan or automatic transport/port
fallback is introduced. The advanced media-port field applies only to TLS.
A different TLS media port must be established from actual device information
before being configured in Advanced options; it never changes TCP 34567.

## Validation requested

- New manual TLS setup: IP + local password + video; accept at most one certificate
  confirmation. Confirm moving video from the WelcomeEye card over trusted
  HTTPS. Close it and verify Philips can reopen. This beta's first hardware
  validation is limited to video; do not test microphone or opening controls.
- Existing entry: Reconfigure without typing fingerprints. Confirm entity IDs,
  enabled features and secrets survive restart. No repeated trust question when
  certificates and endpoint are unchanged.
- If setup fails, download fresh integration diagnostics. Report the displayed
  error, firmware and whether HTTPS/media inspection reached TCP and TLS.
  Do not share credentials or certificate fingerprints publicly.
- TCP mode: follow the [video-only beta procedure](release-044-beta1.md). Do not
  test sound, microphone or physical outputs in that mode.

Software tests use synthetic certificates, loopback TLS servers, and actual HA
config-flow/Repairs APIs with device I/O mocked. This beta's new setup path still
requires physical validation by a Connect 3 owner; no hardware result is claimed.

Rollback: reinstall **v0.4.3**, restart HA and keep the entry. The saved pins use
the existing field names understood by v0.4.3; private additional TLS metadata
is ignored by that version. A TCP-selected entry must be disabled before
downgrading and reconfigured for a working TLS endpoint before being enabled;
v0.4.3 does not support the new TCP choice. Do not delete a working entry.
