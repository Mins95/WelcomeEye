# R002 beta.11: original APK evidence and remaining limits

Static follow-up, 2026-10-07, against WelcomeEye `com.extel.philipswelcomeeye`
6.1.58.24 (versionCode 63). This records evidence for developing the R002 QV
backend. It does not claim that the supplied device has completed a QV login,
played media, accepted microphone audio, operated an output, or delivered a
doorbell event. No device, cloud, port-scan or network probe was run for this
audit. No proprietary binary, decompiled source or private packet is included.

## Inputs and how to reproduce

Workspace root is `C:\Users\grego\Documents\Codex\2026-09-08\je-x20\work`.
Java paths below are relative to `decompiled/sources/`; resource paths are
relative to `decompiled/resources/`. Native files are ARMv7 libraries inside
`xapk/config.armeabi_v7a.apk`, under `lib/armeabi-v7a/`.

| Input | SHA256 |
| --- | --- |
| Main WelcomeEye APK | `ff205ff0d24527b912d5c227aa80f8a6500979393ec0cefa62e35082173d6b52` |
| `liblive_player.so` | `9ce7549858e01dd73d858cb956a2e082fbe828675a830730bd932692838386fe` |
| `libqv-p2p-v2.so` | `210402ce70a86a7d3ab5d25cecb8393b72d682ccd8576b02dfcb8a4458346d41` |
| `libglnkio.so` | `25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992` |

Native addresses below are ELF virtual addresses with the Thumb bit cleared,
not file offsets. Targeted instructions were decoded using the existing
`r002_native_deep.py` ELF/Capstone helpers and symbol catalogue in
`r002-deep-20260930/`. PLT destinations were resolved through `.rel.plt`;
the alarm virtual method was resolved through `.rel.dyn`. The original
WelcomeEye functions were inspected directly: matching Connect 3 symbols alone
were not treated as proof. Existing broader context is in
[the R002 APK audit](r002-apk-analysis.md).

Abbreviated Java filenames later in this note resolve as follows:

| File | Directory below `decompiled/sources/` |
| --- | --- |
| `QvDeviceSDK.java`, `QvOpenSDK.java` | `com/quvii/openapi/` |
| `QvOnlineDeviceHelper.java`, `DeviceOrderHelper.java` | `com/quvii/qvnet/device/` |
| `QvLanSearchInfo.java`, `QvDeviceOnlineStatus.java` | `com/quvii/publico/entity/` |
| `HttpDeviceManager.java` | `com/quvii/qvweb/device/` |
| `PreviewUnlockController.java` | `com/quvii/qvfun/preview/view/viewstub/` |

## Discovery: the existing request is the APK request

In `libqv-p2p-v2.so`, `tdkcloud::LanSearch::Start` at `0x3544d0` creates the
5001 and 5003 local listeners. `OnSearch` at `0x354700` uses the 5003 listener
to send to broadcast `255.255.255.255:5000`. It constructs exactly 18 ASCII
bytes, without a terminating NUL:

`ASZENO.SEARCH.V4.1` (`41535a454e4f2e5345415243482e56342e31`).

The static initializer at `0x35422e` supplies `ASZENO.SEARCH.V4`, and
`OnSearch` appends `.1`. `Parse` at `0x354bb8` accepts the V4 and V4.1 forms;
`ParseNet` starts at `0x354d04`. This is the QV discovery family, independent
of the older glnkio discovery on UDP 1500.

`Java_com_quvii_p2pv2_QvP2PV2Api_lanSearchDeviceGet` at `0x29bce4` extracts
these offsets from each decoded 520-byte configuration record:

| Field | Record offset | Instruction evidence |
| --- | --- | --- |
| Stream port | `0x78`, little-endian u16 | `0x29c116` |
| Model | `0x188` | JNI getter above |
| Channel count | `0x1a4`, minimum 1 | JNI getter above |
| CGI port | `0x1a8`, little-endian u16 | `0x29c0fa` |
| TLS media port | `0x1cc`, little-endian u16 | `0x29c262` |

