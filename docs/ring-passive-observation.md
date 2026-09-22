# Passive Connect 2 observation — 2026-09-22

Branch `feature/042-ring-image-native-crc`; continuation of `26f7e61`.
Production baseline `895e7d2`. No main/release/upstream submission.

## Method and limits

Only metadata hooks were added to the existing RingListener / Session.read.
No second socket reader, additional device request, Start AV, CGI, microphone,
output command, native-file request or automatic snapshot was introduced.
The owner manually used the existing HA video for trial C, as requested.

`RingTrace` is disabled by default, capped at 4,096 events / 900 seconds,
and stores only numeric metadata with a relative monotonic clock. Its optional
log sink queues work on the HA loop; it performs no log I/O in the receive
thread. It records OWSP header/completion sizes, zero words, TLV type/length,
inner TLVs, alarm type, ring acceptance, keepalives, timeouts, framing errors
and listener/session changes. No payload, address, UID or device timestamp.

The limits bound **observation**, not media availability. They never trigger a
snapshot. Frame headers and TLVs preceding recognition can be re-anchored to
the ring using `observer_ms`; `t_ms` is relative to the latest accepted ring.
Sequence zero is the no-ring control period relative to observer creation.

The exported [metadata trace](evidence/ring-passive-20260922.jsonl) contains
375 ordered metadata records, orders 17–391, in the same listener session.
Logging was enabled after startup, so the first 16 in-memory startup records
are not in this export. The export begins at observer +30.167 s and ends at
+861.089 s. No absolute monotonic value or wall timestamp is exported.
These are decoded protocol metadata, **not** a raw network capture.

## Physical observations

| Trial | Conditions | Owner result | Network observation |
| --- | --- | --- | --- |
| A / ring 1 | Players closed; owner later pressed monitor Back to inspect memory | Photo recorded about 2 s after physical press | Ring type 14 at t0; type 26 at +20.522 s coincides approximately with manual Back |
| B / ring 2 | Idle, untouched until screen closes itself | Photo about 2 s; screen closes about 25 s | Only normal keepalive replies during first 30 s; no completion alarm |
| C / ring 3 | HA video opened then closed; owner rang immediately while plate light still on | No new native photo | Same ring and normal keepalive pattern; no special completion/error signal |
| C-recovery / ring 4 | Both plate light and indoor screen off; no player reopened | New native photo recorded | Same ring and keepalive pattern; no extra completion signal |

Owner timings are approximate, measured from the physical press. Trace timings
are measured from receipt/recognition of the ring, not an instrumented monitor
photo-write event. Do not claim sub-second alignment between those clocks.

The recovery required **no device power cycle**. No media-named threads remained
when checked after C; the plate's exact internal release state was not measured.
This supports a transient post-video conflict, not a proved release-command bug
or a measured safe delay. The visual light-off condition used by the owner is
not an identified software-readable readiness signal.

## Counts and interpretation

- 84 normal keepalive sends, each followed by TLV 57 of length 4, in OWSP
  frames of length 12. These also occur in the no-ring control period.
- Seven TLV 510 messages, body length 214 / OWSP length 222; each carries
  inner TLV 14854 length 158. Four have alarm type 14 (the four physical rings).
- Three have alarm type 26: ring 1 +20.522 s; ring 2 +119.074 s and +184.264 s.
  The latter two occur well after the reported native photo/end-of-call and
  among subsequent monitor/video interactions; their individual actions were
  not precisely timestamped. They are not evidence of native capture completion.
- No zero-length OWSP, framing error, decode error, observed disconnect/reconnect
  or session change in the exported interval.
- Around +2 s and +25 s, successful B and failed C expose the same ordinary
  keepalive pattern. No reliable local completion/media-free signal found.

Static APK corroboration: `com/quvii/compathlt/LtConst.java:6` names 26
`ALARM_TYPE_CALL_ACCEPTED`; `QvCompatLtManager.java:674` maps LT 26 to Quvii 20.
The generic Quvii enum uses different numbers, so its type-26 meaning must not
be applied to the LT path. A call-accepted/monitor-dismiss event is not a
photo-write acknowledgment.

**Decision: MEDIA_FALLBACK_ENABLED remains False.** No wait-X implementation or
hardware gate is claimed. Repeating a no-ring reboot trial is not needed to
explain the observed absence; a new native state/API proof is needed instead.
This observation does not prove that no firmware/API can expose such a signal.

## Native-photo investigation continued offline

Existing `work/talkback_native/libglnkio.so`, SHA256
`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.
Focused ARM Thumb disassembly of the file-search/request functions only:

| Function/address | Observed request construction |
| --- | --- |
| searchRemoteFile / 0x8ef00 | sendData2 kind 333 (0x14d), 80-byte structure |
| searchRemoteFile2 / 0x8efc8 | kind 1200 (0x4b0), 76 bytes |
| remoteFileRequest / 0x8f068 | kind 336 (0x150), 296 bytes; file name at offset 4, max 260 bytes; channel at 292; control bytes at 293–294 |
| remoteFileRequest2 / 0x8f104 | kind 1203 (0x4b3), 40 bytes |
| remoteFileCtrlRequest2 / 0x8f1fc | kind 1205 (0x4b5), 36 bytes; calls getPlayBackStreamType |

All have an early rejection for channel member at offset 0x140 == 0x400;
that member's meaning and this device's supported file classes are unproven.
`DataChannel::sendData2` at 0x8902c builds OWSP and, conditionally, a private
509 envelope; do not assume these request kinds are always sent top-level.

Java `glnk/client/DataChannel.java:300–317` exposes JNI wrappers.
`glnk/media/GlnkVodDataSource.java:235` uses remoteFileRequest in a playback
data source, with media/EOF callbacks. This is not proof of a side-effect-free
JPEG download. No Quvii app call to searchRemoteFile/remoteFileRequest, visitor
photo filename, photo-ID mapping or local FTP path was found in the inspected
Java paths. No such request was sent to the device.

The cloud recordSubResUrl path and app-local LT alarm database findings in
[the main report](ring-image-crc32c.md) still apply. The generic face CGI remains
unproven and was not sent. A next active file query would first need a confirmed
file class/index and response semantics that cannot start playback. No local
native-photo provider can responsibly be implemented from the current evidence.

## Software and rollback

64 integration tests pass (59 previous + 5 metadata privacy/limits/single-reader
tests). Existing ring-image architecture, fallback gate, entity and events are
unchanged. [Integration CI](https://github.com/Mins95/WelcomeEye/actions/runs/35784419246)
also passes Python 3.12/3.14 compile, HACS, Hassfest and actual HA ImageEntity.

Production received only client.py/ring.py metadata hooks and ring_trace.py;
it never received the candidate ring-image feature during this observation.
After the trials the two files were restored byte-for-byte to `895e7d2`, the
temporary module removed, and HA restarted. CRC production dependencies were
not changed. The branch retains opt-in instrumentation for future investigation.
The installed baseline hashes were verified after restart. The owner then
confirmed HA video works and Philips regains live video after the HA player
closes. No device power cycle was needed during these passive trials/recovery.
