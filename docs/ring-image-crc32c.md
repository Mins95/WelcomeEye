# 0.4.2 work report: ring images and native CRC32C

Branch: `feature/042-ring-image-native-crc`, based on validated `895e7d2`.
No main change, version bump or release. No work on V1 ring detection.

## Hardware finding: automatic fallback is currently suspended

The first real Connect 2 ring produced a validated JPEG and the HA event
`welcomeeye_local.ring_image`, sequence 1, source `fresh_snapshot`.
`image.welcomeeye_connect_2_last_ring` acquired a new timestamp.
However, the owner reported that the monitor did **not** store its normal photo.

We suspended automatic media fallback, restarted HA, and repeated a physical
ring with both players closed. The owner confirmed that the monitor stored
its photo again. This A/B test supports a conflict between immediate media
acquisition and native recording. It does not establish the firmware's internal
timing or prove that an arbitrary fixed delay would solve it.

Consequently `MEDIA_FALLBACK_ENABLED = False` remains the safe default.
The existing ring event and manual snapshots continue to work. Automatic
image acquisition is **not ready for release** under the requested constraints.
No artificial sleep, speculative CGI, or second video session was added.
The framework is implemented and tested, but this hardware gate is unresolved.

## Native photo investigation

Existing extraction root: `work/decompiled/sources` (workspace, not repository).
No new APK extraction was needed. Relevant Java source paths below are
relative to that root:

| Evidence | Finding |
| --- | --- |
| `com/quvii/compathlt/QvLtPlayerCore.java:1091` | `snapShot` calls the active renderer's `snapShotCompat`; this is a fresh local player capture, not a monitor-memory download. |
| `com/quvii/compathlt/device/TCRequestBean.java` | Bitstream settings, I-frame request and unlock builder; no visitor-photo retrieval. Unlock code untouched and never executed. |
| `com/quvii/core/QvAlarmCore.java:648` and `:962` | Alarm service results supply IDs and resource/subresource URLs; service address comes from QvLocationManager. |
| `com/quvii/qvweb/Alarm/api/AlarmApi.java:43` | Query is an XML POST to `UserAlarm`, not an identified device-local endpoint. |
| `com/quvii/qvweb/publico/utils/RetrofitUtil.java:133` | Alarm Retrofit client uses the supplied service base URL and alarm cookie client. |
| `com/quvii/qvweb/userauth/QvLocationManager.java:313` | Service selection goes through SDK service discovery / region and server data. |
| `com/quvii/qvweb/Alarm/bean/response/AlarmListQueryResp.java:72` | `recordsubresurl` is an optional alarm service field. |
| `com/quvii/qvfun/push/PushCallNotificationHelper.java:174` | Glide loads this URL for the notification image. |
| `com/quvii/compathlt/QvCompatLtManager.java:465` | LT alarm list is merged from LtAlarmManager. |
| `com/quvii/compathlt/alarm/LtAlarmManager.java:64` and `:291` | LT list reads the app's QvLtPushBean database and maps alarm fields; this path does not retrieve the monitor photo or attach a subresource URL. |
| `glnk/client/DataChannel.java:312` | Generic remote file search API exists, but no call to `searchRemoteFile` was found in the Quvii app source. Its presence does not establish visitor-photo support. |

Generic native CGI, previously disassembled and reviewed:

- `work/talkback_native/liblive_player.so`, ARM32 Thumb;
  SHA256 `9ce7549858e01dd73d858cb956a2e082fbe828675a830730bd932692838386fe`.
- `QVCGIConfigPreviewSnapshot` at `0x308c42`,
  `IQVCGIConfig::PreviewSnapshot` at `0x2fd870`, `SendCmd` at `0x2fb674`.
- POST `set.facepic.snap`, body `<content><chn>%d</chn></content>`;
  channel argument + 1; SendCmd timeout 3000 ms.
- XML success handling includes `error`, `info`, `sex`, `wh`, base64 `face`
  and optional `fea`. This looks like a generic face-capture mechanism.
- The inspected caller uses a temporary HTTP socket and does not call Start AV.
  Firmware-side capture resource usage, persistent effects, JPEG format and
  WelcomeEye local CGI support are **not established**. The CGI was not sent.

In `libglnkio.so`, generic `searchRemoteFile` and `remoteFileRequest` symbols
are at `0x8ef00` and `0x8f068`; their names alone are not a protocol proof.