The supplied R002 response was decoded as model `IDS9417AW`, CGI 443, stream
port 0, TLS media port 0 and one channel. The SDK-selected version at `0x1bc`
is empty, but the base field at `0x108` contains
`V401.R002.A302.00.G0058.B002`. An empty SDK version is not an absent firmware
in the record. These are reported values, not proof that every other port or
transport is absent. A successful TCP connection to 34567 followed by a failed
legacy OWSP exchange also does not test the QV setup described below.

### Base version and SDK overwrite

WelcomeEye JNI copies `record + 0x108` at `0x29c042`, then copies `+0x1bc`
at `0x29c066` to the **same beginning** of a 48-byte temporary buffer.
`NewStringUTF` at `0x29c06e` and the Java `version` assignment at `0x29c14e`
use the overwritten value. Door Connect ARM64 does the same at `0x4236dc`,
`0x423718`, `0x423724` and `0x42387c`. Neither path checks for an empty override
or concatenates the two strings. The temporary buffer size is not proof of
the C declaration widths of the source fields.

The real record has three date-like strings starting at `0x128`, `0x148` and
`0x168`. Their roles are not established and the JNI path does not export them.
Beta.11 reads the base text only for explicitly requested metadata, within a
conservative 32-byte window ending before `0x128`. A malformed optional base
field is reported as unavailable; it never rejects an otherwise accepted
discovery or changes media availability. Dates are not exported.

`firmware` retains its existing SDK-selected meaning. Optional metadata adds
`firmware_base`, `firmware_sdk`, `firmware_source: sdk_override_0x1bc` and
`firmware_base_status`. These are ephemeral action-response fields; no raw
record, version strings or dates are copied into standard diagnostics, sensor
attributes or config entries. Firmware discovery does not feed the persisted
firmware classifier or change the transport/identity. This is metadata recovery,
not proof of authenticated device identity or a firmware revision policy.

## Direct LAN credentials, stream key and transport selection

`com/quvii/core/QvPlayerCore.java:348,387,1161-1172` passes
`EncodeDevicePassword(device.getAuthCode())` into `playFormLan` and requests the
stream key with the discovered local IP and CGI port.
`com/quvii/publico/utils/QvEncrypt.java:17-19` hashes an input shorter than 64
Java characters with SHA-256; a value of length 64 or greater is retained.
The SHA-256 representation is lowercase hexadecimal over UTF-8. Empty/null
input follows the utility's empty result handling. This is an app behavior,
not a reason to invent or substitute credentials.

`com/quvii/qvweb/device/DeviceRequestHelp.java:983-995` constructs the local
header with username `adminapp2`, encoded auth code and protocol marker `1`.
`getSecret` at lines 490-492 requests `get.device.streamkey`;
`com/quvii/qvweb/device/api/DeviceApi.java:155` posts to `/tdkcgi`.
`QvPlayerCore.getEncodeKey`, lines 974-1008, returns `dataEncodeKey` to the
player. `HttpDeviceManager.java:816` retains that key directly. The separate
temporary password returned by the CGI response is not substituted by this
play callback for the encoded device auth code.

`QvPlayerCore.java:906-921`, specifically line 914, selects the advertised
TLS media port when `isSupportTlsMedia()` is true, otherwise default TCP
34567. It does not substitute the discovery `stream_port` for this fallback.
`startPlay`, lines 3162-3221, builds `quii://` with `mode=real`, `idc`, `ids`,
normally `ap=2`, and `tls=1` when enabled. The stream key is passed separately.
`CQUIIStreamBase::OnStart` at `0x31c410` obtains the URL address and port and
calls `CQVSocketTCP::Open` at `0x31c4ba`. This branch is TCP media, not RTSP or
the legacy OWSP/SCT session. CGI's HTTPS/443 selection is independent of media.

## TLS: dynamic metadata, no demonstrated fixed alternate port

`QvLanSearchInfo.java:292-293` and `QvDeviceOnlineStatus.java:88-89` define
support as `tlsMediaPort > 0`. `QvOnlineDeviceHelper.java:284-288,349-353`
copies this field from discovery. A zero value therefore makes this specific
LAN app path select its 34567 fallback; it does not establish that the firmware
has no TLS capability.

