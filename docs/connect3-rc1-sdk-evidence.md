# Connect 3 RC1: SDK evidence and remaining limits

This review extends the beta9 evidence using the original Philips Door Connect
`1.0.123.3(2)` application. It executed no application code and contacted no
intercom, cloud service or other device. Static code and synthetic tests establish
the software paths below; they do not establish physical relay activation,
microphone destination or a physical doorbell press.

The table records the original RC1 boundary. The subsequent
[selected-player microphone trial](connect3-channel2-talk.md) and
[additional alarm/snapshot trace](connect3-ring-evidence.md) document later
findings without claiming new physical validation.

| Function | Evidence | RC1 boundary |
| --- | --- | --- |
| Secondary strike / gate | Preview passes its current channel into the existing live order-4 payload | Separate explicit trials for channel 2, native outputs 1 and 2; hardware mapping remains unconfirmed |
| Secondary microphone | Ordinary Preview uses talk selector 65535; FE order 9 can switch the live context while distinguishing displayed and conversation channels | Unavailable in RC1: the client sequence is understood, but its physical speaker destination and equivalence to stop/reopen on channel 2 remain unconfirmed |
| SDK alarm subscription | A real control-device login automatically calls `SDK_StartListenEx` | No autonomous listener: A331 endpoint/authentication and physical-ring semantics remain unproven |
| Passive observation | The existing live reader exposes control packets and FE orders | Voluntary, bounded observation of one already-open camera; metadata only; no inferred ring event |

## Provenance and method

