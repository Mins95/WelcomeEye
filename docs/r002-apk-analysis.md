# R002 APK audit — evidence and limits

Audited 2026-09-26. Application **com.extel.philipswelcomeeye 6.1.58.24**, versionCode 63, compile/target SDK 36, min SDK 26. This is the existing workspace extraction, not a new firmware capture. A newer firmware can use a path this APK selects through metadata; names alone do not establish that selection.

**Follow-up, 2026-09-30:** the [targeted native audit below](#follow-up-2026-09-30--qv-discovery-and-media) identifies the QV UDP discovery request, advertised port fields, CGI credential construction and part of the QV media framing. These are APK findings, not confirmation that the issue #7 firmware selects that path. Earlier statements about unresolved native framing describe the September 26 audit.

**Subsequent implementation:** at the maintainer's explicit request, the replacement beta.3 includes a [bounded UDP discovery action](release-043-beta3.md#next-test--qv-udp-discovery). Statements below about no implementation/execution describe the static audit itself. The service is now implemented and software-tested, but no real R002 response or hardware validation is claimed. CGI, credentials, decryption and media remain outside this change.

## Inventory and reproducibility

Workspace extraction: `C:\Users\grego\Documents\Codex\2026-09-08\je-x20\work\decompiled`. Java paths below are relative to `sources/`; manifest/assets are in `resources/`. No smali tree was present. Inventory: 10,772 Java files, four DEX files (`classes.dex` through `classes4.dex`), 78 asset entries and 17 native ARMv7 shared libraries in the XAPK ABI split. DEX provenance is retained in JADX comments. The packaged libraries include live_player, glnkio, qv-p2p-v2, asp, WebrtcAudio, OpenSSL and FFmpeg components. No proprietary source or binary is redistributed here.

| Input | SHA256 |
| --- | --- |
| `work/xapk/com.extel.philipswelcomeeye.apk` | `ff205ff0d24527b912d5c227aa80f8a6500979393ec0cefa62e35082173d6b52` |
| `liblive_player.so` (6,542,556 bytes) | `9ce7549858e01dd73d858cb956a2e082fbe828675a830730bd932692838386fe` |
| `libglnkio.so` (1,481,388 bytes) | `25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992` |
| `libqv-p2p-v2.so` (4,705,604 bytes) | `210402ce70a86a7d3ab5d25cecb8393b72d682ccd8576b02dfcb8a4458346d41` |

The last three are in `work/xapk/config.armeabi_v7a.apk`, `lib/armeabi-v7a/`. ELF dynamic symbols and only the pertinent JNI play / PreviewSnapshot routines were disassembled with Capstone. ARM PLT call targets were resolved against `.rel.plt` (32-byte header, 16-byte entries), checked against actual instructions; no whole-binary disassembly or port fuzzing.

## FACT: separate LT and QV player paths

`com/quvii/compathlt/QvCompatLtManager.java:275`, `createPlayerCore`, constructs `QvLtPlayerCore`, assigns channel/UID and LT encryption/password from device metadata. `QvLtPlayerCore.java:1091`, `snapShot`, calls the active renderer's `snapShotCompat(getAddress(), ...)`. It is not retrieval of the monitor's stored visitor photo. Historical glnkio remote-file kinds 333/1200/336/1203/1205 belong to the LT investigation; no xref established their use by R002 or by TCP 8765.

`com/quvii/openapi/QvOpenSDK.java:1177`, `createPlayCore`, separately creates `QvPlayerCore` with local/online, HS and protocol-type conditions. This is a real alternative code path, not just a matching string. The firmware string R002 was not tied to either selector in this audit.

## FACT: QV direct live path and port 34567

Call chain:

`QvPlayerCore.playFormLan` (`com/quvii/core/QvPlayerCore.java:1161`)
→ `getEncodeKey(localIp, user, encodedAuthCode, localPort, callback)`
→ success callback at line 914
→ `startPlay(..., supportTls ? tlsMediaPort : SDKConst.DEVICE_DEFAULT_STEAM_PORT, streamKey, ...)`
→ `startPlay` at line 3162
→ renderer.

`com/quvii/publico/common/SDKConst.java` defines default stream port **34567** and CGI port **443**. `startPlay` builds a `quii://` URL carrying user/password, address/port, `mode=real`, channel `idc`, stream `ids`, `ap=2` and optional TLS flag, then passes the URL and separate encoding key to the renderer. These are protocol descriptors, not standard RTSP URLs. Do not log actual URLs.

`com/quvii/qvplayer/publico/entity/QvPlayRender.java:182` routes to `glFrameRenderer.startPlay` or native `startPlayVideo`; `QvRender.java:116` provides another renderer JNI route. `QvJniApi.java:34` loads **liblive_player.so**.

Resolved native evidence in that library:

- `Java_com_quvii_qvplayer_publico_entity_QvRender_startPlayVideo`, address **0x2f5198**, 404 bytes.
- calls `jstringToChar` (0x2f51c0), `QVPanelCreate` (0x2f5268), `QVPlayerSetKey` (0x2f5274), `QVPanelViewOpen` (0x2f5280), `QVPanelViewStart` (0x2f5288).

**Implication:** a 34567 listener is compatible with a QV player hypothesis. It is not proof that the tested R002 uses it, that legacy credentials work, or that TCP 8765 is its media transport. The underlying QV socket framing/authentication has not been reconstructed sufficiently to implement.

## FACT: CGI stream-key path, not an R002 login implementation

`QvDeviceCore.java:341`, `getDeviceSecret`, delegates to `com/quvii/qvweb/device/HttpDeviceManager.java:3484`, then `DeviceApi.getDeviceSecret(DeviceRequestHelp.getSecret(device))`.

`com/quvii/qvweb/device/api/DeviceApi.java:155` declares a POST to `HttpDeviceConst.CGI_ADDRESS`; `common/HttpDeviceConst.java:5` resolves this to **`/tdkcgi`**. `DeviceRequestHelp.java:122,490` uses **`get.device.streamkey`** in an envelope. This establishes an APK stream-key request path. It does not demonstrate a working endpoint/authentication pair for the issue #7 device. None was sent.

## FACT: native PreviewSnapshot is a distinct CGI operation

`QVCGIConfigPreviewSnapshot`, **0x308c42**, 50 bytes, calls `IQVCGIConfig::PreviewSnapshot(int, tag_QV_PREVIEW_SNAPSHOT*)` at **0x2fd870**, 1,152 bytes. Resolved xrefs follow `BuildHeader` → string appends → `BuildBody` → `BuildCmd` → `SendCmd`. The previously identified command/body are `POST set.facepic.snap` and `<content><chn>%d</chn></content>`, with a 3000 ms SendCmd argument. XML fields `error/info/wh/face/fea` and base64 face data belong to this generic snapshot/face path.

This confirms a separate code path; it does **not** identify the stored ring photo, the correct R002 channel, or a safe working R002 authentication scheme. No speculative CGI is implemented or sent.

## FACT: P2P and cloud components exist; R002 selection remains unproven

`com/quvii/p2pv2/QvP2PV2Api.java:71` loads **libqv-p2p-v2.so**. Native APIs include LAN search start/get/stop, queryServiceAddress/Ex, reconnect and connection/status callbacks. `QvP2PClientV2.java:311,315,413` forwards service-mask queries to these JNI APIs. `QvPlayerCore` has distinct LAN and online strategies; its lines 383–400 may request both, with credentials/key metadata. `com/quvii/qvfun/publico/ExportApp.java:63` and `common/AppConfig.java:46` reference qvcloud configuration. Native live_player/glnkio also contain qvcloud host strings.

**STRONG INFERENCE:** the tester's reported cloud startup followed by direct phone↔device UDP is consistent with rendezvous/P2P and local media after negotiation. It does not establish cloud-free credential bootstrap or exact UDP framing. **HYPOTHESIS:** R002 uses this QV rather than LT path. A timestamped passive capture is still needed to tie the path to this firmware. No qvcloud runtime dependency was added.

## Other paths and unresolved questions

| Subject | Evidence | Confidence / consequence |
| --- | --- | --- |
| Talkback | `QvPlayerCore.java:3079`, `buildTalkConnection`, builds `quii://…/talk/idc=…` with `ap`, optional `tls`; calls `AudioPlayerManager.startTalking` at 3115 | FACT for generic QV app path; no R002 codec/packet proof, no R002 micro exposed |
| Outputs | `DeviceRequestHelp.java:250` builds envelope `set.device.opendoor` | FACT generic CGI command exists; no R002 payload/auth/output mapping proof; never sent |
| Ring/alarm | `QvPlayerCore.DeviceCallBack` / `onDeviceCallBack` at 2033 and device API alarm methods exist alongside LT alarm helpers | No R002 event mapping or local subscription established |
| OTA / firmware identification | Multiple generic device/system APIs and protocol metadata selectors, but no traced R002 firmware→transport mapping | Unresolved; no update or version-query command implemented |
| Keepalive | P2Pv2 exposes MQTT keepalive callback; legacy LT has its own path | No R002 wire keepalive established; beta keeps no background R002 session |
| Ports 8765 / 6987 | No relevant vendor Java constant/callsite or matching native text established in selected audit | Numeric constants can be compiled/derived: absence of strings is not absence of code |
| `eziotest`, V401/R002/A302 | No APK call chain tied to these firmware/certificate labels | Fingerprint is field evidence only, not app-derived identity |

No decoded socket transaction, authenticated session, video, audio, ring or relay command for R002 is claimed. The 8765 header/parser is based on **reported hardware observations**, not an APK-proven schema. It might be a status/config/bootstrap side service; “8765 = new OWSP” and “8765 = video” remain unsupported hypotheses.

Next evidence: four metadata-only explicit probes, exact firmware/app version, and the [passive capture sequence](r002-investigation.md#passive-official-app-capture-if-needed). This will determine which discovered APK path deserves the next targeted native disassembly.

## Follow-up 2026-09-30 — QV discovery and media

Scope: deeper static analysis of the same hashed APK/ARMv7 libraries, using the current beta.3 candidate `e85a1dcfb5beb54c56b58aafbd98e55d7499c385` as the integration reference. No device request, application login, cloud request, media session or physical command was performed. No integration code, manifest, CRC dependency or release asset was changed. Native addresses below are ELF virtual addresses with the Thumb bit cleared, not file offsets; Java line numbers refer to the existing JADX extraction.

### 1. How the app selects a family

This narrows the selection beyond the presence of two SDKs:

- `com/quvii/qvfun/publico/common/AppConfig.java:56` defines LT UID prefixes `cg` and `cp`.
- `com/quvii/qvfun/publico/sdk/DeviceHelper.java:1437`, `getDeviceCloudType`, returns `3` for those prefixes and `1` otherwise. This is a case-sensitive initial classification; it does not supersede device metadata returned elsewhere.
- `com/quvii/qvfun/publico/sdk/PreviewHelper.java:30,46` selects the LT preview service for cloud type `3`, otherwise the generic preview service. `QvDevice.isLtDevice` also tests cloud type `3`.
- `com/quvii/qvfun/publico/sdk/SdkManager.java:290` explicitly installs `QvP2PClientV2` as the generic P2P manager. This is application startup wiring, not an unused library name.
- `com/quvii/qvnet/device/QvOnlineDeviceHelper.java:479` starts both the generic LAN search and the LT compatibility LAN search. `APP_SUPPORT_LAN_ADD = false` does not remove this online-device discovery path.

**Consequence:** the firmware label `R002` alone still does not select a protocol. The tester's UID *prefix category* or the app's `cloudType` would help discriminate without publishing a full UID. No real UID has been added to this document.

### 2. New lead: generic QV discovery uses UDP 5000

Java route:

`QvOnlineDeviceHelper.lanSearchStart`
→ `QvJniApi.lanSearchStart` / `lanSearchDeviceStart` (`com/quvii/qvplayer/jni/QvJniApi.java:186–207`)
→ configured `QvP2PClientV2` (`com/quvii/p2pv2/QvP2PClientV2.java:290–306`)
→ `QvP2PV2Api`
→ **libqv-p2p-v2.so**.

Native evidence:

| Function / instruction | Observation |
| --- | --- |
| `tdkcloud::LanSearch::Start`, `0x3544d0` | Creates UDP listeners requesting local ports **5001** and **5003** (`0x354522`, `0x35457e`) |
| Static initializer, `0x35422e–0x35423a` | Initializes the string used by `OnSearch` to `ASZENO.SEARCH.V4` |
| `tdkcloud::LanSearch::OnSearch`, `0x354700` | Appends `.1`, copies the string's length into `CTDKDataBuffer`, calls `SendUdpData` |
| `OnSearch`, `0x354778`, `0x35478e`, `0x354792` | Broadcast destination **255.255.255.255**, UDP destination **5000**, send call |
| `tdkcloud::LanSearch::Parse`, `0x354bb8` | Recognizes response prefixes `ASZENO.SEARCH.V4.1` and `ASZENO.SEARCH.V4`, advances past the matched prefix, calls `ParseNet` |

The reconstructed outgoing request is exactly **18 ASCII bytes**, with no terminating NUL:

```text
ASZENO.SEARCH.V4.1
41535a454e4f2e5345415243482e56342e31
```

The app sends using the listener handle stored for the requested local port 5003. Its native API receives the bind port by reference; a future implementation must check the actual bind outcome, not assume availability. The app schedules repeated discovery; no repetition or new discovery service has been added to WelcomeEye.

An older path also exists in **liblive_player.so**: `IQUIISearchClient::Create` (`0x37b930`) uses IPv4 broadcast UDP 5000 / local 5003; `SendProbe` (`0x37bc5c`) sends `ASZENO.SEARCH.V1` and `ASZENO.SEARCH.V3`. This is kept separate from the application's explicitly configured P2Pv2 path. It is not a reason to send multiple speculative versions.

**Implication for issue #7:** failure of the existing **UDP 1500** discovery does not test this QV discovery protocol. There is now a concrete, app-derived alternative to investigate. Whether the tester's firmware answers it is unknown.

### 3. The discovery response contains advertised ports

This is not a clear-text search for a port number. The active QV discovery path decrypts a configuration record:

1. `LanSearch::ParseNet` (`0x354d04`) interprets fields relative to the bytes **after** the matched ASCII response prefix. A little-endian u32 at `+0x08` supplies seed-material length, bounded by `0x400` in this routine; u32 at `+0x0c` supplies encrypted-data length. Seed material starts at `+0x28`; encrypted data starts at `+0x28 + seed_length`.
2. `LanSearch::ParseV4` (`0x354d58`) calls `AESSecret::GenerateKey` with version string `1.0.0` and 256-bit size, then `AES_set_decrypt_key` and `AES_cbc_encrypt` in decrypt mode. Its IV is **sixteen ASCII `0` bytes (`0x30`)**, not sixteen zero bytes.
3. `AESSecret::GenerateKey` (`0x3e5018`) uses `GenerateSeedKey` (`0x3e50ac`), `GenerateSeedKeyAndBox` (`0x3e5614`) and `GenerateExpansionKey` (`0x3e4e24`). `GenerateSourceData` (`0x3e583c`) loads three embedded 4096-byte tables. This is a vendor key derivation followed by AES-256-CBC, not the legacy LT AES-CFB login scheme and not simply an advertised raw AES key.
4. `LanSearch::ParseData` (`0x354ec8`) copies a **520-byte (`0x208`)** configuration record. The JNI result conversion uses that same stride.

The JNI method `Java_com_quvii_p2pv2_QvP2PV2Api_lanSearchDeviceGet` at **`0x29bce4`** associates explicit Java field names with native loads. Selected offsets in the **decrypted configuration record**, not in TCP 8765 and not in the UDP prefix:

| Offset | Native read | Java field / meaning established by JNI |
| --- | --- | --- |
| `0x64` | u32 | `ip` |
| `0x78` | u16 | `streamPort` |
| `0xc8` | C string | `uid` |
| `0x188` | C string | `type` |
| `0x19c` | C string | `oemId` |
| `0x1a4` | u16 | `channel`, normalized to at least 1 |
| `0x1a8` | u16 | `cgiPort` |
| `0x1aa` | u16 | `directMode` |
| `0x1ac` | u32, tested for nonzero | `adminPasswordSHA256` |
| `0x1b0` | u32 | `category` |
| `0x1b8` | u32 | `ability` |
| `0x1cc` | u16 | `tlsMediaPort` |

Examples of exact field reads: CGI port `0x29c0fa`, stream port `0x29c116`, TLS media port `0x29c262`. `QvOnlineDeviceHelper.java:284–288,349–353` copies the discovered CGI port to `localPort` and retains the TLS media port; `QvLanSearchInfo.supportTls()` tests whether that TLS port is positive.

**Limits:** no captured QV response has been decrypted, and no independent decoder has been validated against native execution. The observed APK bounds are not a safe parser specification: any future decoder needs strict datagram length checks, CBC block-length checks, bounded strings and complete field bounds. These records can contain private addresses and device identifiers; they must not be published raw or copied into standard diagnostics.

### 4. CGI credentials and the key-to-player chain are clearer

For the generic LAN preview path, the Java code establishes:

- `QvPlayerCore.java:348,387` passes `QvEncrypt.EncodeDevicePassword(authCode)` toward `playFormLan`.
- `com/quvii/publico/utils/QvEncrypt.java:19` hashes a nonempty input shorter than 64 characters using UTF-8 SHA-256, represented as lowercase hex. Inputs of at least 64 characters pass through; null/empty handling yields an empty value. This is not the legacy LT password transform.
- `com/quvii/publico/common/SDKConst.java` defines the application user **`adminapp2`**, default CGI **443**, default media **34567**.
- `QvPlayerCore.getEncodeKey` (`:974`) creates a temporary device object and calls `getDeviceSecret`. `DeviceRequestHelp.getSecret` (`com/quvii/qvweb/device/DeviceRequestHelp.java:490`) creates the XML envelope command **`get.device.streamkey`**; `DeviceApi.java:155` posts it to **`/tdkcgi`**.
- `DeviceRequestHelp.initHeader` (`:985`) puts security mode, username, password and `passwordencode` into the XML header. The LAN/local branch uses `adminapp2`, the encoded auth code and `passwordencode="1"`. The temporary-device path also uses the encoded-password header when its username is `adminapp2`. Separate capability/config-dependent HTTP-auth branches exist; this is not a universal authentication recipe.
- `com/quvii/qvweb/publico/utils/RetrofitUtil.java:457–510` constructs the endpoint from the configured CGI scheme/port; the default scheme is HTTPS. Discovery's `localPort` is the CGI port here, not automatically a media socket.
- `GetDeviceSecretResp.java` models `envelope/body/error/content`, with `key`, `tdc` and `synctime`. `HttpDeviceManager.java:816` maps these to encoding key/password/expiry metadata. The observed player callback retrieves the encoding key; this is not proof that `tdc` must replace the preview password.
- On success, `QvPlayerCore.java:914` calls `startPlay` using **the advertised TLS media port when TLS is supported, otherwise 34567**. This particular path does not simply substitute the discovery `streamPort` field. `startPlay` (`:3162`) passes a `quii://` descriptor and a separate stream key to the renderer.

The app therefore has an explicit path from **local discovery → CGI port and capabilities → stream key → media port → native player**. It has not been exercised against R002. No credential, CGI request or media connection was sent during this audit.

### 5. Generic QV media has a 32-byte header — not evidence for changing 8765

Targeted disassembly of **liblive_player.so** follows the previously established renderer route into the `CQUIIStreamBase` / `CQUIIStreamLive` code:

| Native function | Address | Observation |
| --- | --- | --- |
| `CQUIIStreamLive::SendSetup` | `0x31f2e0` | Builds a 32-byte header, first byte `0xa9`; appends 32 bytes at `0x31f328` |
| `CQUIIStreamBase::OnRecv` | `0x31cbf8` | Setup receive path waits for 32 bytes, checks first byte `0xa9`, handles setup, consumes 32 bytes |
| `CQUIIStreamBase::OnRecvSetup` | `0x31ce74` | Interprets setup-specific fields, including byte 9 as a result; fields are phase-dependent |
| `CQUIIStreamLive::SendFastPlay` | `0x31f370` | Builds a header starting with `0xaa`, appends 32 header bytes; additional authenticated/encrypted payload handling follows |
| `CQUIIStreamBase::OnRecvCommand` | `0x31d280` | Reads a two-byte length from offset 9; checks availability of `32 + length`, then processes the payload |
| `CQUIIStreamBase::OnRecvData` | `0x31ded4` | Reads a four-byte length from offset `0x0b`; assembles a 32-byte header plus the declared media data |

This is a partial, phase-specific framing reconstruction, not a complete playable session implementation. Cipher/key negotiation, command-specific fields and device selection still require verification. In particular, **none of this establishes that the four TCP 8765 responses use a 32-byte QV header**. Their `<HHHHI` candidate and unresolved 10/12-byte boundary must remain a separate investigation. Similar numeric command values in unrelated native switches are not cross-references.

### 6. What the broader search did not establish

Searches covered vendor Java, native text/symbols, immediate constants in the defined ARM/Thumb functions of six relevant libraries, and candidate port constants in all 17 packaged libraries. No socket call chain tied **8765**, **6987**, `eziotest` or the `R002` firmware label to the QV or LT paths was established. Some binary integer matches were text/hash/data coincidences, not port references. This is not proof of absence: ports can be supplied by discovery/cloud metadata, constructed at runtime, or reside in code outside the selected functions.

The APK is a multi-family client. Its presence on the tester's phone does not mean every firmware service is implemented by every bundled SDK, nor that an unauthenticated 8765 response describes its media protocol.

### 7. Next discriminating evidence, without expanding beta.3

1. Keep beta.3's already prepared, bounded four-type TCP 8765 observation unchanged. It addresses the unresolved prefix/body boundary directly.
2. If available, establish only the tester's UID prefix category (`cg` / `cp` / neither) or the app's actual family metadata; do not request a full UID in a public issue.
3. A **separately scoped, explicitly triggered** future QV discovery check can now use the exact app-derived `ASZENO.SEARCH.V4.1` request to UDP 5000, with the app's receive-port behavior and a bounded collection window. It would not require a phone capture, CGI, login, media, ring or output command. It has not been implemented or executed here; broadcast scope and identifier handling must be addressed before any live test.
4. Validate any response decoder offline before using discovered ports or credentials. A QV response would provide much stronger evidence for the next path than guessing an endpoint from an open port. No response would remain inconclusive.

### Audit checks and retained artifacts

The library hashes above identify the exact binaries. Selected symbols, ARM/Thumb call targets, PC-relative literals, the string initializer, request byte count, JNI field-name/load associations and Java call sites were cross-checked. Literal pools were distinguished from instructions; a linear disassembly alone is not treated as control-flow evidence. This is static verification, not a hardware or cryptographic interoperability test.

Local analysis helper: `work/r002_native_deep.py`. Local symbol inventories and selected disassembly: `work/r002-deep-20260930/`. These workspace artifacts and the proprietary APK contents are not included in the integration package or committed to this repository. This follow-up changes documentation only, including the beta.3 release notes and changelog. The functional beta.3 candidate, main, stable release and CRC implementation remain unchanged.

## 2026-10-04 — beta.5 common decoder verification

The subsequent Door Connect analysis provided an encrypted QV record decoder.
It has now been cross-checked against the original WelcomeEye ARMv7 library:
the three key-derivation tables are bit-identical, seven synthetic native-math
vectors match across architectures, and the 520-byte JNI record fields match.
The portable offline reproduction is `tools/verify_qv_kdf_armv7.py`; exact
hashes, offsets and limits are in [the beta.5 evidence](connect3-beta5-auth.md).
No device or manufacturer service was contacted for this verification.

Beta.5 therefore decodes any matching QV reply **already collected** by the
explicit `r002_discover_qv` action. `include_details: true` returns advertised
model, firmware, ports and channels only in that response, without UID/IP.
Raw datagrams still require the separate `include_response` option. Persistent
diagnostics contain only allowlisted counters, sizes and fixed decoder errors.
There is no additional request, background discovery or automatic port change.

This establishes shared SDK mathematics, not R002 firmware interoperability.
Issue #7's TCP prefixes and TLS CN do not prove QV/CGI authentication support.
The strict TCP 8765 parser and its unresolved 10/12-byte boundary are unchanged;
no CGI, authentication or media implementation is added for R002.
