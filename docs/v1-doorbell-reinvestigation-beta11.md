# WelcomeEye Connect V1 — local doorbell reinvestigation, beta 11

Static audit dated 2026-10-07. The local V1 doorbell remains **unresolved**, not
proved cloud-only. The previous unsuccessful `0/3/0` listener establishes a
negative result for that session and those observations; it does not exclude
another local session, activation sequence or firmware behavior.

This audit found two concrete native SDK details beyond the previous report:
the long-connection option forces stream type **7**, and the SDK's default
keepalive interval is **5 seconds**. Neither is a demonstrated V1 alarm
subscription. No device connection, cloud registration, physical action or
integration behavior change was performed.

## Sources and scope

The existing APK extraction is `work/decompiled/sources` in the research
workspace, outside this repository. Java paths below are relative to that
directory. Native addresses refer to its existing ARM `work/native/libglnkio.so`:

```text
SHA256 25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992
```

Addresses are Thumb code addresses with the low function-pointer bit cleared.
The JNI table and branch tables were checked directly against ELF bytes, in
addition to the existing `symbols.json`, `plt.json` and `work/asm_range.py`.
Previous evidence was rechecked against
`work/v1-doorbell-apk-chain-20260923.md`,
`work/v1-doorbell-native-parser-20260923.asm.txt` and
`work/v1-parse-alarm-info-20260923.asm.txt`.

This is a targeted audit of the LT application, channel lifecycle, JNI entry
points and native alarm/keepalive paths. It is not an exhaustive proof about
every native function, APK version or device firmware.

## JNI entry points and actual session parameters

The registered methods are concrete native APIs, rather than conclusions drawn
from suggestive symbol names:

| JNI table address | Method and signature | Native wrapper |
| --- | --- | --- |
| `0x168bd0` | `native_setLongConnFlag (I)V` | `0x87ab4` |
| `0x168ba0` | `native_setAliveInterval (I)V` | `0x877b4` |
| `0x168abc` | `native_sendAliveReq ()I` | `0x86cc0` |
| `0x168a2c` | `native_setMetaData ([B[B[BIII)I` | `0x8652c` |
| `0x168900` | `native_parseAlarmInfo (Ljava/lang/String;)Ljava/lang/String;` | `0x62458` |

`native_setLongConnFlag` reaches `DataChannel::setLongFlag` at `0x8869c`:

```text
store supplied flag at this + 0x98
if flag != 0:
    store 7 at this + 0x164
```

`DataChannel::getStreamType` at `0x88ba8` returns `this + 0x164`, confirming that
this changes the **stream type**, not an arbitrary alarm flag. `setParams`
(`0x88778`, relevant block `0x888fe–0x8891a`) preserves stream 7 when the long
flag is nonzero; otherwise it stores the requested stream. Channel and mode
remain the adjacent fields `+0x160` and `+0x168`.

These fields reach login: `ConnChannelPeer::onChnConnected` at
`0x8d85e–0x8d874` and `onReLogin` at `0x8d95e–0x8d976` pass channel, stream and
mode to `requestLogin`; the forwarding channel's relogin does the same at
`0x933ca–0x933e2`. This is evidence of a real alternative session parameter,
not evidence of a ring callback. Stream 7 also occurs in the encrypted-login
branch at `0x8d782–0x8d7b4`, so it cannot be classified as alarm-exclusive.

The complete extracted Java search finds `setLongConnFlag` only in
`glnk/client/DataChannel.java:398` and the
`glnk/media/GlnkDataSource.java:446` SDK wrapper. There is no application-side
caller in this extraction. In particular, the examined LT application uses:

| Source | Requested channel / stream / mode | Demonstrated purpose |
| --- | --- | --- |
| `com/quvii/compathlt/QvLtPlayerCore.java:620–624` | `channel + 15 / 1 / 2`, ordinarily `16/1/2` | Live preview |
| `com/quvii/compathlt/device/ProtocolTaskHelper.java:194–205,580–581` | `channels / 3 / 0`, default `0/3/0` | Bounded device commands |
| `glnk/client/NetworkTest.java:76` | `0 / 65539 / 0` | SDK network test |

The last value is `0x10003`; native `setParams` retains low stream bits and
uses the upper flag (`0x88940–0x88950`). It is not a discovered alarm channel.
There is still no application call sequence showing which channel/mode should
accompany stream 7 for a V1 ring, nor a proven alternate local alarm port.

## Keepalive: exact request and timing difference

`DataChannel::sendAliveReq` at `0x892a4` constructs one OWSP TLV with:

```text
type = 49 (0x31)
payload length = 4
payload = [channel & 0xff, 0, 0, 0]
```

It calls `packetOWSP` at `0x892d0` and `sendDsData` at `0x892d8`. No alarm
identifier, subscription mask or additional registration payload is built by
this function. This matches the request in
`custom_components/welcomeeye_local/client.py`, `Session.send_keepalive()`.

The native constructor stores `0x004c4b40` (5,000,000 microseconds) at `+0x29c`
at `0x87fb4–0x87fc0`. The scheduling chain was checked:

1. `onReading`, `0x8b3aa–0x8b3ee`, compares elapsed time with that interval and
   posts `AMessage` type `0x18` (24).
2. `onMessageReceived` dispatches through the halfword table at `0x8b782`.
   Entry 24 resolves to `0x8bb0e`.