XAPK SHA256:
`f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
ARM64 `liblive_player.so` SHA256, checked against the local binary:
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.

Native addresses are ELF virtual addresses in that binary. DEX addresses are
code-item or instruction offsets in the named DEX, not reconstructed Java line
numbers. The local research files are outside this repository under
`work/050-analysis/`: `beta10-sdk-login.asm`, `beta10-sdk-session.asm`,
`beta10-jni-listen.asm`, `beta10-connect-activation.asm`,
`beta10-sdk-login-wire.asm`, `beta10-device-login-main.asm`,
`beta10-dex-alarm-xrefs.json`, `beta10-native-alarm-xrefs.json` and
`beta10-login-native-xrefs.json`. Their earlier working label is retained for
traceability. Original binaries and decompiled application files are not shipped.

The DEX call scan covered all four DEX method tables and decoded method bodies
under `com.quvii` and `com.extel`, with no decode errors. The native call scan
covered direct ARM64 `B`/`BL` instructions and resolved PLT targets. Absence from
that scan does not exclude virtual or indirect calls. Symbol names alone were
not treated as proof of a usable feature.

## Secondary outputs use the selected live context

`PreviewPresenter.startUnlock` (`classes4.dex`, code item `0x19f7b8`) calls
`Device.getCurrentChannel()` at `0x19f810`. `PreviewModel.unlock`
(`0x19b238`) passes that value to `QvPlayerCore.unlock(channel, output, code)`
at `0x19b25e`. The channel is therefore an explicit Preview selection, not a
hard-coded first-door value.

`QvPlayerCore.unlock` (`classes2.dex`, `0x28ef60`) applies
`EncodeDevicePassword` at `0x28ef70`, calls
`SendUnlockData(output, channel, true, encoded)` at `0x28ef7a`, then sends
order 4 at `0x28ef82`. `DeviceOrderHelper.SendUnlockData`
(`classes4.dex`, `0x21bbc8`) places native output at payload byte 0,
channel at byte 2, true at byte 3 and the encoded separate opening code at
byte 16. Native outputs 1/2 are the app's strike/gate controls, as established
in [the original control evidence](connect3-control-evidence.md).

The transport is also tied to the current player:
`sendDeviceOrder` (`0x28dcd8`) → `transparentEx` (`0x28eecc`) → JNI
`QvJniFunc_transparentEx` (`0x454ccc`, call `0x454d4c`) →
`QVPlayerTransparent` → `IQUIIStreamLive::Transparent` (`0x545530`) →
`CQUIIStreamLive::Transparent` (`0x4a9410`). The latter requires active state 4
and queues the command on that stream. `OnSendData` (`0x4a9838`) produces FE
with the order at header byte 13. No separate control login or additional live
reader belongs to this Preview path.

This supports an explicit channel-2 trial using its own accepted live session
and payload channel 2. It does not prove which physical relay a particular
installation will actuate. `ReceiveUnlockData` (`classes4.dex`, `0x21bec4`)
still returns only the native result from at least two parameter bytes; it has
no request identifier or target-channel correlation. Writes must remain
serialized, single-attempt and subject to the existing uncertainty latch. A
native accepted reply must not be labelled physical activation.

## Talk selection: explicit XVR routing and ordinary Preview context

The Philips Preview path is explicit: `PreviewModel.talkStart`
(`classes4.dex`, `0x19b1d8`, call `0x19b1fe`) invokes the no-channel overload
`QvPlayerCore.startSendTalkData(listener)`. `PushCallModel.talkStart` also uses
that overload. In `classes2.dex` it is at `0x28ea84`: instruction `0x28ea94`
loads 65535, `0x28ea9a` loads false, and `0x28ea9c` calls the overload taking
an integer channel and a boolean.

`buildTalkConnection()` (`0x28cb80`, default at `0x28cb90`) and the string-only
overload (`0x28cbbc`) also default to 65535. The overload at `0x28cbdc`
stores `mTalkChannelNo` at `0x28cc6a` and appends it to `/talk/idc=` at
`0x28ccea..0x28ccf8`. One special `cloudType == 2` and non-IP-added-VSU
branch maps the default to 0 (`0x28cc46..0x28cc68`); that is a separate
application branch, not evidence for substituting 2 on Connect 3.

There is another API, `vsuTalkStart` (`0x28efb8`), which selects 65535 for
mode 1 (`0x28efce`) and otherwise reads `mChannelNo` (`0x28efd6`).
`PreviewPresenter.talkDataSend` (`classes4.dex`, `0x19fbd4`) chooses the XVR
path only when `Device.isXvrDevice()` is true (`0x19fc4c..0x19fc5c`).
That predicate (`0x1c031c`) means device category **5 or 11**. The path then
passes through `openXvrTalk` and `PreviewModel.xvrTalkSwitchStart`
(`0x19b2cc`, call `0x19b302`). No inspected hardware result establishes those
categories for the Philips installation. A generic SDK channel parameter is
therefore not evidence that replacing the ordinary talk selector with 2 is the
Philips sequence.

### FE order 9 distinguishes displayed and conversation channels

`PreviewPresenter.channelSwitch` (`classes4.dex`, `0x19db08`) has a direct
switch path when the device supports multiple channels, the selected subdevice
is enabled, Preview is active and the device supports CallHold. CallHold is
ability bit **76** (`DeviceAbility.isSupportCallHold`, `0x1b95dc`). This path
sets only `Channel.showChannelNum` (`0x19dbc8`) before sending the request;
the active `Channel.channelNum` can remain different.

`PreviewModel.directSwitchChannel` (`0x19a8fc`) calls
`QvPlayerCore.directSwitchChannel` (`classes2.dex`, `0x28d0e0`). It reads
`isTalking` at `0x28d0f0` and sends FE order **9** at `0x28d100` on the
existing player. `DeviceOrderHelper.SendChannelsSwitchChannel`
(`classes4.dex`, `0x21ba2c`) builds exactly two parameter bytes:
`[selected_channel, isTalking]`. This is a context-switch command, not a
different talk URL.

The order-9 branch of `QvPlayerCore.u`
(`classes2.dex`, `0x28b4ba..0x28b4e8`) requires at least four parameter
bytes and calls `onDirectSwitchChannel` with success when byte 0 is zero,
byte 2, byte 1 and a keep-talk flag when byte 3 is one.
`PreviewPresenter$1.onDirectSwitchChannel` (`classes4.dex`, `0x19bce8`)
handles success differently according to that flag:

- With `keepTalk=true`, it leaves the active channel and talk unchanged even
  though the displayed channel has changed.
- With `keepTalk=false`, it stops voice sending (`0x19bd4a`), copies the
  displayed channel into the active channel (`0x19bd50..0x19bd58`), then
  calls `QvPlayerCore.setChannelNo` (`0x19bd72`). It does not automatically
  start the microphone.

`Channel.setChannelNum` (`0x1b91f8`) sets both channel fields;
`setShowChannelNum` (`0x1b92e4`) sets only the displayed field.
`Device.getCurrentChannel` (`0x1c07e4`) and `getCurrentShowChannel`
(`0x1c0814`) expose these two different fields. Consequently, seeing camera 2
does not establish that an already-active conversation has moved to panel 2.

### Starting a new conversation after selecting channel 2

The ordinary Preview path preconnects talk even before the user enables the
microphone: `PreviewModel.setDevice` (`0x19acf8`) sets
`setPreConnectTalkConnect(!device.isXvrDevice())` at `0x19ade8`.
`PreviewModel.play` (`0x19a3d0`) passes `Channel.getChannelNum()` to
`setChannelNo` (`0x19a400..0x19a408`). `QvPlayerCore.startPlay`
(`classes2.dex`, `0x28e6a8`) uses that channel in its live `/mode=real&idc=`
URL (`0x28e8cc..0x28e8da`), invokes native playback at `0x28e94e`, then
calls default `buildTalkConnection()` at `0x28e976`. This ordering does not
wait for a successful media or talk callback.

After a direct FE9 switch with no microphone already active,
`PreviewModel.talkStop` (`classes4.dex`, `0x19b208`) calls
`stopSendTalkData`. Its implementation (`classes2.dex`, `0x28edc0`) returns
immediately when `isTalking` is false; it does **not** close the preconnected
talk socket. A later ordinary `startSendTalkData(65535, false, listener)`
reuses the ready talk connection when talk state is 4 (`0x28eb04`), rather
than rebuilding it with the new channel. `setChannelNo` itself
(`0x28ddbc`) only changes a Java field and sends no packet.

This establishes an application path in which the accepted FE9 context switch
precedes a new conversation using the same default talk selector. It is stronger
evidence for contextual routing than merely observing a missing channel
argument. It still does not reveal the firmware's physical speaker mapping.
The fresh-start case differs: a full core `stop` closes talk
(`0x28ec8a..0x28ec90`), and reopening channel 2 initiates its live request
and a new default talk connection. The inspected code does not prove that this
stop/reopen sequence and FE9 have identical effects on the device's talk context.

The [existing microphone evidence](connect3-talk-evidence.md) establishes the
separate `CQUIITalk` socket and its wire framing. RC1 does not infer `idc=2`,
broadcast semantics or a verified speaker destination from selector 65535. Nor
does this review rule out contextual routing. A future trial must distinguish
fresh talk from a held conversation, establish the applicable device abilities
and selection sequence, and verify which physical panel receives the audio.

## The alarm path is a distinct SDK control connection

### Android and JNI entry points

`QvDeviceCtrlCore(QvDevice, int)` (`classes2.dex`, `0x2874d4`) obtains
`getDeviceConfigPassword(index)` at `0x28751e` and uses the device username,
IP, `getPort()` (`0x287546`), cloud type and data-encoding key.
`QvDevice.getPort` (`0x2e3b84`) returns the model's `port` field; this path
does not establish a fixed A331 port number.

`getDeviceConfigPassword(int)` (`0x2e3d88`) has subdevice/shared-device
branches and, in the ordinary branch, calls `getPassword()` at `0x2e3e50`.
This is not proof that a current CGI stream key, opening code or the same
encoded local password can be substituted into a new control login.

`QvDeviceCtrlCore.getUrl` (`0x2872fc`) constructs a `quii` URL from the
username, escaped password, IP and that port. Its ordinary branch uses
`ap=2`; another HS branch uses `ap=1`. `openDevice` (`0x287058`) calls
`deviceCtrlOpen` at `0x2870a6` or `deviceCtrlOpen2(url, key)` at `0x2870b4`.
The JNI implementations (`0x45a2bc` / `0x45a3cc`) call `QVDeviceOpen`
at `0x45a334` / `0x45a45c`.

The DEX scan found real generic control/online-state callers, including
`QvDeviceOldApi`, `QvDeviceListOnLineHelper` and `QvDeviceCtrlCoreHelper`.
It did not establish which of those branches configures an A331 control
connection while idle. There is no need for an explicit Java alarm-listen
method to activate the native subscription described below.

### Main login, Login2 and automatic subscription

`QVDeviceOpen` (`0x809ca4`) resolves the device interface. In the QVII path,
`CQVIIDeviceObject::Open` (`0x507ce8`) acquires an `IQUIIDevice`
(`0x507d60`), sets its keys (`0x507db4`) and calls the object's login/wait
path (`0x507e18`).

| Native step | Evidence |
| --- | --- |
| Main login | `IQUIIDevice::Start`, `0x4af154`, reads URL username/password/IP/port, protocol and `tls=1`, then calls `SDK_Login` at `0x4af374` and stores its handle at object offset `0x1d8` |
| Successful connection | `Start` calls `OnConnect` at `0x4af5dc`; `OnConnect` (`0x4af9ac`) automatically calls `OnAlarmListenStart` at `0x4afb50` |
| Alarm start | `OnAlarmListenStart`, `0x4b0ae4`, passes the main handle at `0x1d8` to `SDK_StartListenEx` at `0x4b0b18` and records successful activation |
| Alternative attach API | `CQVIIDevice::AttachAlarm`, `0x4ac448`, attaches a signal and calls `OnStartListen` at `0x4ac4c0`; `OnStartListen`, `0x4ac380`, calls `SDK_StartListenEx` at `0x4ac398` |
| Secondary login | `Login2`, `0x4aec24`, calls `SDK_Login` at `0x4aee40` but stores the result at **`0x618`**, not `0x1d8` |

`GetStreamLoginID` (`0x4aeac8`) uses the main handle for supported stream
paths, otherwise attempts `Login2` at `0x4aeb74` and falls back to the main
handle if necessary. Treating `Login2 → StartListenEx` as one direct pipeline
would therefore conflate two handles.

Conversely, `CQVIIPlugin::StartListen` overloads at `0x539080` and `0x5390b0`
are no-ops returning zero. Their names do not prove a listener. The automatic
`IQUIIDevice::OnConnect` call does prove a native subscription attempt after
a successful control-device login.

### Subscription and response framing

`SDK_StartListenEx` (`0x67bad0`) calls `CAlarmDeal::StartListenEx`
(`0x772608`, call `0x67bb34`). That function validates the SDK device,
avoids duplicate channel objects and invokes the device's channel-open method
with SDK selector **6** and callback mode **1** (`0x77274c..0x772760`).
Selector 6 is the SDK alarm-channel kind, not outdoor panel 6.

`CDvrDevice::open_channel` (`0x7ce664`) invokes
`sendAlarmQuery_comm(false, 1)` at `0x7cfbdc` and creates a
`CDvrAlarmChannel`. A separate mode-0 branch calls it with 0 at `0x7cfb20`.
`sendAlarmQuery_comm` (`0x7e2704`) delegates to `sendAlarmQuery_dvr2`
(`0x7e5b58`). These modes must not be mixed:

| Mode | Request evidence | Response evidence |
| --- | --- | --- |
| 0 | 32-byte header, command `A1` at byte 0 and boolean at byte 8 (`0x7e5bc4..0x7e5be4`) | `CDvrAlarmChannel::OnRespond`, `0x7db2ac`, checks `B1` at `0x7db310`; one exact-32-byte branch reads bitmaps at offsets 16/20/24 |
| 1, selected by StartListenEx | A sequence of 32-byte `68` headers: action 2 at byte 8, subtype at byte 12, device-information word at offset 28 (`0x7e5c88..0x7e5ca4`); loop covers subtypes 1–13, followed by additional explicit subtypes | `OnRespond` checks `69` at `0x7db6dc`; accepted subtype comes from byte 12, callback data starts at packet offset 32 and callback type is subtype + 100 (`0x7db7ac`) |

The mode-1 receiver has additional subtype filtering and a special subtype-5
branch; it is not a generic statement that every `69` packet is a doorbell.
`sendcammand_dvr2` (`0x7e46d4`) obtains `CDvrDevice::gettcp` and writes through
`CTcpSocket::WriteData` at `0x7e471c`. This is the SDK control connection, not
the accepted Python live stream's transparent FE channel.

The native library also contains a credential-bearing DVR login builder:
`build_login_packet` (`0x7c00dc`) sets command `0A`, a 32-byte header and
formats two supplied credential strings as `%s&&%s` at `0x7c0350..0x7c0370`.
`try_connect_dhdvr` (`0x7c1330`) calls it at `0x7c2db8`, writes its header
and body separately and parses the reply at `0x7c2efc`. `SDK_Login` uses
`CManager::Login_DevEx` (`0x68d054`), which dispatches through a selected
protocol function pointer. This establishes that starting this SDK path can
involve a separate credential exchange. It does not establish which plugin,
negotiation, credential transform or TLS behavior an A331 needs.

`IQUIIDevice::OnAlarm` (`0x4b0c8c`) translates several generic SDK event
types into `CQVAlarmInfo` and channel/status records, then commits signal
`0x1004` at `0x4b109c`. No inspected branch establishes that a particular
`B1` bitmap, `69` subtype, or one of those generic records means a physical
press on outdoor panel 1 or 2.

### What is still required before an autonomous listener

- An A331-specific control endpoint and selected protocol path, including
  whether it is accessible independently of Preview. The model's generic
  `port` field is not enough to assume 34567, 8443 or the CGI port.
- The exact control-login credential/key inputs, wire negotiation and TLS
  verification rules. Existing verified media credentials are not authority to
  invent a new secret-bearing exchange.
- A proven subscription subset and its teardown/renewal behavior for that
  firmware, rather than copying the SDK's broad list of DVR subscriptions.
- A physical-press mapping with panel identity, event state and duplicate/replay
  semantics. An event name or a nearby timestamp alone is insufficient.

These gaps prevent a justified background implementation. RC1 opens no new
alarm socket and sends no SDK alarm subscription.

## Voluntary observation on an existing video session

The existing FE candidate is narrower and observable without a new connection.
`QvPlayerCore.u` (`classes2.dex`, `0x28b2e0`) handles order 23 by passing
parameter byte 0 and the remaining UTF-8 text to `onOtherDoorBellCall`
(`0x28b3cc`). `DeviceCallBackImp.onOtherDoorBellCall` (`0x289110`) immediately
returns. Order 1 calls `ReceiveHangUpData` (`classes4.dex`, `0x21bdf0`),
which requires at least one byte (`0x21be02`). Neither path proves a physical
button event. The bounded diagnostic checks this minimum structure and exposes
the order-23 first byte only as an uninterpreted unsigned `candidate_selector`.
It never interprets or exports the UTF-8 tail. See the
[notification appendix](connect3-ring-evidence.md) for the extended trace and
cross-panel test procedure.

`DoorbellObservation.execute(..., channel=1|2)` selects one already-connected
session. It neither acquires media nor creates a socket, subscription, reader
or retry. The parent observer receives packets from the existing primary or
secondary reader, but accepts only the session fixed at observation start.
Replacement/closure of that session ends the observation; it cannot silently
follow a different camera. A marker or stop aimed at another channel is rejected.

Observation lasts 30–300 seconds, ends earlier with the media session or unload,
and retains at most 128 event records and five voluntary markers. Reports contain
the selected channel, relative milliseconds, sequence, command/order, lengths,
candidate type, structure validity and that single candidate selector. The
nearest-marker number and signed time difference describe proximity only.
Reports contain no raw payload, UTF-8 identifier, credential, absolute timestamp,
image or audio. A per-run comparison reports identical candidate contents
without exporting hashes or private tails, and does not deduplicate rings. Counters
explicitly retain `physical_ring_confirmed=false` and `ring_events_emitted=0`.

`tests/test_connect3_doorbell.py` covers both sessions, foreign/replaced/closing
sessions, deadline, unload, bounds, malformed candidate structure, marker
correlation and payload exclusion. Every case asserts no media acquisition or
release by the observer. These synthetic checks validate the observation
boundary; they cannot validate a real doorbell or hardware routing.