The other reviewed port entry points also accept a value rather than discover
a magic default: `com/quvii/openapi/QvOpenSDK.java:1731,2222,2345-2366` supplies
the 34567/443 defaults and enables TLS only for a supplied positive TLS media
port; `QvDeviceSDK.java:1528-1534` applies the advertised-port rule.
`queryDevicePort`, lines 3075-3091, returns stored local parameters in local
mode and invokes P2P mapping otherwise. Searches of vendor Java and printable
native strings found no 34568, 34569 or 8443 media fallback in these paths.
This is a bounded negative finding, not an exhaustive claim about every binary
immediate, firmware revision or server configuration.

There is a second TLS metadata source in QV P2P. In `libqv-p2p-v2.so`,
`RespMsgContentP2PConnect::ParseJson` at `0x34fb50` reads `tlsDevPort` at
`0x35043e-0x35046e`, storing the integer at object offset `0xe0`; its constructor
at `0x34f99c` initializes it to zero. `GetTlsDevPort` is at `0x2e5eb2`.
`P2PTest::GetTlsEnable` at `0x2e5e6a` reduces it to `> 0`;
`P2PConnect::GetTlsEnable` at `0x2c6698` delegates that check. `P2PManager::AddPort`
copies the resulting TLS flag to its caller at `0x2c7d82-0x2c7d92` and
`0x2c8988-0x2c8998`. The Java `PortInfo`/`QvDevicePortBean` thus carries a local
mapped port and a TLS flag. The numeric `tlsDevPort` is not demonstrated here
to be an independently reachable LAN TCP port or a relay listening port.

## QV P2P really has LAN UDP/KCP negotiation; it is separate from legacy UDT

The negotiated metadata is concrete. `P2PManager::AddPort` (`0x2c79b0`)
constructs a `p2pconnect` request (command at `0x2c814a`, version `v3.2.i.1` at
`0x2c8172`), sets the device ID, session flag, random request session ID and KCP
parameters, then calls `ServerManager::SendUstMsg` at `0x2c87c4`.
`ServerManager::SendUstMsg` itself (`0x2c9cd0`) calls
`ProtocolManager::SendMqttMsg` at `0x2c9d00`: this is service negotiation,
not a direct broadcast or a LAN port scan.
`P2PManager::OnResp` (`0x2cb2a8`) recognizes `p2pconnect` and obtains the
`RespMsgContentP2PConnect`. Its JSON parser reads `pub-ip`, `pub-udpport`,
`loc-ip`, `loc-udpport`, `utd-pub-ip`, `utd-pub-udpport`, `dest-ip`, `dest-port`,
`session-flag`, `resp-session-id`, `kcpParam`, `appTrans` and `tlsDevPort`.

The subsequent tests have distinct destinations:

| Method | Address | Destination from response |
| --- | --- | --- |
| `P2PTest::OnSendTestLan` | `0x2e95b0` | Each `loc-ip` with `loc-udpport` |
| `P2PTest::OnSendTestP2P` | `0x2e97f0` | `pub-ip` with `pub-udpport` |
| `P2PTest::OnSendTestTrans` | `0x2e9a10` | `utd-pub-ip` with `utd-pub-udpport` |

These inspected branches retry the supplied endpoints, not a sequential range
of ports. `SendTestMsg` at `0x2e61c8` calls `NetComKcp::BuildRbHead` at
`0x2e6242`, incorporates session flag and response session ID, and uses
`NetComKcp::SendUnreliableMessage` at `0x2e64a6`/`0x2e64e2`.
`P2PTest::OnBindConnect` at `0x2e5f80` creates `DevConnectRbUdp` with a TLS
boolean. That class sets `KcpLinkConn::SetTlsMedia` (`0x2f86f2`), creates a
session through `KcpLinkClient::Connect` (`0x2f8916`) and forwards data through
`KcpLinkConn::Send` (`0x2f8a6e`). This establishes a QV reliable-UDP/KCP family,
not compatibility with the old SCT handshake.

