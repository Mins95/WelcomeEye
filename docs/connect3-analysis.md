# Connect 3 / Philips Door Connect — beta.4 evidence

This document records the beta.4 investigation. The subsequent hardware
discovery report and QR authentication findings are in
[beta.5 evidence](connect3-beta5-auth.md).

This is a separate QV experimental backend, not a new name for Connect 2 R002.
Analysis date: 2026-10-01. No intercom, production HA, manufacturer account or
cloud endpoint was contacted. All request/response fixtures are synthetic.

## Provenance and baseline

Base: `v0.4.3-beta.3`, commit `cf3e8cedb79ac72b5bad3b04bfbdaf5a9f3acc9c`.
Branch: `feature/043-connect3-experimental`. The supplied handoff was read and
important interpretations checked in the original DEX and ARM64 libraries.
Only selected methods/functions were dumped; no proprietary binary or complete
decompilation is shipped in the repository or HACS ZIP.

| Input | SHA256 |
| --- | --- |
| Philips Door Connect XAPK, 1.0.123.3(2), versionCode 7 | `f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c` |
| Base APK, package `com.extel.philipsdoorconnect` | `a7b45d1c8d48ec4d453992803728ff3226280481ed5b21fe755c4024878119d2` |
| ARM64 split APK | `769d9439d957835f9aa1a472c8aa9cc0e176edb5bae29c37b43d93e2a008dda1` |
| ARM64 `libqv-p2p-v2.so` | `4aa8fe8a7c4895225754b0c61ad6178796357e106ba41d378085a7bee71f59d5` |
| ARM64 `liblive_player.so` | `bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68` |

Native addresses below are ELF virtual addresses in these ARM64 files. DEX
addresses are code_item offsets in the named DEX, not Java source line numbers.

## Discovery: completed algorithm, device response still to verify

`libqv-p2p-v2.so`:

| Function / location | Evidence |
| --- | --- |
| Initializer `0x55bdb0`–`0x55bdc8` | Constructs the global string at `0x734f90` from `ASZENO.SEARCH.V4`, literal at `0x2b5933` |
| `LanSearch::OnSearch`, `0x55c544` | Appends `.1`, sends exactly 18 ASCII bytes (no NUL) to broadcast UDP 5000, through its 5003 listener |
| `LanSearch::Start`, `0x55c1c4` | Listener ports 5001 and 5003 |
| `LanSearch::Parse`, `0x55cd18` | Accepts V4.1 first, then V4; skips the matching literal prefix |
| `ParseNet`, `0x55cf18` | 40-byte envelope; LE u32 seed length at +8, encrypted length at +12; seed then ciphertext; seed bounded to 1024 |
| `ParseV4`, `0x55cfa4` | `AESSecret` version `1.0.0`, 256-bit key, CBC IV sixteen ASCII `0` bytes |
| `ParseData`, `0x55d1f4` | Copies one 520-byte record; deduplicates UID/address/stream/CGI ports |
| `lanSearchDeviceGet` JNI, `0x4231dc` | Record stride `0x208`; maps fields to QvLanSearchInfo |

The earlier R002 UDP transport is reused **only** because this independently
verified exchange is identical. No R002 TCP probe, OWSP login or media command
is reused. One broadcast, three-second collection plus at most one second for
close, four replies of at most 2048 bytes, 64 datagrams total, source restricted
to the configured IPv4 address. Ports are bound exclusively; a busy port fails
without a broadcast. There is no retry or startup discovery.

Supported record offsets: address `0x64`, stream port `0x78`, UID `0xc8`, type
`0x188`, channel count `0x1a4`, CGI port `0x1a8`, version suffix `0x1bc`, TLS media
port `0x1cc`. `QvLanSearchInfo.ipValueToStr`, classes2.dex `0x2e59fc`, prints the
least significant byte first, matching IPv4 octets in the record. JNI copies
the version at `0x108` then **overwrites it** with the string at `0x1bc`; beta.4
does not assume the former is the exposed version. Text parsing is bounded,
NUL-terminated, UTF-8, and rejects control characters. Advertised address must
match the configured/source address. Advertised ports never silently change
the configured HTTPS endpoint.

The decoder accepts a 528-byte CBC container for the 520-byte structure and
does not interpret the final eight decrypted bytes as a proven padding format.
Other encrypted record lengths are rejected with `unsupported_record_size`.
Envelope bytes 0–7/16–39 are not assigned invented meanings. A hardware reply
is still needed to validate this record/container revision and text widths.
Encryption is **not authenticated**: a decoded record does not prove a model,
UID ownership, trusted peer or successful login. No auto-identification follows.

### Independent KDF verification

