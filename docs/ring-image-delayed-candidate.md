# Ring image: explicit four-second candidate

## Hardware result, 2026-09-23: idle use passes; after-video defect remains

Candidate `dfb0871` was installed for an attended owner Connect 2 test after
71 offline tests and all [CI jobs](https://github.com/Mins95/WelcomeEye/actions/runs/35787364670)
passed. Six component files were installed and SHA256-verified; no CRC package,
CRC diagnostics module, listener, transport or output code was installed/changed.

| Trial | Owner's physical observation | HA observation |
| --- | --- | --- |
| 1, videos closed | New native monitor photo present | ring_image sequence 1, source fresh_snapshot |
| 2, new scene and idle | New native monitor photo present | ring_image sequence 2, source fresh_snapshot; image visibly displayed in HA |
| 3, use/close HA video, instructed to wait for idle | Video OK, **no new native monitor photo** | ring_image sequence 3, source fresh_snapshot |

The original broad acceptance gate failed on trial 3. This does not isolate whether the
preceding media session, firmware release timing, or delayed snapshot caused
the missing monitor photo. No assertion that T+4 alone caused it is justified.
Actual fallback start latency was not recovered from the UI download during
the test; the four-second lower bound is verified by the offline simulated-clock
tests. The HA event timestamps alone do not measure ring-to-image latency.

Testing stopped immediately: no live-video ring, no further fallback attempt,
no increased delay and no output activation. Before rollback the process probe
showed zero HA media-named threads and one ring-listener thread. Six files were
restored to `895e7d2` (candidate-only image modules removed), then HA restarted.
An additional control test on restored `895e7d2`, with **no automatic HA
snapshot**, repeated video open/close then ring. The owner again reported no
new native monitor photo. The missing photo is therefore reproducible without
the delayed fallback; T+4 is not necessary to trigger this observed failure.
This does not prove the delayed fallback has no effect in other circumstances.

The owner then explicitly prioritized ordinary idle rings over the after-video
scenario. The T+4 candidate is retained with **MEDIA_FALLBACK_ENABLED=True** for
that scope. Its two idle rings are physically verified; the after-video defect
is tracked separately and must not be described as fixed or universally safe.
No teardown protocol change was made. Reinstallation on the owner's HA is
part of this attended continuation; no stable release or main change. The same
six files from `dfb0871` were reinstalled and SHA256-verified, with no functional
code change after those offline/CI tests. No physical output or power cycle was
used during these trials. Live-video ring and final microphone/Philips checks
remain unvalidated in this run and must not be inferred from the two idle rings.

The following sections describe the original candidate and validation plan.

This follow-up supersedes the previous automatic-fallback suspension **on the
feature branch only**. The owner explicitly selected a 4.0-second product delay
after observed native monitor capture at about two seconds. This is not a
protocol acknowledgment or a demonstrated safety guarantee.

Production remains on physically validated `895e7d2`. This candidate has not
been installed: the owner deferred physical testing. No change to main, version,
CRC packaging, output commands, media protocol or native teardown.

## Behavior

The accepted ring immediately fires `welcomeeye_local.ring`. The existing
serialized image coordinator records that ring's monotonic start time and
sequence. A correlated native provider gets at most the remaining time before
T+4; currently the provider returns unavailable without network I/O.

When native retrieval is unavailable, a cancelable asyncio timer waits until
that ring's T+4. No ring-triggered media acquisition happens earlier. One call
to the existing `capture_fresh_image(single_session_attempt=True)` acquires and
releases its shared consumer. Existing viewers retain their session. With no
viewer, normal worker cleanup follows. There is no second automatic snapshot
attempt after failure.

T+4 is the earliest **start of acquisition**, not a promise of a decoded JPEG
at exactly four seconds. Scheduling, login, receiving a fresh frame and cleanup
take additional time. A preceding operation is drained safely before handling
the newest pending ring. Its result is discarded if superseded; the newer ring
keeps its own deadline. The existing global 20-second image budget remains.

Memory-only ImageEntity, event ordering, JPEG validation and unchanged timestamp
on failure are preserved. Unload/shutdown wakes a pending timer immediately,
cancels its handle and drains any operation already holding a media consumer.

New safe diagnostics: `fallback_delay_seconds` and `last_fallback_started_ms`.
The latter is relative to that ring, not an absolute device timestamp.

## Offline tests and hardware gate

The ring-image suite uses a simulated clock for the four-second timer. It covers
native success before the deadline/no fallback, no media at 3.999 seconds,
one acquisition at 4.0 seconds, active/inactive shared media, snapshot timeout,
superseded timers, stale in-flight results, native decoding errors, unload,
shutdown, expired requests and consumer cleanup.

Local validation: 71 tests pass (11 dashboard resource, 13 fresh snapshot,
6 CRC packaging unchanged, 19 ring image, 5 passive trace, 17 doorbell).
Python 3.12 compile and `git diff --check` pass.

Hardware acceptance is **pending**, not inferred from these software tests:

1. At idle, repeat rings and verify both the monitor photo and HA image.
2. Repeat after HA video has closed and the device has returned to idle.
3. Ring with HA video active; verify the existing session is reused.
4. Verify video, microphone and Philips after several cycles without restart.

If T+4 still interferes with native capture or device release, stop testing;
do not change the delay to five/six seconds automatically.

## Native retrieval attempt, 2026-09-22

The owner requested an actual attempt to retrieve an existing native photo.
No new APK extraction was performed. A bounded connectivity check from the
owner's HA container found TCP 21, 80 and 443 refused. No FTP login, HTTP request
or CGI was sent. This says nothing about nonstandard ports.

Focused analysis of existing `libglnkio.so` found
`DataChannelIOCtrl::downloadFile` at `0x8f2b4`, size 168:

- `sendData2` kind 338 / `0x152`, 272-byte body;
- filename at offset 4, maximum 260 bytes;
- integer/byte parameters at offsets 264–271;
- rejection for channel member `0x140 == 0x400`.

Those parameter meanings and visitor-photo support are not established.
The download command was **not sent**. `remoteFileRequest` is also used by a
VOD playback source and was not treated as a JPEG download.

`QvFileCore.java:108` and `:143` implement a generic image/file download path;
`QvMediaFile.java` distinguishes picture media type 2 from video type 1.
These belong to the generic Quvii layer, not proof of LT monitor-memory access.
In the LT player, `QvLtPlayerCore.java:851` has an empty `setPlaybackList`;
the inspected Java searchRemoteFile occurrences were SDK wrappers only.
The owner also confirmed that the native photos are visible only on the
indoor monitor, not in the Philips app. This limits app-based observation;
it does not prove that no firmware-local retrieval operation exists.

One **read-only file-index request** was tested with the native
`searchRemoteFile` construction at `0x8ef00`: kind 333, 80-byte structure,
encapsulated in existing protected TLV 509. The date range covered the current
device-clock day and previous day; channel bit 0 and record filter 0 were
explicit experimental assumptions, not proven visitor-photo selectors.

The temporary connection used the app's control-only login profile 0/3/0.
It had its own sole reader and did not touch HA's existing listener socket.
No production integration file was changed, no media login/Start AV/playback
or output command was sent. Send allowlist: login 40/501, keepalive 49 and a
single 509 index request. Actual sent types: 40 once, 501 once, 509 once.

Result: authentication succeeded; **no response frame** arrived in the bounded
six-second observation (total 6196 ms); TCP closed in `finally`. Afterwards HA
had zero media-named threads and one ring-listener thread. No file or photo was
retrieved. No automatic retry or alternative file request was sent.

Silence cannot distinguish unsupported operation, wrong file filter/channel,
different envelope requirements or another firmware behavior. It is **not** an
empty-directory response and does not prove native retrieval impossible. Native
retrieval/correlation remains unresolved; no speculative provider is enabled.
`set.facepic.snap` remains untested, and cloud access was not added.

Workspace reproduction scripts (outside the shipped integration):
`work/native_photo_services.py`, `work/native_file_inventory_probe.py` and their
metadata-only `*-report.json` results. They are manual research probes, not
automatic capture code. No raw image, filename, credential or address is in
this report or its diagnostic output.

## Rollback

Production needs no rollback because it was not updated. For a future attended
installation, restore component files from `895e7d2`, remove candidate-only
`image.py`/`ring_image.py`/`ring_trace.py` if installed, and restart HA. Do not
change CRC dependencies or device settings. No photo files were written to
`/config/www`.
