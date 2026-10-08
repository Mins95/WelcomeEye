# 0.4.4-beta.2 — Connect 3 certificate setup fixes

This prerelease fixes two certificate checks that could refuse a valid
certificate during initial setup or reconfiguration. Unknown noncritical
manufacturer extension values are no longer decoded as a known extension.
A successful system-CA/IP handshake is no longer followed by an incorrect
own-key signature check on a self-issued certificate.

Both bugs were reproduced with synthetic local TLS servers. **The actual A331
certificate cause has not yet been identified.** This beta does not claim that
the tester's configuration or TCP video now works on hardware.

TLS verification, expired-certificate rejection, key-strength requirements,
pin checks and the QV AES-256/SHA-256 credential gate remain enabled. No media
framing, audio, microphone, opening protocol or CRC32C dependency changes.
Stable 0.4.3 and beta.1 remain untouched.

## Retry setup

1. In HACS, show beta versions, redownload **0.4.4-beta.2**, and restart HA.
2. Keep existing entries if present. If already removed, add **one** Connect 3
   using its known IP, local connection password and **QV TCP 34567
   (experimental)**. HTTPS remains on 443; no media certificate is requested.
3. If setup succeeds, approve the displayed local certificate/TCP confirmation.
   Close Philips players, open video **once**, download diagnostics, close with
   X and verify Philips can reopen.
4. If setup still fails, expand **TLS verification result** and send the six
   displayed values. They are available even before entry creation. Do not
   delete another entry, change ports, scan the network or repeat video tests.

The result contains only endpoint label, status, fixed error reason, serial-sign
status and public-key type/size. It contains no IP, UID, fingerprint, certificate
or secret, and is not stored in configuration options or standard diagnostics.
Pending setup still exists in HA's authenticated flow context until cancelled.

TCP remains video-only, explicitly selected, with no automatic fallback. Its
fixed-IV protocol does not provide TLS-equivalent media authentication or
integrity. See [TCP scope](release-044-beta1.md) and
[automatic certificate setup](connect3-auto-tls.md).

## Rollback

Redownload beta.1 and restart HA; transport settings and pins are compatible.
Its original certificate check may still block setup. Before downgrading to
stable 0.4.3, disable a TCP-selected entry: stable requires a working TLS media
endpoint. Do not delete working entries or their credentials.
