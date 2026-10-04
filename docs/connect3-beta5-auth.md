# Connect 3 beta.5 — authentication preparation

Base: beta.4 `ef9f43e5d11c1194a96da30fe217f5783a8186f6`.
Branch: `feature/043-connect3-beta5-auth`. Analysis date: 2026-10-04.
This work contacted no device, HA instance or manufacturer service.

## Hardware evidence supplied by the tester

The beta.4 report describes one successful UDP request/reply, 616 bytes, one
decoded record, no decode errors: type `IDS94E6SW`, empty advertised firmware,
stream port 0, CGI 443, TLS media 8443, one channel. The separately reported
firmware is `V401.R001.A350.00.G0123.B025`, app 1.0.123.3(2), HAOS 18.2 /
Core 2026.9.3 / Python 3.14.6 / amd64. No UID or raw packet was provided.

This validates discovery/decryption for that observed device. Advertised ports
do not demonstrate reachability, TLS trust or authentication. Firmware `R001`
alone must not select the legacy Connect 2 backend. No raw hardware fixture is
invented from this summary. The empty-version/zero-port regression is synthetic.

## Installation QR path found in the APK

Same original Philips Door Connect XAPK as the [beta.4 analysis](connect3-analysis.md),
SHA256 `f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
Offsets below are DEX code_item offsets, not source line numbers.

| Location | Evidence |
| --- | --- |
| classes4.dex, `DeviceHelper.getDeviceQrCodeInfo`, `0x1cefd4` | Splits on ASCII spaces, ignores empty tokens; first four tokens feed DeviceQrCodeInfo |
| `DeviceQrCodeInfo.<init>`, `0x1bcb20` | Arguments map to AP name, UID, **authCode**, model, cloud type |
| `DeviceHelper.getDeviceQrCodeInfoV2`, `0x1cf3b0` | Gson parses DeviceQrCodeV2 directly; `u` is UID, `c` is authCode, `m` is model, `a` AP name, `d` config metadata |
| Instructions `0x1cf478`–`0x1cf480` | `getC()` feeds `DeviceAddInfo.setAuthCode()` |
| `DeviceHelper.dealWithQrInfo`, `0x1d26e4` | JSON path first, space format second, unrelated share-code path afterwards |
| `DeviceHelper.bindDeviceOnLan`, `0x1d2070` | DeviceAddInfo authCode is copied into the Device object |
| classes2.dex, `QvApi2.bindDevice`, `0x2be97c` | Supplied authCode passes through EncodeDevicePassword before use |

Only the parsing/credential assignment is reproduced. No bind, enrollment,
reset, Wi-Fi configuration or cloud API is called. The space importer accepts
exactly four tokens, stricter than the SDK. JSON supports the demonstrated
`a/u/c/m/d/v` fields, rejects duplicate/unknown fields and requires `u/c/m`.
Both imports are limited to `IDS94E6SW`, 2048 UTF-8 input bytes, UID 64 characters
and authCode 256 characters. URLs, sharing QR codes and other models are rejected.

**Limits:** this proves the SDK has an installation credential path, not that
the tester's physical QR contains one, nor that it remains valid after binding.
The app also retrieves authCode through authorization/cloud responses. The
installation code must not be confused with account password, dynamic password,
media key or opening code. APP_SUPPORT_LAN_ADD remains disabled in this app;
parsing a QR is not proof that cloud-free enrollment is supported.

The beta.4 local CGI header and password normalization are unchanged. There is
no credential generation, default-code attempt, renewal or authentication retry.

## Local implementation and privacy

The optional password-style `installation_qr` field is in Add/Reconfigure only,
never service parameters. The original QR/AP metadata are discarded. Its
authCode, device UID and fixed source label are retained in the private config
entry; the random HA entry identity is unchanged. Blank inputs retain existing
values. Clearing credentials explicitly removes the QR binding and TLS pin.
Conflicting QR/manual input and an imported QR for a different bound UID fail.

Before access/history with a QR credential, one fresh bounded discovery must
produce a unique matching UID and `IDS94E6SW`. Missing/mismatched/ambiguous
identity prevents CGI entirely. This is a consistency guard, **not authenticated
discovery**; the existing HTTPS trust/pin check remains mandatory. No advertised
port is automatically applied. The manual authCode path retains beta.4 behavior.
The discovery deadline is three seconds plus bounded socket close; the CGI read
then retains its twelve-second deadline. One operation per entry, no retry,
cancellable at unload, no startup I/O.

`connect3_check_certificate` performs one TCP/TLS connection to the configured
CGI endpoint, with a local inspection-only SSL context. No HTTP, authCode or
media bytes are sent. The existing bounded DER metadata reader is reused;
non-positive serials need no deprecated cryptography tolerance. It reports only
known CN labels, parser/sign status and sanitized errors. This is not trust
verification. TLS establishment/metadata failure remain separate observations.
Three-second operation deadline plus at most one second for socket cleanup.

Only explicit `include_details: true` returns the certificate SHA256. It is
never installed as a pin automatically. An independently verified pin can then
be entered in Reconfigure. Standard diagnostics and sensor attributes exclude
the pin, UID, QR, authCode, raw DER and remote arbitrary text. The new service
requires an identified administrator AND HA entity-control permission. HA may
retain explicit action responses in script traces; private config entries are
also persisted normally by HA and must not be shared.

No video, microphone, output operation, photo download, ring subscription or
R002 protocol change is added. APK function existence is not hardware validation.
