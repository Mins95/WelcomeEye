# 0.4.3-beta.5 — Connect 3 authentication preparation

Candidate, not yet published. Stable remains **0.4.2**.

The tester confirmed discovery/decryption on `IDS94E6SW`. This candidate adds
optional installation-QR import and a certificate inspection action to prepare
local authentication. The imported code's validity and HTTPS reads still need
hardware confirmation. **No Connect 3 camera, microphone or output controls.**

Existing beta.4 entries can use **Reconfigure**; no deletion or duplicate entry
is needed. Leave authCode/QR fields blank if unavailable. An owner may enter the
decoded text of their own installation QR locally; do not share it on GitHub.
The original QR is discarded, but its credential/device binding are retained
privately by HA. This is not a QR-image scanner or account-login flow.

Explicit certificate inspection, available even without credentials:

```yaml
action: welcomeeye_local.connect3_check_certificate
target:
  entity_id: sensor.YOUR_CONNECT3_STATUS
data:
  include_details: true
```

It returns TLS/metadata status and optionally `certificate_sha256`, using only
the configured CGI port. No HTTP or secret is sent. An observed fingerprint is
**not automatically trusted**; independently verify before entering it as a pin.
Read the displayed response of this single action execution. Script traces may
retain it; review before sharing.

With an existing local authCode or accepted installation QR and appropriate TLS
trust, the existing `connect3_check_access` action performs one read. QR imports
first require a matching discovery identity; `credential_identity_not_matched`
sends no CGI. `device_rejected` is not retried. No media starts on success.

The additional static findings and proof limits are in
[Connect 3 beta.5 authentication evidence](connect3-beta5-auth.md).
All new network tests use mocks and synthetic inputs, not the tester's hardware.

Rollback: redownload **0.4.3-beta.4** in HACS and restart HA. The experimental
entry and provisional identity remain compatible. If a QR was imported, clear
credentials through beta.5 Reconfigure before rolling back: beta.4 does not
enforce the new QR/discovery binding. No main/stable tag or old asset is replaced.
