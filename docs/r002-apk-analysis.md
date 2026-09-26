# R002 APK audit — evidence and limits

Audited 2026-09-26. Application **com.extel.philipswelcomeeye 6.1.58.24**, versionCode 63, compile/target SDK 36, min SDK 26. This is the existing workspace extraction, not a new firmware capture. A newer firmware can use a path this APK selects through metadata; names alone do not establish that selection.

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
