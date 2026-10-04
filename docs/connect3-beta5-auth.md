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

No video, microphone, output operation, photo download or ring subscription is
added. APK function existence is not hardware validation.

## Authentication re-audit and HTTPS evidence

The Door Connect DEX confirms the existing LAN header; there is no evidence for
changing its username, encoding or endpoint. Further exact code_item locations:

| Location | Evidence |
| --- | --- |
| classes4.dex `DeviceRequestHelp.initHeader`, `0x2337e4` | LAN path uses adminapp2, encoded authCode, passwordencode=1; HTTP-auth branch is capability/config dependent |
| classes2.dex `QvPlayerCore.playFormLan`, `0x28d71c`, `getEncodeKey`, `0x28d130` | Temporary device carries IP, CGI port and encoded authCode into the streamkey read |
| classes2.dex `QvEncrypt.EncodeDevicePassword`, `0x2f0988` | SHA256 UTF-8 for inputs shorter than 64 Java UTF-16 units; otherwise pass through |
| classes4.dex `HttpDeviceManager.E4`, `0x24c954` | Nonzero XML body/error is forwarded as a device error |
| classes2.dex `EmitterUtils.onError`, `0x2e945c`; `QvPlayerCore$15.onError`, `0x2883bc`; `QvPlayerCore.E`, `0x28ba18` | Device error string 401 is explicitly treated as authCode error |
| classes4.dex `RetrofitUtil.getRetrofit`, `0x2a50cc`; `OkHttpUtil.createBuilderWithCustomCA`, `0x2a175c` | CGI TLS branch uses no client KeyManager; optional device CA is public trust material |

The temporary QvDevice constructor (`classes2.dex 0x2e4478`) leaves supportTls
false; `getEncodeKey` does not copy the media TLS capability into this CGI
object. Even the custom-CA CGI builder calls SSLContext.init with **null client
KeyManagers** (`classes4.dex 0x2a1820`). A distinct client.bks/private-key path
exists for other clients; it is not imported or used here. The integration keeps
normal TLS trust or an explicit owner-verified pin; it never accepts credentials
over an unverified connection. No APK keys or certificates are redistributed.

The authCode getter only returns a field. Proven sources are installation QR
or enrollment/authorization responses; none derive a valid code from UID or
advertised ports. Cloud-only dynamic authorization is not added. Capability
124 and SDKConfig.IS_OPEN_AUTH guard a distinct HTTP-auth path, whose activation
is not established for the tester's device. HTTP 401 is therefore reported as
`http_unauthorized`, never mislabeled as a bad authCode or automatically retried.

Each access/history action now reports safe TLS/HTTP/XML stages and counters.
`request_sent_count` counts locally initiated HTTP header sends, not confirmed
device receipt. No response or transport exception triggers an access retry.
Device XML 401 gives `auth_code_rejected`; other device codes stay distinct.
Only a successful bounded read marks `authentication_status=accepted`.
`device_authenticated` describes the **last access/history observation**, not
retained login state or a trust assertion from discovery. Later failure or a
missing credential clears it; an inspection-only action does not establish it.
Stream keys, passwords, pins, URLs and response bodies are excluded from these
diagnostics. No auth flow, request command or media negotiation was changed.

`tests/test_connect3_https.py` sends actual HTTPS to a temporary loopback server
with synthetic credentials and an ephemeral certificate. It independently
checks POST /tdkcgi and the exact XML header, success, XML401 vs HTTP401, TLS
pin/trust rejection before any POST, malformed/empty responses, timeout and
cancellation. One access call sends at most one POST; all sockets are closed.
These are software transport tests, not a real Connect 3 authentication result.

## Common QV discovery with WelcomeEye / R002

The supplied WelcomeEye ARMv7 `libqv-p2p-v2.so` SHA256 is
`210402ce70a86a7d3ab5d25cecb8393b72d682ccd8576b02dfcb8a4458346d41`.
Its three 4096-byte key-derivation tables at `0x2922df`, `0x2932df`, `0x2942df`
match the Door Connect ARM64 tables at `0x2d031b`, `0x2d131b`, `0x2d231b`
bit for bit. `tools/verify_qv_kdf_armv7.py` emulates only the original
mathematical functions, with bounded memory and local primitive hooks. All
seven synthetic native ARM64 vectors also match the original ARMv7 output and
the production Python decoder. The repository contains no APK binary/table.

WelcomeEye `ParseData` at `0x354ec8` copies 520 bytes, and JNI at `0x29bce4`
uses the same record offsets: IP +0x64, stream +0x78, UID +0xc8, model +0x188,
channels +0x1a4, CGI +0x1a8, TLS media +0x1cc; firmware is replaced from +0x1bc.
This establishes a reusable decoder **when a QV datagram is actually observed**.
It does not prove that the R002 firmware responds to this broadcast or accepts
the CGI path. The public issue #7 has no reported QV datagram to authenticate.

The existing administrator-triggered R002 discovery uses its same single UDP
request and collection deadline. Decoding consumes the already collected bytes,
with no second reader/request. Optional metadata appears only in the explicit
service response; UID/IP never do, and raw bytes require the separate opt-in.
Standard diagnostics retain only bounded counters and fixed error labels.
R002 TCP 8765, its ambiguous 10/12-byte boundary, TLS fingerprint classification
and all physical/media restrictions remain unchanged.