The captured local `reportAlarm` messages identify a ring with type/channel/
timestamps, not a photo ID, file path or capture-completion signal. The
existing listener retains only the validated ring identity. No complete
local chain from monitor-memory file to app retrieval has been established.
The identified resource URL chain is a cloud alarm-service path; it has not
been proved to expose this monitor's native photo either. No cloud dependency
was introduced. Native retrieval remains **unidentified**, not proved impossible.

Permission constants 13/14/15 occur in LtCommonCode, but no reliable binding
to a native photo-completion event was found. Successful login is not sufficient:
the hardware test shows that media may be granted while native recording loses
its photo. Therefore this implementation does not pretend login is a safe
capture-completion signal.

## Implemented image pipeline

- Immediate existing `welcomeeye_local.ring`, now with monotone runtime
  `ring_sequence` (resets with a new hub after restart).
- One asynchronous capture task; newest pending ring replaces older pending
  work. In-flight work is drained safely, and stale results are discarded.
- A native provider interface requires correlation to the specific ring and
  validates JPEG decoding in HA's executor. Current provider reports no proven
  local capability; it does not manufacture a network request.
- Fallback uses only `capture_fresh_image` / hub acquire/release. A new
  ring-only worker stops after its first failed media session; existing live
  viewers retain their original connection behavior. No output command retry.
- One native attempt (up to 3 seconds), one shared snapshot acquisition (up to
  15 seconds), a 20-second result freshness budget including queue wait. Normal
  protocol cleanup is drained even when it extends beyond the image budget.
- Real HA ImageEntity: `image.<device>_last_ring`, memory-only JPEG,
  `image/jpeg`, same device. No acquisition in properties or `async_image`.
- A new successful image changes the timestamp. Failed capture leaves the
  prior image, timestamp and image's sequence intact, with `capture_status=failed`.
- `welcomeeye_local.ring_image` is emitted only after a new image has been
  committed and entity state written. Payload: entry_id, image_entity_id,
  ring_sequence, source. No bytes or secret URL.
- No image files in `/config/www`, no new public endpoint, no JPEG in
  attributes/logs/diagnostics/Recorder. The normal HA ImageEntity proxy is used.

The event contract can be consumed without integration internals:

```yaml
triggers:
  - trigger: event
    event_type: welcomeeye_local.ring_image
conditions:
  - condition: template
    value_template: >-
      {{ trigger.event.data.image_entity_id == 'image.welcomeeye_connect_2_last_ring' }}
actions:
  # Use trigger.event.data.image_entity_id with the chosen notification service.
  # Image/file/URL support depends on that service; no public URL is created here.
  - action: persistent_notification.create
    data:
      title: WelcomeEye
      message: "Image disponible : {{ trigger.event.data.image_entity_id }}"
```

This example announces readiness; it does not attach media to a text SMS.
It will not fire while fallback is suspended and native retrieval is unavailable.
Only the latest image is retained; consumers should use the event sequence to
avoid confusing a newer image with an earlier queued notification.

## CRC32C: demonstrated native correction, upstream rollout still required

Actual installed runtime: HA 2026.9.3, Python 3.14.6, Alpine 3.24.1, QNAP amd64.
Installed google-crc32c 1.8.0 is a `py3-none-any` wheel: `_crc32c` absent,
cext import raises ModuleNotFoundError, selected backend `python`.
`pip debug --verbose` / packaging tags identify musllinux, incompatible with
the published CPython 3.14 manylinux wheels. The HA extra index has the pure
wheel. The exact historical installer download/build log remains unavailable.

The unmodified official sdist builds natively with `crc32c-dev` and
`CRC32C_PURE_PYTHON=0`. Auditwheel bundles the library dependencies into a
musllinux wheel. ELF inspection confirms bundled dependencies; cext import
succeeds in a clean image without system crc32c-dev. Standalone `ldd` on a
Python extension reported Python API symbols as unresolved; actual Python
loading and clean-container tests succeeded, so this was not a loader failure.

| Validation environment | Native µs / 1,200 B | Python µs / 1,200 B | Result |
| --- | ---: | ---: | --- |
| QNAP, isolated clean HA amd64 container | 1.599 | 691.150 | native C + SCTP/audio/video pass |
| GitHub amd64 runner, clean HA musl container | 0.531 | 216.819 | pass |
| GitHub ARM64 runner, clean HA musl container | 0.185 | 209.116 | pass |

