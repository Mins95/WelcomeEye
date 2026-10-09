# Connect 3: notification and independent snapshot evidence

This appendix extends [the RC1 SDK review](connect3-rc1-sdk-evidence.md).
It follows notification registration, callback translation and teardown in the
original Philips Door Connect `1.0.123.3(2)` application. No application code was
executed and no intercom or cloud service was contacted. It does not establish a
physical bell press, a firmware-supported background subscription or a safe
automatic visitor photo.

## Sources and reproducibility

The XAPK SHA256 is
`f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
ARM64 `liblive_player.so` SHA256 is
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
Native addresses below are ELF virtual addresses. DEX addresses identify code
items or individual instructions in the named DEX.

The checked sources are `work/connect3-static/classes*.dex` and
`work/connect3-static/liblive_player.so`, extracted from
`com.extel.philipsdoorconnect.apk` and its ARM64 split. The older
`work/decompiled` / `work/xapk` tree belongs to Philips WelcomeEye; its Java was
used only as a search aid, never as proof of the selected Connect 3 app path.

Additional offline extracts are under `work/050-analysis/`:

- `rc1-ring-app.txt`, `rc1-ring-push.txt`, `rc1-ring-qv-registration.txt`,
  `rc1-ring-cloud-dispatch.txt`, `rc1-ring-login-mode.txt`;
- `rc1-ring-native.asm`, `rc1-ring-dispatch.asm`, `rc1-ring-state.asm`;
- `rc1-native-snapshot.asm`, `rc1-native-snapshot-selection.asm`,
  `rc1-ring-xrefs.txt` and its generator `rc1_ring_xrefs.py`.

`inspect_talk.py` produces selected DEX method dumps and native disassemblies.
The snapshot cross-reference scan covers direct ARM64 `B`/`BL` instructions in
`.text`, including resolved PLT targets, and the relocated snapshot vtable.
Absence from that scan does not exclude indirect calls. Binaries and extracted
application code are not distributed with this integration.

## The V1 cloud subscription cannot simply be reused

The existing [V1 cloud listener](v1-cloud-doorbell.md) registers its own FCM
client and device UID with the LT push service, without a Philips account.
`v1_cloud_protocol.py` builds the LT `pda_more_api.php` / `pushCheck.php`
requests, including the `philips` tag, `tiandi.json` application key and alarm
types `14,26`. Its event parser expects the LT `message_content` structure.

The Connect 3 APK contains both LT compatibility and QV notification code. Their
presence together is not evidence that an A331 can use the V1 registration:

| Selection or request | Exact evidence | Consequence |
| --- | --- | --- |
| `QvOpenSDK.t` | `classes2.dex`, code item `0x2b1b00`: `isLtDevice()` at `0x2b1b16` **and** `SDKConfig.isNoLoginMode()` at `0x2b1b22` gate `QvCompatManager2.setAlarmStatus` at `0x2b1b46`; otherwise `QvAlarmApiInterface.setAlarmStatus` at `0x2b1b64` | The LT accountless branch is conditional, not a generic QV registration |
| Device cloud family | `QvDevice.isLtDevice`, code item `0x2e3524`, compares cloud type with `3`; the same class defines QV as `1` | A QV device does not take this LT branch |
| Subdevice subscription | `QvOpenSDK.b1`, code item `0x2b2ea8`, has the same two checks and separate fallback at `0x2b2f0c` | A second channel/subdevice does not bypass the provider distinction |
| QV subscription | `QvAlarmCore.setAlarmPush`, code item `0x27fc48`, calls `checkLogin` at `0x27fcb4`; `AlarmRequestHelper.getAlarmSettingsReq`, `classes4.dex`, uses `client-update-dev` at `0x22a7a4` | The QV branch has a separate alarm registration lifecycle |
| QV login envelope | `AlarmRequestHelper.getLoginReq`, code item `0x22a9a0`: `client-login` at `0x22a9b0`; auth request URL at `0x22a9c4`; client/token/OEM/app fields at `0x22aa00` onward; `ACCOUNT_ID` at `0x22aa26` | It uses cloud client/account/auth-server context, not the local stream password alone |
| Envelope header | `AlarmRequestHelper.initHeader`, `SESSION_ID` read at `0x22b0bc`, with flag `tdkcloud` | Local CGI authentication is not proven interchangeable with cloud session identity |
| HTTP endpoint | `AlarmApi` annotations in `classes4.dex`: login, settings, logout and token update are XML `POST UserAlarm` | These are not the LT PHP subscription requests |

The default APK account mode is also distinct from the LT no-login branch:
`SDKConfig.isAccountLogin` initializes true; `getLoginMode` (`0x2d8fec`)
defaults accordingly; `isNoLoginMode` (`0x2d8f90`) checks mode `1`.
However, `QvAlarmCore.loginAlarm` (`0x27f5ec`) explicitly handles a missing
user by choosing string `"0"` at `0x27f618`. Static defaults therefore do **not**
prove that every QV alarm registration requires a human account. They also do
not prove that the server accepts an accountless registration for the selected
A331 firmware. No such registration has been validated here.

The reusable part of V1 might be its generic FCM transport. The cloud
registration, app identity/token configuration and message parser require
separate proof. This review does not enable the V1 service for Connect 3, invent
a new cloud login, or assume the CGI `authCode` is a cloud or native SDK password.
“Subscription” here means notification registration, not a paid plan.

## What the app actually receives while idle

The Connect 3 manifest declares `com.quvii.qvfun.push.fcm.FcmPushService`.
`AppConfig.<clinit>` (`classes4.dex`, `0x1b40d4`) assigns `PUSH_MODE=10` at
`0x1b412c`. `PushHelper.InitPush` (`0x1fd210`) routes modes 1 and 10 to
`QvFcmPushManager.initPush` at `0x1fd28a`.

`QvFcmPushService.onMessageReceived` (`0x229d3c`) has separate provider
branches. Its QV branch reads `AlarmId`, `DevUmid`, `DevChNo`, `AlarmEvent`,
`AlarmTime`, `AlarmSource`, `AlarmInfo`, `AlarmState` and resource URLs.
`DevChNo` is parsed at `0x22a2b4` and incremented by one at `0x22a2bc`, with a
default of 1 when absent. This is explicit cloud channel normalization; it
must not be applied to the unrelated live FE payload by analogy.

The app then uses:

1. `FcmPushService.onQvMessageReceived` (`0x1fe8b8`): a do-not-disturb guard,
   `AlarmMessageInfo(QvPushInfo,1)` at `0x1fe916`, and `HandlePush` at `0x1fe926`.
2. `AlarmMessageInfo` constructor (`0x1fdec4`): copies alarm ID, channel, event,
   time and state; the cloud event is not translated into a live FE order.
3. `PushHelper.HandlePush` (`0x1fd0f8`): call events 19/38/45, corresponding
   cancellation events 20/39/46, and generic processing for other alarms.
   Event 38/state 1 has its own cancellation path.

The enum label `DOOR_BELL=7` exists alongside `CALL=19`, indoor 38/39 and
manager 45/46. The numeric names alone do not establish which event a physical
A331 button emits. `HandlePush` suppresses another call activity while
`isProcessCalling` is set (`0x1fd1ea` onward); this is UI state, not a proven
ring deduplication key. Although `AlarmId` is available, no generic alarm-ID
deduplication has been established in this dispatch path.

## Native alarm stream: further translation and teardown

The earlier review establishes the distinct SDK control login followed by
`SDK_StartListenEx` selector 6, mode 1 and command `0x68`, with `0x69`
responses. The additional native trace resolves what happens after reception:

- `CDvrAlarmChannel::OnRespond` (`0x7db2ac`) reads header byte 12 in mode 1.
  Subtype 5 with header byte 13 equal to 1 is rewritten to 254. Accepted
  subtypes include 1–8, 10–13, 157–190, 192 and 251–255; the forwarded callback
  type is subtype plus 100, and the body begins after the 32-byte header.
- `CAlarmDeal::DeviceStateFunc` (`0x770134`) translates callbacks, including
  expanding 4/8-byte channel bitmaps into 32/64 per-channel state bytes. Other
  callback cases produce structured records. Thus callback numbers, alarm
  enums and wire bytes are separate layers.
- `IQUIIManager::OnSDKAlarm` (`0x5326f4`) validates the manager, nonempty body
  and device handle, then calls `IQUIIDevice::OnAlarm` at `0x5327d8`.
- `IQUIIDevice::OnAlarm` (`0x4b0c8c`) handles selected enums. The bitmap group
  requires 32 or 64 bytes. The record group consumes 32-byte records whose
  first bytes are channel, alarm number and status. A separate group accepts
  one byte without a channel record. Handled events are emitted as
  `CQVAlarmInfo` through signal `0x1004` at `0x4b109c`; unsupported cases return.
- `IQUIIDevice::OnDisconnect` (`0x4af7dc`) calls `OnAlarmListenStop` at
  `0x4af97c`. `OnAlarmListenStop` (`0x4afe58`) checks the active flag and login
  handle, clears the flag at `0x4afe98`, then calls `SDK_StopListen` at
  `0x4afea0`. `SDK_StopListen` (`0x67bb90`) calls `CAlarmDeal::StopListen`
  (`0x772850`), which closes/removes the per-device alarm channel under a lock.
  `CDvrAlarmChannel::close` (`0x7db27c`) removes it from the device. This trace
  does not identify a firmware-specific remote unsubscribe packet.

These paths establish lifecycle and payload translation, not a complete A331
listener. The control login endpoint/credentials, exact firmware subscription
choice, physical press event, channel mapping and deduplication remain
unconfirmed. The integration therefore does not create a background listener.

## A real native snapshot path exists, but is not yet an A331 photo service

There are several different snapshot interfaces in the library:

| Path | Evidence | Established boundary |
| --- | --- | --- |
| App renderer snapshot | JNI `QvJniFunc_snapShot` calls `QVPlayerSnapshot` at `0x455028`; `snapShotCompat` at `0x45de38`; `QvJniApi_snapshot` calls `QVPanelSnapshot` at `0x462864` | Captures an existing player/render frame |
| Generic core snapshot | `QVCoreSnapshotCreate` (`0x82cb7c`) asks for `IQVSnapshot` at `0x82cbc0` | Distinct native interface, not evidence that the Java app uses it for A331 |
| Network snapshot implementation | `QVNETSDKGetInterface` accepts URL schemes `quii`, `umsp`, `tdks` at `0x4888f8` onward, compares `IQVSnapshot` at `0x488df0` and constructs `CQUIISnapshot` at `0x488e28` | A network snapshot implementation is present |
| Event snapshot wrapper | `QVSnapshotOpen` (`0x82cdfc`) asks for **`IQVEVTSnapshot`** at `0x82ce4c` | It must not be conflated with the proven `IQVSnapshot` implementation |

`CQUIISnapshot::SendSetup` (`0x4a087c`) constructs command `0xA9` with byte 9
equal to 3. Its `SendOpen` (`0x4a0934`) calls virtual offset `0x1d0`; the
relocated vtable resolves this to `CQUIISnapshot::SendPlay` (`0x4a0960`). That
function constructs command **`0x11`**, adds a time value, two 16-byte masks,
username/password separated by `&&`, custom ID, SHA handling and conditional
encryption. The constructor initializes both masks with the first eight bytes
set and the following eight zero; its `ParseURL` (`0x4a1014`) simply copies the
URL in the inspected body. It does not establish a selected-panel selector.

This is a genuine candidate protocol distinct from live command `0x10`, and
distinct from `set.facepic.snap`, but it still creates a network stream and
performs setup/authenticated request exchange. No direct call to
`QVCoreSnapshotCreate` was found in the scanned `.text`, and no selected A331
Java caller, firmware capability, response framing or harmless interaction
with an active bell call has been established. An HTTP JPEG endpoint or a
nonintrusive visitor photo cannot be inferred from these names. Stored-photo
download is another path (`QvFileCore.startDownload`), not this interface.

## Implemented bounded observation

The existing reader can observe FE order 23 while a camera is already open.
`QvPlayerCore.u` (`classes2.dex`, code item `0x28b2e0`) passes payload byte 0
at `0x28b3c8` and the UTF-8 tail to `onOtherDoorBellCall(int,String)` at
`0x28b3cc`. No physical press semantics are established by that callback name.

The diagnostic now reports `candidate_selector` only for structurally valid
order-23 packets. It is the raw unsigned wire byte, 0–255, with
`candidate_selector_interpretation: unknown`. Java uses signed `aget-byte`;
the diagnostic deliberately preserves the wire octet. This value is **not**
the selected live channel. The report's `channel` is the camera session chosen
by the user. The UTF-8 tail is never exported.

Equal order/parameter contents within one observation are related by
`same_candidate_as_sequence`, using an in-memory HMAC with a fresh random
per-run key. Neither raw tails, keys nor digests are exported. At most 128
events/fingerprints and five voluntary markers are retained; counts can
continue after the event cap. Keys/fingerprints are discarded on stop,
deadline, session closure or unload. Equality does not suppress packets or
identify duplicate physical presses.

Connect 3 accepts 30–300 seconds, default 90, in both the service schema and
the observer. The timer uses the requested duration without automatic repeat.
There is one observation per intercom, bound to one exact existing session.
Channel 2 resolves to `hub.channel2.live.session`; packets from channel 1 are
ignored. A replacement session ends the run rather than silently retargeting
it. The observer never acquires media or opens a socket. No Home Assistant
ring event is emitted, including after a marker or an order-23 candidate.

### Voluntary cross-panel check

The callback is named `onOtherDoorBellCall`. Prioritize crossed trials on the
same intercom with its two panels; watching and pressing the same panel could
miss a notification specifically concerning the other panel. Keep three
values separate: the viewed channel, the physically pressed panel, and the
uninterpreted candidate byte.

1. Open panel 1's live video in the WelcomeEye card and leave it open.
   In Home Assistant **Developer tools → Actions**, select
   `welcomeeye_local.connect3_observe_doorbell` and target the Connect 3
   diagnostic status sensor. Use `operation: start`, `channel: 1`,
   `duration: 300` (five minutes maximum).
2. Press **panel 2's** physical bell once, then immediately run the same action
   with `operation: mark`, `channel: 1`. Use `status` to read the result or
   `stop`, `channel: 1` to finish early. Save the response/diagnostics before
   starting another run, which replaces the previous observation. Label the
   saved report manually **view 1 / press 2**; the marker does not itself
   record the physically pressed panel.
3. Open panel 2's live video, then repeat `start`, one physical press on
   **panel 1**, `mark` and `stop` with `channel: 2`. Label this saved report
   **view 2 / press 1**. Stop the first observation before starting this one.
   If the video closes or is replaced, start a fresh observation.
4. Same-panel trials (**view 1 / press 1**, **view 2 / press 2**) can provide
   controls afterward, each in its own saved run. Always set the action's
   channel to the **viewed** channel, never to the pressed panel merely to
   label a marker. Do not run two observations concurrently.

Each run stops after the requested duration or on video closure. Markers
record relative time only; the procedure reports candidates and their timing,
never a confirmed or fabricated ring. Synthetic tests cover exact-session
selection, channel 1 then 2 without concurrency, a real 300-second timer
argument, duration limits, raw-selector privacy, bounded equality state and
cleanup. They do not replace a user-observed physical press.
