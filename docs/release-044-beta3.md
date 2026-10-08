# 0.4.4-beta.3 — Connect 3 setup and TCP video

This candidate consolidates automatic certificate approval and the explicit
**QV TCP 34567** video trial. No console, PEM file or manual fingerprint is
needed to add or reconfigure an entry.

## What changed

Certificate inspection now distinguishes system-CA authentication from explicit
trust on first use (TOFU). The system-CA path still verifies the chain and IP.
For local certificates, a successful TLS handshake, bounded certificate parsing,
validity dates and public-key strength are checked before presenting approval.
The original certificate is pinned after approval and checked before credentials
on every new connection. A changed pin still blocks access and needs approval.

The local-pin path no longer attempts to reconstruct a certification authority:
equal issuer/subject names do not prove a self-signature, certificate signature
algorithms are not TLS handshake algorithms, and CA extension semantics do not
establish trust in a directly approved certificate. Non-positive serials remain
readable without rewriting the certificate or suppressing warnings. These
properties never establish the device model or independent identity.

Setup errors retain their exact **verification stage** and safe partial metadata.
No raw certificate, fingerprint, IP, password or key is included in that result.

The APK's existing HTTPS stream-key → QV setup → encrypted PLAY → media → teardown
path was rechecked. No media framing change was justified. TCP remains explicitly
selected and video-only; HTTPS remains verified. TLS 8443 keeps its existing
audio and controls. No automatic fallback or port scan is added.

## Software evidence and limits

A synthetic legacy `eziotest` certificate with serial zero is tested through the
configuration flow, approval, saved entry, real loopback HTTPS authentication,
QV TCP negotiation, H.264 decoding and three open/close cycles. Refused approval
and a changed certificate are also tested to send no authenticated request.
The fixture is generated for the tests; it is **not the hardware certificate**.

The fixture includes an opaque noncritical CertificatePolicies extension. Against
the published beta.2 inspector, the same peer reproduces `certificate_malformed`
with unknown serial/key fields, despite readable metadata and working pinned
HTTPS. The new inspector accepts it for explicit approval; it does not use that
extension to validate an issuer chain or authenticate an identity.

The A331 hardware report proves that the old metadata reader and HTTPS handshake
work, while beta.2 setup rejects the certificate. It does not include the actual
DER certificate, so the exact rejected property on that device is still unknown.
The consolidated path is software-tested; **A331 TCP video is not yet physically
validated**. An XML error 401 remains a separate local-password rejection.

## One test on the A331 installation

1. In HACS, show beta versions, install **0.4.4-beta.3**, then restart HA.
2. Keep existing entries. Reconfigure **one** Connect 3, choosing **QV TCP 34567
   (experimental)**, HTTPS port **443**, and live video. If the entry was already
   deleted, add it using its IP and its own local connection password.
3. Approve the local certificate and the experimental TCP transport when asked.
4. With Philips players closed, open the HA video once. Download the integration
   diagnostics immediately, whether it succeeds or fails.
5. Close the video with its X, then check that Philips Door Connect can reopen it.

If setup fails, send the fields in **TLS verification result** instead. No second
video attempt, network scan, device deletion or physical opening test is needed.

## Rollback

Reinstall **0.4.4-beta.2** through HACS and restart HA. Existing entries, pins and
transport choices are compatible, although beta.2 may refuse reconfiguration.
Before returning to **0.4.3**, disable a TCP-selected entry: that stable release
does not implement Connect 3 TCP media. Do not delete the entry or credentials.

Technical references: [APK media evidence](connect3-beta7-video-evidence.md),
[automatic TLS setup](connect3-auto-tls.md),
[aiohttp fingerprint checks](https://docs.aiohttp.org/en/stable/client_advanced.html#example-verify-certificate-fingerprint),
[TLS trust configuration](https://docs.python.org/3/library/ssl.html#ssl.SSLContext.verify_mode).