Microbenchmark figures are illustrative, not end-to-end video performance.
Each runtime passed five known vectors, incremental checks, 700 concurrent
checks and three actual PeerConnection/DataChannel/bidirectional synthetic
audio/video cycles with full closure and no CRC fallback warning.

[Successful native-wheel CI](https://github.com/Mins95/WelcomeEye/actions/runs/35778484771).
See [upstream draft, reproduction and rollout plan](../tools/crc32c/UPSTREAM.md)
and [patch](../tools/crc32c/home-assistant-wheels.patch).

Production HA's CRC package is **unchanged** and still uses Python. These
research wheels were not injected into custom_components or site-packages.
The proposed durable correction is the official HA wheel builder/index/Core
image path. A same-version native wheel does not automatically replace an
already satisfied pure dependency; upstream rollout must account for this.
No supported transparent HACS-only workaround is established yet.

HAOS supervisor/hardware and physical microphone with the new native backend
were not validated. The equivalent Core musl image was validated on both
architectures. No backend replacement, monkey patch, checksum disablement or
warning suppression was introduced. No upstream issue/PR has been submitted.

## Diagnostics and validation

Photo diagnostics: requests, successes, native/fallback successes, failures,
superseded captures, source, elapsed time, image generation, safe exception
types, native capability and whether automatic fallback is enabled.
CRC diagnostics: backend, version, native availability, import exception type,
wheel tags and actual extension packaging. No addresses, payloads or private paths.

Baseline: 47 tests re-run before changes. New ring-image tests cover native
success, unavailable/invalid/uncorrelated native image, shared live media,
timeout, rapid rings, unload, native timeout, one failed-session limit,
viewer retry-policy preservation and suspended fallback. Actual HA ImageEntity
and Pillow APIs were also exercised in a separate Core Python process.
The upstream wheel-builder patch passes all 41 tests.

Final software validation: 59 integration tests (47 baseline + 12 ring-image
tests), Python 3.12/3.14 compile, Hassfest, HACS and actual HA ImageEntity/Pillow
runtime test passed in [CI](https://github.com/Mins95/WelcomeEye/actions/runs/35781219913).
The repeated [native CRC CI](https://github.com/Mins95/WelcomeEye/actions/runs/35781219732)
also passed on x86_64 and aarch64. These passes do not override failed hardware
acceptance. The zero-context upstream patch passed reverse-apply checking;
the final repository diff passes whitespace checking.

Hardware: initial automatic HA capture succeeded but conflicted with monitor
recording; fallback disabled restored monitor photo. Video and microphone HA
were subsequently confirmed working by the owner. Philips then stayed busy,
and the owner later reported that HA video no longer started. The cause of
this release/reopen failure is not established. The owner's installed component
has therefore been restored to validated `895e7d2`; the candidate remains only
on its feature branch. No claim of complete hardware validation.
After rollback, the failure persisted. Disabling the entire HA integration
left zero media-named threads and zero ring-listener threads, but the owner
still could not use Philips. A physical power cycle then restored Philips.
This establishes that stopping HA connections alone did not clear the observed
device state; it does not establish which preceding exchange caused it.
The restored integration was re-enabled only after the owner closed Philips.
The owner then confirmed moving video and microphone in HA, closed the HA
player, and confirmed that Philips live video worked again. The post-close
probe showed zero media-named threads and one ring-listener thread. Production
is therefore recovered on the validated baseline, not on the candidate.
Two scene-change rings, live-video rings and repeated-ring success remain
unvalidated because native capture coordination failed the first gate.
V1 was not contacted. No physical output was activated.

## Rollback and next evidence needed

The validated pre-mission code remains at `895e7d2`. To undo this candidate,
restore that commit's component files and remove only newly added `image.py`
and `ring_image.py`, then restart HA. No device settings or CRC dependency
replacement need reversing. The saved candidate keeps automatic media fallback
suspended; production has been restored to the pre-mission component instead.

The next protocol test must be passive on the existing local control listener,
with all video players closed: one ring, monitor photo creation, end of the
monitor's call cycle. Record only relative timing, TLV types/lengths and safe
state changes to find a genuine native-capture completion/media-free signal.
Do not infer completion from a fixed timeout or invent a busy status mapping.
If no observable local signal exists, the native-file retrieval or a firmware
capability explanation is needed before enabling automatic fallback safely.