`AESSecret::GenerateKey` overload `0x64ed50`, `GenerateSeedKey` `0x64ee3c`,
`GenerateSeedKeyAndBox` `0x64f728`, `GenerateSourceData` `0x64fab8`, expansion
`0x64ea2c`. The Python implementation retains only reduced lookup constants
needed for wire interoperability, not the original 12 KiB tables or any device
secret. Mutable native global substitution state was replaced by per-call data.

Seven synthetic seeds were passed to the **original ARM64 mathematical
functions under Unicorn**, with only memory/allocation/string primitives
allowed. All seven resulting keys match the production Python implementation,
including empty seed, one byte, 32 bytes, 33 bytes and all-FF. Native behavior
ignores seeds longer than 32 bytes (up to the envelope's 1024 limit); this is
reproduced, not proposed as secure key derivation. These are discovery keys,
not a way of deriving an owner's authCode.

Reproduce locally with `pyelftools` and `unicorn`, and the owner's extracted
ARM64 library (the script checks its exact SHA256):

```sh
python tools/verify_connect3_kdf.py /path/to/libqv-p2p-v2.so
```

`tests/fixtures/connect3_kdf_native.json` contains only those synthetic
input/output vectors. Encrypted packet tests are explicitly synthetic
round-trips and **do not constitute an independent real-device packet capture**.

## AuthCode, stream key and HTTPS

`AUTH_MANAGER_V2` is enabled during application startup. Separately,
`APP_SUPPORT_LAN_ADD` is false: a LAN playback path does not imply that account
bootstrap or enrollment can be performed without the manufacturer.

`QvAuthManager.dealWithDynamicPwd` (classes2.dex `0x2c18d4`, instructions around
`0x2c19a8`) assigns the response `authCode` to `QvDevice.authCode`, alongside
dynamic password, expiry and dataEncodeKey. `QvApi2` also consumes authCode from
bind/free-register responses. Those are different fields from account login,
token and `defaultOutAuthCode` (opening authorization). No embedded private
key, app account or cloud token is reused here.

The implemented subset is the LAN/local `DeviceRequestHelp.initHeader` branch
(classes4.dex code_item `0x2337e4`): `security=username`, `username=adminapp2`,
`passwordencode=1`, normalized device authCode. `QvDevice` initializes security
to `username`; `QvEncrypt.EncodeDevicePassword` uses lowercase SHA256 UTF-8 for
nonempty strings shorter than 64 Java UTF-16 units, otherwise passes through.
The optional credential is not guessed from the opening code. The owner must
already possess their device's authCode from authorized app data; there is no
verified normal settings-screen export, automated bootstrap or renewal yet.

`QvPlayerCore.playFormLan` (`0x28d71c`) calls `getEncodeKey` (`0x28d130`), which
builds a temporary device with address, username, normalized password and CGI
port. `DeviceRequestHelp.getSecret` (`0x2319f0`) builds the XML envelope for
`get.device.streamkey`. DTO annotations establish `<envelope><header>…</header>
<body><command>…</command><content/></body></envelope>`. DeviceApi declares
`POST /tdkcgi`, SDKConfig.CGI_PROTOCOL is `https`, and content type is
`application/xml;charset=utf-8`. The parsed reply is body/error plus content
`key`, optional `tdc` and `synctime`. These secrets are never returned by HA;
`connect3_check_access` reports only that a nonempty key was received after
error=0. This is a CGI acceptance result, not working media or model proof.

Different SDK branches (`httpauthen`, HS, transport/client certificate handling)
are not silently emulated. HTTPS uses normal trust or a separately verified
SHA256 certificate pin supplied by the owner. No automatic trust-on-first-use,
redirect, HTTP downgrade, APK client private key, environment proxy or cookie
store. If firmware requires another auth branch/client certificate, the read
fails; beta.4 does not bypass it. Normal dependency versions and CRC stay intact.

## History is implemented; photo download is not

`DeviceRequestHelp.getRecordSession` classes4.dex `0x231900` sends:
`get.record.session`, content/record with `filetype=picture`, `occurtype=all`,
`channel` (1-based CGI channel), `starttime`, `endtime`, `stream=all`.
`QvDateTime.parseXml` (`0x223240`) uses literal lowercase `t` and `z`, without
timezone conversion. This corrects an initially plausible but wrong inference
of a space-separated wire timestamp. The action accepts monitor wall time
`YYYY-MM-DD HH:MM:SS` and converts to the app's syntax.

Session response content/record/id feeds `get.record.message` (`0x23181c`).
`HttpDeviceManager.j2` (`0x24a7dc`) requests further pages while `page != 0`.
`QvDeviceXmlParsing.readRecordList` (`0x24fa7c`) reads data records including
channel, filesize, idf/ids/idx, filetype, occurtype, start/endtime and filename.
Beta.4 allows at most one session request plus four page reads within twelve
seconds, 256 KiB per XML, 128 distinct records, a 24-hour query window. Duplicate
records are ignored; a page cap reports `history_complete=false`, not success
for a complete transfer. A failed request is never retried. Search session IDs
and records are ephemeral; all HTTP resources close on failure/cancellation.
No undocumented session-delete request is invented.

This action returns metadata, **not JPEGs**. It does not claim `occurtype=event`
means a doorbell, correlate timestamps to a ring, or replace a stored photo
with a live snapshot. The SDK download path continues from PlaybackModel /
QvMediaFile / QvFileCore to `QVDownloadOpenFile` (`0x80b3f4`), then a proprietary
file stream. `filew://` is its local output descriptor, not an HTTP photo URL.
No cache/ImageEntity is created until complete authenticated file transfer and
ring correlation can actually be implemented. `File.getTotalSpace()` is not
used as a completed-file test.

## Deeper native results and exact remaining blockers

The ARM64 `liblive_player.so` path was followed beyond exported function names:

| Function | Address | Result |
| --- | --- | --- |
| QVPlayerOpen / QVPlayerStart | `0x8183cc` / `0x81854c` | Dispatch through native object virtual methods; not RTSP |
| IQUIIStreamLive::OnStart | `0x5440a8` | Checks device protocol support/selection before choosing old openStream or CQUIIStreamLive; obtains three key strings from IQUIIDevice |
| CQUIIStreamLive::SendSetup | `0x4a78b8` | 32-byte setup buffer, first byte `0xa9`, initially zero elsewhere |
| CQUIIStreamBase::OnRecvSetup | `0x4a4034` | Reads response status byte +9 and encryption selector +10; supported selectors 0–2, then virtual next stage |
| CQUIIStreamBase::SetKey | `0x4a6378` | Stores distinct strings at +0x348/+0x368/+0x388; not a single discovery AES key |
| CQUIIStreamBase::EncryptData | `0x4a41c4` | Separate write/read key buffers; AES-CBC when enabled; SetIvec and GetKeyLen depend on session state |
| CQUIIStreamBase::SHA / GetSHALen | `0x4a53c0` / `0x4a4c40` | SHA256 when mode 1, digest length 32; mode 0 has no digest |
| CQUIIStreamBase::GetExtDataLen | `0x4a5288` | Adds digest and rounds to the negotiated encryption block size |
| CQUIIStreamFile::SendSetup / SendOpen | `0x54a4f8` / `0x54a5b8` | File session setup plus 32-byte open structure, username/password joined by `&&`, filename or packed range metadata, digest and conditional encryption |
| CQUIITalk::SendSetup / SendOpen | `0x550a20` / `0x550adc` | Separate talk stream setup/authentication; existence does not establish negotiated audio format or safe sharing |

The blockers are now concrete: selection and negotiation of the two connection
protocol branches, derivation/assignment of the three session keys and IV,
authenticated file/live framing after open, completion/keepalive/close state,
and the actual firmware-selected audio mode. Copying only the 32-byte hello
would not implement these. Media TLS follows the advertised tlsMediaPort when
selected; 34567 is the non-TLS path. Port 8765 is not imported from R002.

Push FCM handling and player/session callbacks remain separate evidence. No
reader-closed local ring subscription has been established for Connect 3;
therefore no ring state, stale event replay or fabricated ring-image event is
added. Transparent session order 4 and `set.device.opendoor` exist in separate
paths; their mapping/equivalence is unproven. Neither is in the backend's
allowlist. No physical command can be sent, queued or replayed by Connect 3.

The SDK's stop/release distinction remains relevant to a future native transport.
This beta opens no media session; HTTP and discovery transports are short-lived,
single-operation, cancellable and closed before the operation task completes.

## Privacy and API boundaries

Connect 3 uses an explicit family choice before any old login. Unknown persisted
families/variants fail closed; reauth cannot fall into legacy validation.
Reload and address reconfiguration preserve the random provisional config
identity, which is expressly not a hardware UID or IP hash.

One diagnostic sensor, no camera, mic, photo entity, ring sensor or physical
button. The card still accepts only camera entities, so the Connect 3 status
sensor cannot acquire legacy controls. All three actions require HA entity
control and an identified administrator. Direct backend dispatch is restricted
to discovery/access/history. Optional metadata goes only into the current
action response, not hub summaries, sensor attributes or downloaded diagnostics.
HA can retain responses in script traces: review them before public sharing.
Credentials live only in the owner's normal HA config entry, never service
arguments, options, diagnostics, action response or code. Do not publish the
private `.storage/core.config_entries` file.

References for host APIs: [HA config flows](https://developers.home-assistant.io/docs/core/integration/config_flow/)
and [aiohttp certificate fingerprint verification](https://docs.aiohttp.org/en/stable/client_advanced.html#example-verify-certificate-fingerprint).
HA API behavior is additionally exercised by `verify_connect3_runtime.py` in
actual 2026.7.3 and 2026.9.3 containers with device networking mocked.
