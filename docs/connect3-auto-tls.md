# Connect 3 — automatic local TLS (0.4.4-beta.7)

TLS media remains the default. This beta also offers an explicit **QV TCP 34567
(experimental)** mode with video, sound and optional microphone/opening trials. Its protection is not equivalent
to TLS. Video and release back to Philips were confirmed on one A331 installation;
audible sound, microphone and physical openings still need validation.

## Installation

1. Install **v0.4.4-beta.7** through HACS (show beta versions), then restart Home
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
   the HTTPS certificate is unknown. The sound received from the outdoor panel
   shares the video connection. Use the card's speaker button to unmute; the
   player starts muted. To test microphone and openings, explicitly enable
   **Allow microphone and controls over TCP (experimental)**. Opening buttons
   also require **Enable strike and gate** and the separate Philips opening code.
   Existing entries keep these TCP controls disabled until explicitly enabled.
5. Use the existing WelcomeEye camera card. No OpenSSL, PEM file, certificate
   action, or manually entered fingerprint is required.

Approval is **trust on first use (TOFU)**. It identifies the certificate you
observed and approved; it is not independent proof of the intercom's identity.
Cancel if you are unsure about the endpoint or network. No credential is sent
during inspection or approval.

TCP uses native QV credential encryption, but lacks TLS peer authentication,
integrity and replay protection; microphone audio is only partly encrypted.
Use a trusted local network. Each opening is explicit and never automatically retried.

Some Connect 3 certificates report **identical start/end dates**, giving no
usable validity period. The approval screen then also requires **Accept these
certificates despite their zero validity duration**. This is an explicit
exception for those exact certificates and dates, not an assertion that the
dates are valid or the manufacturer identity is authenticated. The original
certificate is not modified. Each endpoint's fingerprint is still checked
before credentials; a different certificate requires new approval. The saved
exception survives restart and is not requested again for an unchanged setup.

Beta.6 also supports an explicit **RSA 1024-bit key** exception. The trust screen
shows a separate unchecked acceptance box when that exact legacy key size is
observed. If the certificate also has zero-duration dates, both exceptions need
approval. RSA 1024 remains weaker than current keys: pinning checks which
certificate is presented but does not repair or strengthen its private key.
The private approval record binds the key type/size to the exact SHA-256 pin and
approved endpoint. It survives restart, cannot authorize a replacement, and is
removed for an active endpoint if a stronger certificate is approved later.
No TLS cipher, protocol version or security level is lowered by the integration.

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
open requires another review. Ordinary expired/future, reversed-date, malformed or unsupported
certificate structures and unusable/weak public keys outside the explicit RSA
1024 exception are refused; fix the device
certificate/clock before approval. A local pin directly trusts the approved
certificate; it does not authenticate its issuer, Common Name or model. The
certificate's own signature and CA-extension semantics are not reinterpreted as
another authority check. Normal system-CA/IP validation remains with the TLS
library. [Policy and complete test path](release-044-beta3.md).

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

The A331 owner reports both dates as `1969-12-31T16:00:27+00:00`, a non-positive
serial and an RSA **1024-bit** key. Beta.6 covers these three properties together
with explicit certificate-specific exceptions. Do not change HA's clock.
The accompanying beta.5 entry diagnostics still show TLS 8443 with no saved pin
and zero media attempts: rejected reconfiguration did not replace the old entry.
The original certificate has not been supplied; tests use signed synthetic
certificates with the reported properties, not hardware DER.
The owner's beta.6 report confirms TCP video: 372 decoded frames without errors,
first image in 1.052 seconds, normal session close and Philips access afterwards.
That session also received 361 audio frames which beta.6 deliberately ignored.

- New manual TLS setup: IP + local password + video; accept at most one certificate
  confirmation. Confirm moving video from the WelcomeEye card over trusted
  HTTPS. Close it and verify Philips can reopen. The existing TLS path is unchanged.
- Existing entry: Reconfigure without typing fingerprints. Confirm entity IDs,
  enabled features and secrets survive restart. No repeated trust question when
  certificates and endpoint are unchanged.
- If setup fails, download fresh integration diagnostics. Report the displayed
  error, firmware and whether HTTPS/media inspection reached TCP and TLS.
  Do not share credentials or certificate fingerprints publicly.
- TCP mode: retain the accepted entry and certificates. On one device, enable
  the TCP controls above, open video and unmute to check sound. Enable the
  microphone briefly, check the outdoor speaker, then turn it off. If safe,
  test the strike and gate once each and report the actual physical result.
  Download diagnostics immediately, close the player and verify Philips access.

Software tests use synthetic certificates, loopback servers, and actual HA APIs.
No real device or physical output is contacted by these tests. Hardware claims
above are limited to the owner's beta.6 video report.

To undo only beta.7's TCP changes, reinstall **v0.4.4-beta.6**, restart HA and
keep the entry. TCP video and existing pins remain; TCP audio/controls are disabled.

Rollback: reinstall **v0.4.3**, restart HA and keep the entry. The saved pins use
the existing field names understood by v0.4.3; private additional TLS metadata
is ignored by that version. A TCP-selected entry must be disabled before
downgrading and reconfigured for a working TLS endpoint before being enabled;
v0.4.3 does not support the new TCP choice. Do not delete a working entry.
