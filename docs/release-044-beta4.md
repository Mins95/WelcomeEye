# 0.4.4-beta.4 — Connect 3 certificate validity diagnostic

The A331 owner now reaches the validity check, with
`certificate_invalid_validity` / `certificate_validity` and a decoded
`non_positive` serial. The accepted date encodings reach an equal/reversed
interval check before HA's clock is consulted. The actual device dates are
not yet available; no hardware video success is claimed.

This beta retains both parsed UTC dates and displays them in **TLS verification
result** when setup fails. It does **not** approve that certificate or alter
TLS, media negotiation, credentials, outputs, or other device families.
The dates are read-only; they are not added to entity attributes, standard
diagnostics, or logs. No extra network request is made to collect them.

## Tester procedure

1. In HACS, enable beta versions, download **0.4.4-beta.4**, then restart HA.
2. Keep the existing entry. Reconfigure it once with the same **QV TCP 34567**
   selection and HTTPS port **443**. Leave a saved password blank to retain it.
3. If rejected, expand **TLS verification result** and send the displayed
   **Reported validity start (UTC)** and **Reported validity end (UTC)**,
   along with the rejection reason. A screenshot is sufficient.

No Philips application operation, certificate file, terminal, OpenSSL command,
new scan, video attempt, opening test, or entry deletion is needed. Do not
change HA's clock to bypass this rejection. There is no need to resend a
password, opening code, or certificate fingerprint.

## Software validation and rollback

Synthetic signed certificates cover equal dates, reversed dates, the X.509
UTCTime 2049/1950 boundary and GeneralizedTime. These are test fixtures, **not
the actual A331 certificate**. Invalid periods remain rejected even with an
exact pin; valid and expired certificates retain their previous behavior.
HA flow tests verify read-only date display, no entry persistence on failure,
no media/authenticated request, and rejection of arbitrary text in date fields.

Rollback: redownload **0.4.4-beta.3** in HACS and restart HA. Existing entries,
pins and selected transports are unchanged. Stable **0.4.3** is untouched.