3. `0x8bb0e` calls `DataChannel::sendAliveReq` through PLT `0x15df90`.

`setAliveInterval`, `0x88b08–0x88b3c`, rejects changes once active; with the
normal positive starting interval it clamps a nonzero setting to 2–5 seconds
and converts to microseconds. Zero disables the timer. The extracted app has
no caller of this setter or the manual `keepliveReq` API outside SDK wrappers.

The integration's session/ring loops currently use 10 seconds. This is a
specific SDK difference that can be controlled in a future bounded experiment.
It does **not** establish that 10 seconds caused the missing rings: the old
listener authenticated and remained alive. A shorter heartbeat is not an
alarm subscription, and a keepalive reply must never become a ring event.

## Alarm input and callback tracing

The local parser capability remains real. In `DataChannelIOCtrl::onParse`, the
TLV table at `0x911d6` routes type 510 to `0x91a7a`, calls
`parsePriProRspData` at `0x91a92`, then recursively parses a positive decoded
payload at `0x91aa6`. Inner type 14854 takes the generic branch at `0x919d4`.
Its callback at `0x919f8` uses vtable offset `0x34`; the JNI adapter relocation
at `0x16686c` identifies `JNIDataAdapter::onIOCtrl`, through the secondary-base
thunk, reaching native `0x94290` and Java
`glnk/client/JNIDataAdapter.java:185–186`.

The decoded local input therefore reaches a generic callback. The application
consumers inspected do not complete a ring-dispatch chain:

- `QvLtPlayerCore.java:269–282` handles IO control 426 (unlock response).
  Its manufacturer callback handles stream/fps replies; JSON delegates to
  `SdkLtUtil` for start-stream, device-type and unlock results. The private
  callback at line 336 only logs its input.
- `ProtocolTaskHelper.java:494–579` matches replies to the pending request
  (`executeType + 1`, or its manufacturer/private equivalent). No independent
  ring subscription was found there.
- Preview authorization calls `getDeviceInfo` at
  `QvLtPlayerCore.java:176,447`. `LtDeviceManager.java:482–618` builds channel,
  CCTV status, ability, firmware and upgrade-status queries. The inspected
  sequence does not register an alarm listener.
- `GlnkService.java:79–88` event 2 provides push-server metadata
  `(device, server, port)`, not an alarm payload; the LT manager's
  `onPushSvrInfo` callback is empty. Event 11 at lines 139–146 provides a
  doorbell web domain; its LT consumer logs it. These callback names do not
  establish a separate local notification connection.

Two additional native names were disambiguated. `CloudConfigMgr::alarm_get`
(`0x7fdb8`, 36 bytes) and `alarm_get2` (`0x7fddc`, 52 bytes) scan supplied
NUL-terminated string tokens and return token boundaries/lengths; `alarm_get2`
also checks a minimum token length. They are not network `getAlarm` requests.
`GlnkService::parseAlarmInfo` (`0x65a0c`, 340 bytes) validates and decodes a
supplied string and calls a security implementation. Its Java references are
SDK wrappers. The inspected entry point does not open a local listener.

The separate received-call path is explicit:
`QvFcmPushService.java:99–126` consumes Firebase `message_content`, creates LT
push information, and calls the compatibility manager. That manager maps LT
14 to application CALL=19; `LtAlarmManager.java:339–368` deduplicates and stores
the result. Its `getAlarmList` reads the app database. `AlarmHelper` supplies
application alarm behavior/labels; it does not turn that database operation
into a local intercom poll.

`LtAlarmManager.java:405–445` registers notifications through public LT HTTP
endpoints, including the FCM token and device alarm selection. This proves an
application cloud path. It is neither a local OWSP subscription to reproduce
nor proof that a parallel local firmware path cannot exist. No registration
or request to those endpoints was executed for this audit.

## What this permits and what remains missing

| Finding | Supported conclusion | Missing evidence |
| --- | --- | --- |
| Previous `0/3/0` session saw 40/57/70/502, no reliable 510/14854 | That observed profile did not deliver a usable ring | Other session/state/firmware behavior |
| Native 510/14854 parser and JNI callback | SDK can deliver such local input | V1 actually emits it and its required activation |
| Nonzero long flag forces stream 7 | Concrete alternative SDK session parameter exists | App use, alarm meaning, V1 acceptance and channel/mode |
| Native five-second keepalive | Reproducible timing difference | Causal link to V1 alarm delivery |
| Proven FCM receive chain | App has cloud notifications | Absence or presence of a parallel local notification path |

There is no newly proven local alarm request to implement. A next local
investigation can first observe the already validated `16/1/2` session, using
the existing media worker as its only reader, and distinguish an actual
ring-correlated message from routine traffic. The stream-7 behavior is a
separate candidate for further protocol analysis, not a justified default or
a reason to silently restore the failed listener. No phone capture or phone
setup is requested by this report.

The V1 standby guards remain unchanged. Existing regression coverage includes
`test_v1_start_does_not_open_ring_listener` in `tests/test_hub_lifecycle.py`
and `test_v1_ignores_local_ring_and_late_listener_state` in
`tests/test_doorbell_trial.py`. This documentation-only audit did not rerun
those tests or establish hardware behavior. Connect 2's confirmed listener,
V1 video and single-shot output control are unchanged.
