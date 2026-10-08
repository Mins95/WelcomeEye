# 0.4.4-beta.5 — explicit approval for zero-duration Connect 3 certificates

The A331 owner's beta.4 result reports the same certificate date twice:
`1969-12-31T16:00:27+00:00`. This explains the `certificate_invalid_validity`
rejection. It is unrelated to HA's current clock.

This beta supports an **explicit exception for a zero-duration certificate**.
The existing trust screen adds an unchecked acceptance box and read-only dates.
Approval is rechecked against exactly the certificate shown before it is saved.
Each endpoint's private record binds the fingerprint and original dates; it
survives restart and does not authorize a different certificate or endpoint.

The certificate dates are not rewritten or declared valid. TLS encryption and
the exact approved fingerprint remain mandatory before CGI credentials. Initial
trust is still TOFU, not independently authenticated manufacturer identity.
Bounded DER parsing and public-key strength remain checked. Ordinary expired,
future, malformed, weak-key and reversed-date certificates remain refused.
No TLS protocol/cipher setting, CRC32C dependency, media protocol, opening
control or other model is changed. TCP media remains experimental and video-only.

## One test on Yohan's A331

1. Download **0.4.4-beta.5** in HACS and restart Home Assistant.
2. Reconfigure **one existing Connect 3 entry**, preferably the one whose HTTPS
   access already succeeded. Keep HTTPS **443**, **QV TCP 34567**, and video.
   Leave the saved password blank to retain it; do not delete the entry.
3. Review and accept the local certificate/TCP consent and the extra
   **zero-duration certificate** checkbox. No console or certificate file is needed.
4. Close Philips video players, try the HA video once, and download the
   integration diagnostics immediately, whether the video succeeds or fails.
5. Close the HA video with its X, then check that Philips can reopen video.

If setup still fails, send the expanded **TLS verification result**. Do not
retry repeatedly or test physical openings. The second device's earlier XML
401 is a separate local connection-password rejection, not this date problem.

## Evidence and limits

The tests use synthetic signed certificates with the exact reported dates and
a non-positive serial, not the actual device certificate. They exercise local
TLS inspection, explicit consent, persistence, verified HTTPS authentication,
QV TCP negotiation, video frames and cleanup. Omitted/refused consent and a
changed certificate must prevent credentials. **A331 hardware video remains
unconfirmed** until the owner tests this version; no physical output was tested.

The per-certificate exception remains private. No fingerprint, raw certificate,
credential or exception record is added to entity attributes or diagnostics.
CGI and TLS media are approved independently. No media certificate is requested
for the explicitly selected TCP transport.

Rollback: download **0.4.4-beta.4** in HACS and restart HA. Keep the entry; that
version will refuse the zero-duration certificate again, without deleting its
saved credentials. Stable **0.4.3** and older release files are untouched.

Implementation references: [aiohttp exact certificate fingerprint verification](https://docs.aiohttp.org/en/stable/client_advanced.html#example-verify-certificate-fingerprint),
[X.509 validity representation](https://datatracker.ietf.org/doc/html/rfc5280#section-4.1.2.5).