The port returned to the player is an application-side TCP listener:
`P2PConnect::AddPort` (`0x2c600c`) calls `PortMapChannle::CreatePort` at
`0x2c60e2`; that method (`0x2f98e0`) calls `TdkNetCom::CreateTcpListen` at
`0x2f9912` and stores the resulting port. `PortMapChannle::StartClient`
(`0x2f9f60`) binds the accepted client to `P2PTest::BindConnect` at `0x2f9fb4`.
Java uses `127.0.0.1` for the mapped media connection (`QvDeviceSDK.java:1539-1548`).
It must not be reused as a remote device port. Likewise, native
`queryServiceAddress` (`0x29c348`) calls `ServerResourceInfo::Query` at
`0x29c3a6`; this is not the LAN discovery record getter.

The app can therefore negotiate a LAN UDP endpoint through its P2P service
flow. This audit has not established a cloud-free source for the complete
endpoint/session metadata or a device-only request that obtains it. Reusing
that path would require implementing and validating its negotiation and KCP
transport. Neither zero discovery ports nor a failed legacy UDT exchange rules
it out, but neither provides the missing endpoint/session data.

The existing `experimental_udt.py` is only a bounded SCT handshake. Its
original evidence is the **other** library, `libglnkio.so`:
`LanSearchIndepHandler::parse` (`0x97848`) reads legacy ports at offsets 32/34;
`LanDevice::setParams` (`0x69320`) stores UDP/TCP separately;
`GlnkDevice::getLanDeviceAddr` (`0x69c30`) chooses UDP in mode 2;
`DataChannel::onSpecifiedMode` (`0x89810`) reaches
`ConnChannelPeer::openChnConnection` (`0x8d478`),
`UdtConnection::open` (`0x6f4f6`) and `SCTConnectNoBlock` (`0x6f562`).
No bridge from the QV discovery or media path to that SCT session was proved.
UDP 5000, CGI 443 and TCP 34567 must not be guessed as its UDP destination.
See [the legacy experiment evidence](experimental-diagnostics-beta8.md).

## Media, output and microphone fields verified in WelcomeEye itself

The original `liblive_player.so` has the same relevant QV framing family as
the implemented Connect 3 backend. `CQUIIStreamLive::SendSetup` (`0x31f2e0`)
zeroes a 32-byte header, writes `0xa9` at byte 0 and live selector 0 at byte 9
(`0x31f31e`, `0x31f324`). Setup response, encryption and key functions are
`OnRecvSetup` (`0x31ce74`), `EncryptData` (`0x31cf78`) and `SetKey` (`0x31e566`).
This supports reusing the QV implementation with an explicit R002 backend;
actual firmware negotiation remains to be observed.

`CQUIIStreamLive::OnSendData` (`0x3207ac`) constructs transparent command `0xfe`:
timestamp at bytes 1-8, extension length at 9-10, parameter length at 11-12,
order at 13, payload following the 32-byte header. Header and body invoke
encryption separately (`0x320a54`, `0x320a9c`).
`QvPlayerCore.java:2709-2710` sends order 4 for unlock;
`DeviceOrderHelper.java:239-245` packs output at byte 0, zero at 1, channel at
2, true at 3, twelve zero bytes, then the encoded **opening code** at offset
16 without NUL. It is separate from the device auth code.
`ReceiveUnlockData`, lines 155-165, requires at least two bytes: byte 0 zero
is success; otherwise byte 1 equal to 2 means device busy, else failure.
An accepted ACK does not by itself prove physical movement.

`PreviewUnlockController.java:46-57` maps the first button to output 1 and the
second to output 2. Original resources `res/drawable-xhdpi/btn_unlock1.png`
and `btn_unlock2.png`, inspected locally, depict respectively a door/strike
and a double-leaf gate. Layout `res/layout/preview_item_preview_unlock.xml`
and `res/drawable/selector_btn_unlock1.xml`/`selector_btn_unlock2.xml` establish
the binding. No resource images are copied into the repository.

