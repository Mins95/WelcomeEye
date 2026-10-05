# Connect 3 beta.7: LAN live-video evidence

This document separates static application evidence, synthetic implementation
tests and the tester's actual CGI result. The native path below is sufficient
to implement an explicit, experimental live-video attempt. It is not a claim
that media on the tester's device has already worked.

## Inputs and provenance

Application: Philips Door Connect `1.0.123.3(2)`, APKPure XAPK SHA256
`f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
ARM64 `liblive_player.so` SHA256:
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
ARM64 `libqv-p2p-v2.so` SHA256:
`4aa8fe8a7c4895225754b0c61ad6178796357e106ba41d378085a7bee71f59d5`.
Native addresses below are ELF virtual addresses in this exact live-player
binary. DEX addresses are `code_item` offsets, not Java source line numbers.
Evidence-package references: E010, E034, E042, E070–E076 and `JNI_MAP.md`.
No APK certificate private key or binary is distributed with the integration.

Reported hardware is Connect 3 / `IDS94E6SW`, firmware
`V401.R001.A350.00.G0123.B025`. The beta.4 discovery decoded one channel,
CGI port 443 and TLS-media port 8443, with stream port zero. That observation
does not establish that the 8443 service accepts the following live session.

## What has actually been validated on hardware

Issue [#1, comment 5990060066](https://github.com/Mins95/WelcomeEye/issues/1#issuecomment-5990060066)
and its sanitized diagnostic report record beta.5 on HAOS 2026.9.3,
Python 3.14.6, amd64. The explicitly requested CGI access check completed in
229 ms, with one request, HTTP 200, verified configured certificate pin,
`authentication_status=accepted` and `streamkey_received=true`.

This proves the supplied local auth code and pinned CGI path were accepted
for that read. It does **not** prove media authentication, media decoding,
8443 certificate equality, client-certificate requirements, physical output
mapping, or model authentication. The same report says
`media_available=false`, `hardware_validated=false`, `model_confirmed=false`.

## Java: original credential and returned stream key

1. `QvPlayerCore.playFormLan`, classes2 `0x28d71c`, reads the online-status
   local IP, CGI port and TLS-media support. It requests a key with username
   `adminapp2` and the supplied encoded auth code.
2. `QvDeviceCore.getDeviceSecret` calls `HttpDeviceManager.getDeviceSecret`,
   classes4 `0x244234`; the callback chain reaches `HttpDeviceManager.Q4`,
   classes4 `0x24ce24`.
3. After XML `error=0`, Q4 copies `content.key` **directly** to
   `QvDevice.dataEncodeKey`; it copies `content.tdc` to the temporary device's
   password and `content.synctime` to its password expiry time.
4. `QvPlayerCore$15.onNext`, classes2 `0x288428`, passes only
   `getDataEncodeKey()` into the continuation.
5. `QvPlayerCore.e`, classes2 `0x28aba8`, calls `startPlay` with `adminapp2`,
   the **original encoded auth code**, the original local IP, the advertised
   TLS-media port when TLS is supported (otherwise 34567), and the returned
   key. It does not read the temporary device password or expiry time.

Consequently the media login password is the existing `encode_auth_code`
result, **not** `tdc`. The returned `key` has no additional Base64 decoding,
hex decoding, SHA derivation, or timestamp mixing in this LAN continuation.
`tdc` and `synctime` need not be retained for this media implementation.

`QvPlayerCore.startPlay`, classes2 `0x28e6a8`, stores these values and builds:

```text
quii://adminapp2[:escaped_encoded_auth]@local_host:media_port/
mode=real&idc=channel&ids=stream[&inner=1][&newcn=1]&ap=2[&tls=1]
```

The line breaks above are explanatory. `getCorrectPassword`, classes2
`0x2872c8`, only escapes `@` as `@@` for URL parsing; it performs no crypto.
Normal `ap=2` can become `ap=1` for cloud type 2. `newcn=1` is added only when
the device was not added by IP and capability 84 is reported. A device added
by IP therefore has a statically established setup-first LAN path.

## Selection of the native live protocol

`IQUIIStream.Start` `0x54261c` acquires an `IQUIIDevice` with requested
connection protocol 2 (`0x5426a0`). Its constructor `0x4accb4` initializes
the supported protocol mask to 3. `GetProtocolType` `0x533d90` reads `ap`
from the URL. `CheckSupportConPro` `0x49e49c` tests the mask, while
`GetReqConProType` `0x4ae7e0` reads the requested protocol.

`IQUIIStreamLive.OnStart` `0x5440a8` selects the legacy `openStream` route
if protocol 2 is unsupported or protocol 1 was requested. Otherwise it creates
`CQUIIStreamLive`. Thus the normal `ap=2` LAN route reaches the reconstructed
32-byte QV live protocol, rather than an assumed legacy OWSP route.

Inside `CQUIIStreamBase.OnStart` `0x4a300c`, absent `newcn=1` selects
`SendSetup` (`0x4a78b8`); present `newcn=1` selects `SendFastPlay`
(`0x4a7978`). These are distinct branches. The experimental IP-based
integration uses the setup-first branch and does not guess capability 84.

## Three keys, modes, IV and integrity

`Java_com_quvii_qvplayer_publico_entity_QvPlayRender_startPlayVideo`
`0x454928` converts the separate Java `dataEncodeKey` argument into a string.
At `0x454a10` it calls `QVPlayerSetKey(panel, key, key, key)` with **three
identical pointers**. `IQUIIStream.SetKey` `0x542e38`,
`IQUIIDevice.SetKey` `0x4b13b0` / `GetKey` `0x4b1420`, and
`CQUIIStreamBase.SetKey` `0x4a6378` copy these strings without transformation.
`IQUIIStreamLive.OnStart` obtains and forwards the three strings at
`0x544418` / `0x544474`.

`GetKeyLen` `0x4a6304` yields no AES for mode 0, AES-128 for mode 1,
AES-256 for mode 2. `EncryptData` `0x4a41c4` uses the raw string buffer
as key bytes: first 16 or 32 bytes according to the mode. Its transmit path
uses the first key and receive path the second. `EncryptMediaData`
`0x4a60e0` uses the third key for media decryption, with the same negotiated
AES key length. Because JNI supplies one string three times, these key bytes
are identical in the app's observed LAN call chain.

`SetIvec` `0x4a2528` constructs sixteen ASCII `0` bytes (`0x30`), **not**
sixteen NUL bytes. Every encryption/decryption call resets that IV.
Control header and extension/body are therefore separate AES-CBC operations.
Native outgoing buffers use zero padding to the AES block boundary.
`SHA` `0x4a53c0` and `GetSHALen` `0x4a4c40` select SHA-256 or none;
the digest covers the plaintext control header plus plaintext parameters.
There is no demonstrated media checksum to invent.

The unencrypted setup response sets control-encryption mode from byte 10 in
`OnRecvSetup` `0x4a4034`; `OnRecv` `0x4a3c04` validates and stores byte 11
as SHA mode (`0x4a3d90`–`0x4a3d9c`). Supported values are AES modes 0–2
and SHA modes 0–1. Media-encryption capability defaults to enabled in the
base constructor, and each media header identifies whether its data is
encrypted. Actual response values on Connect 3 remain to be observed.

## Channel, stream, framing and live-only commands

`QvOpenSDK.createPlayCore(cid)`, classes2 `0x2b0f4c`, uses channel 1.
`PreviewModel.play`, classes4 `0x19a3d0`, supplies `Channel.channelNum`;
the fallback single-channel list in `Device.getChannelList`, classes4
`0x1c14cc`, starts at channel 1. `QvPlayerCore` constructors default to
channel 1 and stream 2. `CQUIIStreamLive.ParseURL` `0x4a7748` preserves
`idc`, but subtracts one from positive `ids` at `0x4a77f8`.
Thus the default app profile becomes **wire channel 1 / stream 1**.

The framing module reconstructs only setup, live play, keepalive and teardown:

| Message | Evidence | Fields used |
| --- | --- | --- |
| Setup | `SendSetup 0x4a78b8` | 32 bytes, `A9` at byte 0, remaining live request bytes zero |
| Play | `OnSendPlay 0x4a8384` | command 1; little-endian time at 1, extension length at 9, parameter length at 11, channel at 13, action at 15, zero-based stream at 16 |
| Keepalive | `SendKeepAlive 0x4a37f0` | command 0 with control integrity/encryption |
| Teardown | `SendTeardown 0x4a5414` | command 7; no physical output opcode |
| Media outer packet | `OnRecvData 0x4a58f0`, `PackFrame 0x4a5b90` | command A0–A3, extension length at 9, packet length at 11, encryption flag at 15, skipped offset at 16 |
| Media frame | `CFramePack.PackFrame 0x4aafa4` | 20-byte prefix, `00 00 01 E0`–`EB`, payload length at 4, codec at 14, fps divided by 4 at 15, width/height at 16/18 |

Play parameters are `username&&password\0customID\0` and optionally
`clientID\0`; the integration sends no speculative physical commands.
`CFramePack` returns after one assembled frame. Its caller discards the
remaining decoded chunk (`0x4a6064`–`0x4a6070`); trailing bytes must not be
declared invalid merely because they are nonzero, and must not be treated as
proven concatenated frames. A new QV prefix at a chunk's beginning resets
an incomplete assembly (`0x4ab040`). All implementation sizes remain bounded.

## TLS-media trust is separate from CGI

`IQUIIStreamLive.OnStart` `0x5440a8` sees `tls=1`, fetches device CA,
client-certificate and key paths, and passes them to the stream at `0x544604`.
`SetClientCertsPathName` `0x4a25c0` enables the SSL socket.
`CQVSsl.BindSsl` `0x88205c` uses `TLS_client_method`, creates a context,
calls `CheckCerts`, creates SSL, sets the descriptor and calls `SSL_connect`.
There is no SNI or hostname-verification call in this function.

`CQVSsl.CheckCerts` `0x882240` behaves differently depending on its inputs:
if any of the three path strings is empty, it returns without loading them or
enabling peer verification. If all are nonempty, it loads the CA, client
certificate and private key, checks the key pair and sets `SSL_VERIFY_PEER`.
`QvCore.createP2PClient`, classes2 `0x2829c0`, calls `QvJniApi.certsSet`
with the manager's certificate paths at `0x282ab8`.

Therefore the official media stack **can present a client certificate**.
Static client code does not prove that the actual 8443 server requires it.
The existing successful CGI check does not settle this question. The integration
does not copy or use an APK private key. Its separate explicit media-certificate
inspection sends no credentials. Before QV setup/login it compares the observed
media DER hash to a configured media pin (or an explicitly configured CGI pin
when identical); a mismatch fails rather than silently trusting the endpoint.
A TLS failure remains diagnostic evidence, not an invitation to weaken TLS.

## Cadence and release

`CQUIIStreamBase` constructor `0x4a2000` initializes keepalive and resend
intervals to 10 seconds (`0x4a2260` / `0x4a226c`) and live receive timeout
to 60 seconds (`0x4a2268`). `OnRecvPlay` `0x4a50e4` arms these timers
after a successful result. `OnPolling` `0x4a3568` sends keepalive when its
timer fires; `SendKeepAlive` and `OnRecvKeepAlive` rearm the 10-second timer.

`CQUIIStreamBase.OnStop` `0x4a32c0` invokes teardown then closes the socket
and clears buffered media. `IQUIIStreamLive.OnStop` `0x544c40` detaches the
callback, stops and releases the allocated live stream. Java
`QvPlayerCore.stop`, classes2 `0x28ec00`, stops talk data, breaks the talk
connection, closes the device and invokes native rendering stop.
`QvPlayerCore.release`, classes2 `0x28da74`, clears disposables/listeners;
it is not a substitute for the explicit stop path.

The integration must close on last viewer release, timeout, EOF, failed
negotiation and unload. Multiple explicit viewers share one media session;
there is no startup media session, background polling or profile retry.

## Strike and gate remain unavailable

The separate application path is visible but not complete enough for a safe
physical implementation. `PreviewPresenter.startUnlock` classes4 `0x19f7b8`
calls `PreviewModel.unlock` then `QvPlayerCore.unlock` classes2 `0x28ef60`.
That method encodes the opening code and constructs `SendUnlockData`
classes4 `0x21bbc8`, then uses `sendDeviceOrder(4, payload)` and
`transparentEx` (classes2 `0x28eecc`, JNI `0x454ccc`). Its payload is
16 prefix bytes plus the encoded-secret bytes: output at 0, channel at 2,
confirmation boolean at 3, secret starting at 16. This does not yet establish
the two buttons' actual output mapping or the complete native transport path.

Another SDK path calls `set.device.opendoor` with channel/lock/password XML
(`DeviceRequestHelp.deviceUnlock`, classes4 `0x230e0c`). Its equivalence
to the transparent live command has not been demonstrated. Neither path is
included in beta.7 media; no strike/gate experiment, output retry, talkback,
audio or ring implementation is justified by the video reconstruction.

## Acceptance boundary

Synthetic socket and decoded-frame/WebRTC tests can verify framing, bounded
reads, cipher round trips, lease accounting, capability isolation and cleanup.
They cannot replace a hardware result. The next tester report must separately
identify CGI acceptance, media TLS/pin, setup result, play result, first H264
frame, repeated open/close and the official app's subsequent access. No
successful media or physical result is asserted until those observations exist.
