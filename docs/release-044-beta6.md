# 0.4.4-beta.6 — Connect 3 legacy certificate approval

Yohan's latest TLS result identifies an **RSA 1024-bit key**, a non-positive
serial and identical validity dates in 1969. Beta.5 refused the key before its
approval screen. This beta handles those properties together.

The setup screen adds explicit acceptance of the RSA 1024-bit key, alongside
the existing zero-duration date acceptance. Both are required for this case.
Approval is saved privately for the exact certificate and endpoint. Every new
credential-bearing connection still checks the approved fingerprint; a changed
certificate cannot inherit either exception. The legacy key remains weak:
pinning does not strengthen it or independently authenticate the device.

No global TLS setting, cipher/security level, media protocol, CRC32C dependency
or physical command is changed. Other weak key types/sizes remain refused.
QV TCP 34567 remains explicit, experimental and **video only**.

## One-device test

1. Install **0.4.4-beta.6** through HACS and restart Home Assistant.
2. Reconfigure one existing Connect 3 entry whose HTTPS password was accepted.
   Keep HTTPS **443**, select **QV TCP 34567**, and enable video. Keep the entry
   and existing credentials; blank password fields retain their saved values.
3. Review and accept the displayed local certificate/TCP consent, the
   **RSA 1024-bit key**, and **zero-duration validity** exceptions.
4. Close Philips video players. Open HA video once, then download the integration
   diagnostics immediately, whether it succeeds or fails. If configuration still
   fails, send the expanded **TLS verification result** instead.
5. Close HA video and check that Philips can open it again. No physical-opening
   or microphone test is requested in this TCP mode.

The supplied beta.5 diagnostics still show TLS 8443, no configured pin and zero
media attempts. This is the preserved entry after a rejected reconfiguration,
not a new video-session failure. The second device's earlier XML 401 remains a
separate local-password issue.

## Validation and limits

Tests use signed synthetic certificates combining the reported properties,
local TLS/HTTPS and QV servers, private approval persistence, denial before
credentials, changed-certificate rejection, video cycles and cleanup. They do
not contact Yohan's devices or reproduce the actual certificate bytes. Successful
video on his A331 remains to be confirmed.

Rollback: download **0.4.4-beta.5** through HACS and restart HA. Keep the entry;
that version again refuses RSA 1024 during reconfiguration. Stable **0.4.3**,
main and all previous release assets remain unchanged.