Talk uses the same selected address/port and stream key:
`QvPlayerCore.java:3080-3115` builds `/talk/idc=...`, normally `ap=2`, with
the same TLS flag. Default talk channel is 65535 (lines 2787-2788).
JNI `AudioPlayerManager_startTalk` (`0x2f49a8`) passes the key into `QVTalkStart`
at `0x2f49d2`. `CQUIITalk::SendSetup` (`0x3876dc`) sends `0xa9`, selector 2
at byte 9. `OnRecvOpen` (`0x387d04`) reads result at 11, codec mask at 12-13,
prefers bit 4 when present, and reads the format variant at 14.
`OnSendRequest` (`0x387e8c`) uses command `0x0d` for the request and `0x0c`
for transmit parameters: channel at 11-12, enable at 13, codec at 14 and rate
at 15-16. `OnSendData` (`0x3885c0`) uses `0xa2`, payload length at 11-14 and
encryption flag at 15. Variant 0 prefixes audio with `00 00 01 f0`, codec at
4, frequency code at 5 (8000 Hz -> 2, 16000 Hz -> 4), u16 payload length at
6-7. These fields support implementation reuse, not a claim about the
unobserved R002 codec mask, accepted rate or microphone operation.

## Doorbell with video closed: identified separate SDK family, not yet portable

The live transparent callback is limited evidence:
`QvPlayerCore.java:1551-1553` dispatches order 23 to `onOtherDoorBellCall`,
using the first byte and following UTF-8 string. Order 1 is hangup and order
4 is unlock feedback. This does not establish an own-device ring event with
the video closed.

The independent native alarm path is real:
`IQUIIDevice::OnAlarmListenStart` (`0x324e2e`) calls `SDK_StartListenEx`
(`0x446494`) using an existing device handle. `CAlarmDeal::StartListenEx`
(`0x4df808`) calls a virtual device method with selector 6 and parameter
field `+8` equal to 1 (`0x4df8d4-0x4df8d6`). The `CDvrDevice` vtable at
`0x627734` resolves that method to `CDvrDevice::open_channel` (`0x516f84`).
Selector 6 with that flag calls `sendAlarmQuery_comm` (`0x52381c`) and creates
`CDvrAlarmChannel` (`0x51ece8`). The selector is an SDK argument, **not** QV
setup byte 9 equal to 6.

`sendAlarmQuery_comm` delegates to `sendAlarmQuery_dvr2` (`0x525bc8`). The
`StartListenEx` branch emits several 32-byte `0x68` requests, byte 8 equal to
2, alarm category at byte 12 and device/session information at bytes 28-31.
Categories include 1, `0x9d`, `0x9f`, `0xa0`, `0xa1`, `0xae` through `0xbe`,
and `0xc0`. The older non-Ex branch emits `0xa1`. These are written to the
SDK device TCP socket by `sendcammand_dvr2` (`0x524df4`).
`CDvrAlarmChannel::OnRespond` (`0x51eeb0`) handles several legacy packet
forms, including command `0x73`; the event callback at `0x51f390` uses 10000.
This is not enough to map a specific closed-video R002 doorbell event.

Crucially, `IQUIIDevice::OpenDevice` (`0x3236a0`) checks
`GetReqConProType` at `0x323708` and returns immediately when it is 2.
The normal `ap=2` QV live path therefore does not automatically provide the
SDK device login required above. `IQUIIDevice::Login2` (`0x323ad4`) separately
reads URL host/port/user/password and calls `SDK_Login` at `0x323c28`;
`SDK_Login` (`0x444c40`) reaches `CManager::Login_DevEx` (`0x4516d0`).
The traced DVR connector `try_connect_dhdvr` (`0x50ec3c`) performs its own
`setup`, `build_login_packet` and `parse_login_respond`, then creates a DVR
device and its keepalive. `build_login_packet` (`0x50e068`) constructs a
different login format: `0x0a` command, body length at 4, credentials after
32, and `a1 aa` at header bytes 30-31. Copying alarm requests onto a QV media
socket would skip this separate login/session family.

The bounded result is an implementation gap: a closed-video SDK alarm path
exists in the APK, but a working R002-specific login, subscription and ring
decoder is not yet proved. CGI methods named `getAlarmInputInfo`,
`getAlarmChannel` and `getRecordAlarmInfo` concern configuration/history;
their names alone do not establish a push subscription. Keep unknown events
unknown and do not represent a video-only callback as a validated standalone
doorbell listener.
