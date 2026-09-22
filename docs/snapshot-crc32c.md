# 0.4.2 candidate: fresh snapshots and CRC32C audit

Audit date: 2026-09-22. Branch `fix/042-snapshot-crc32c-audit`, based on
`af38fda` (earlier local candidate), public baseline `c933b4c` / 0.4.2-beta.2.
No stable release or main update. No work on V1 doorbell detection.

## CRC32C: demonstrated cause and packaging boundary

Measurements came from the owner's **actual HA Core container interpreter**,
not an add-on or the Windows development interpreter. The probe is a separate
process using that interpreter and installed packages, not an injection into
the running HA event loop.

| Field | Observed |
| --- | --- |
| HA | 2026.9.3 |
| Python | CPython 3.14.6 |
| OS / architecture | Alpine 3.24.1 / x86_64 |
| libc / ABI | musl / cpython-314-x86_64-linux-musl |
| Distribution | google-crc32c 1.8.0, installer `uv` |
| WHEEL metadata | Root-Is-Purelib: true; Tag: py3-none-any |
| Imported module | /usr/local/lib/python3.14/site-packages/google_crc32c/__init__.py |
| Selected implementation | python |
| Native extension | `_crc32c*.so` absent from installed RECORD |
| Explicit `import google_crc32c.cext` | ModuleNotFoundError: No module named 'google_crc32c._crc32c' |
| CRC32C of ASCII `123456789` | 0xe3069283, correct |

The `cext.py` wrapper exists; its existence does **not** establish that the
compiled `_crc32c` extension exists. This is an installed pure-Python wheel,
not an observed wrong-architecture ELF, missing dynamic-library dependency,
or load failure of a present binary. The historical install/build log is not
available, so the exact original download URL/build fallback is not proved.

Official packaging evidence:

- [PyPI 1.8.0 files](https://pypi.org/project/google-crc32c/1.8.0/)
  contain CPython 3.14 **manylinux** x86_64/aarch64 wheels, but no musllinux
  wheels. Intersecting their tags with `packaging.tags.sys_tags()` in the
  actual container found zero compatible wheels. The earlier candidate's
  claim that manylinux covers this HA runtime was wrong.
- The [HA musllinux index](https://wheels.home-assistant.io/musllinux-index/google-crc32c/)
  lists `google_crc32c-1.8.0-py3-none-any.whl`, consistent with the installed
  metadata. The container is configured to use this extra index; a direct
  urllib request from the container returned HTTPError during this audit.
  Index contents were independently checked through the web tool.
- [Upstream setup.py v1.8.0](https://github.com/googleapis/python-crc32c/blob/v1.8.0/setup.py)
  links against `crc32c` and permits a pure-Python build fallback. This
  explains how such a wheel can be produced, not the exact historical build.
- The [HA wheel builder](https://github.com/home-assistant/wheels) supplies
  the musl build infrastructure. A durable fix needs a correctly compiled
  wheel there/upstream (including its native dependency), for each supported
  architecture. A custom integration requirement cannot supply build tools
  or turn a pure wheel into a binary wheel.

Actual aiortc 1.15.0 metadata requires `google-crc32c>=1.1`. The installed HA
package constraints contain no crc32c/aiortc/aioice pin. Both **pip and uv**
dry-run resolution against public PyPI with binary-only, no dependencies,
and reinstall/ignore-installed failed to find a usable 1.8.0 wheel in this
runtime. No package was installed by these probes.

The candidate-only explicit pin is removed; `aiortc==1.15.0` remains unchanged.
The warning remains visible and is **not claimed fixed**. No manual
site-packages replacement, monkey patch, warning suppression or CRC algorithm
substitution is used. New diagnostics distinguish package missing, native
binary missing and native import failure, and check the reference checksum.
Metadata collection runs in HA's executor. Arbitrary exception text, package
URLs and user paths are not exported.

An actual aiortc peer-to-peer DataChannel loopback succeeded inside the Core
container (no STUN server or device connection). This verifies SCTP/CRC data
transport in that runtime; it is not a physical microphone or remote ICE test.
No native-wheel installation was attempted, hence no successful native-import
or warning-free-restart validation can be claimed. Aarch64 was checked in
the published wheel inventory, not on physical hardware.

## Fresh still images on the one media worker

The old public `async_camera_image()` returned `hub.image` immediately. That
cache survives media teardown and can describe a previous scene.

The candidate records `image_generation` at request entry, acquires a unique
lease through the real hub, and waits for a strictly greater generation.
Every decoded JPEG dispatch increments the counter even if its bytes are
identical. Idle captures temporarily start the normal worker; an existing
HLS/WebRTC/microphone consumer retains its worker and lease. Concurrent
snapshots share it. There is no new Session or physical-output call here.

Acquisition and image waiting share a 15-second deadline. A timeout returns
no image, never the previous cache. Normal device teardown/join can add time
after the capture deadline. The lease is released on success, error, timeout
and cancellation. Cleanup is drained even when cancellation occurs during
release or is repeated. Disconnect and integration unload wake image waiters;
unload also wakes pending acquisitions. A failed worker join remains visible
as an error, and the hub's existing stopping-worker guard prevents a second
worker from being started over it.

Snapshot counters report requests, successes, timeouts, errors, actual worker
reuse/start, elapsed wait and the latest error type. No JPEG, endpoint or
credentials are included. A success counter means a new JPEG was obtained;
a later release failure is separately recorded as an error.

HA's camera API is also used for thumbnails: refreshing a thumbnail may
acquire media. This API cannot identify a user-initiated service call merely
from `async_camera_image(width, height)`. The change supplies fresh frames to
that API; it does not add a new snapshot button or an automatic archive.
`camera.snapshot` writes to the filename requested by the user, subject to HA's
existing allowed-directory rules. No ring-triggered snapshot is configured.

## Native visitor capture: proven facts and remaining gaps

The [Connect 2 manual V4](https://pim.avidsen.com/documentsproduits/fr/531036-notice.pdf)
(printed F11, F14-F15) confirms photo/video/off visitor-memory modes on the
monitor, configurable 10/20/30-second video clips, and internal/microSD storage.
It explicitly says visitor-memory capture cannot be consulted from the app.
The photo-count values conflict between F11 (1/2/3) and F14 (1/3/5), so no
numeric firmware mapping is inferred. The [original Connect manual](https://pim.avidsen.com/documentsproduits/fr/531002-notice.pdf)
also documents photo/video/off and requires microSD for video. No setting was
changed. These manuals do not specify the ownership or release timing of the
network media session during native recording.

The owner inspected the actual Connect 2 monitor during this audit and
confirmed **Photo** mode. No setting was changed.
With both HA and Philips players closed, the owner then rang once, verified
that a new photo was recorded on the monitor, and confirmed access to Philips
live video afterward. This proves native photo capture and subsequent app
access in this test, not the exact release latency or concurrent access during
capture. No automatic HA snapshot was triggered and no native image was fetched
over the network.

The existing APK extraction is `work/decompiled/sources`; inspected ARM32
library is `work/talkback_native/liblive_player.so`, SHA256
`9ce7549858e01dd73d858cb956a2e082fbe828675a830730bd932692838386fe`.

| Evidence | Meaning / limit |
| --- | --- |
| `com/quvii/compathlt/QvLtPlayerCore.java:1091-1098` | `snapShot` calls the active renderer's `snapShotCompat`. This is a live-player screenshot, not recovery of the monitor's visitor-memory file. |
| Same class `:1106-1119` | Manual app video recording uses `startRecordVideoCompat` and a phone file path. It does not establish native call-recording control. |
| `com/quvii/compathlt/device/TCRequestBean.java` | Contains bitrate/query/I-frame/output requests; no native photo/clip setting is established here. Output requests were not used. |
| Generic `qvweb/device/entity/QvDeviceRecordConfigInfo.java`, `bean/requset/SetRecordConfigContent.java` | Other SDK families expose record configuration. Support and mapping on LT WelcomeEye are unproved. No command sent. |
| `QVCGIConfigPreviewSnapshot`, address `0x308c42` | Wrapper calls `IQVCGIConfig::PreviewSnapshot`, address `0x2fd870`, size 1152. |
| `PreviewSnapshot` | Builds `set.facepic.snap`, content `<content><chn>%d</chn></content>` with argument + 1, sends POST with 3000 ms parameter. Parses XML `error=0`, `info` attributes `sex`, `wh`; optional `face` Base64 data and `fea` string. This looks like face capture in a generic SDK; JPEG format and WelcomeEye support are not established. |
| `IQVCGIConfig::SendCmd`, address `0x2fb674`, size 256 | Creates a temporary `CQVHTTPSocket`, optionally SSL, calls `Commit`, requires HTTP 200, destroys the HTTP socket. No Start AV call in this inspected path. |

The temporary HTTP path does **not** prove that firmware leaves settings or
its single media resource untouched. It does not prove an independently
available fresh image endpoint on Connect 2. **`set.facepic.snap` was not sent.**

Current strategy: manual HA capture uses the shared worker. Automatic call
capture stays disabled in this work. A local native photo-fetch API remains
unproved; native clip download/frame extraction remains unproved. Turning
off native visitor memory is a documented monitor option, but there is no
proven remote setting command and no automatic change. It can also affect
answering-machine behavior, so it is not a default workaround.

## Validation and rollback

Production files changed from public beta.2:
`custom_components/welcomeeye_local/camera.py`, `hub.py`, `snapshot.py`,
`diagnostics.py`, `crc32c_diagnostics.py`. The final manifest requirements and
version match beta.2; the speculative CRC pin existed only in the older local
candidate. Supporting changes: the three relevant test files,
`.github/workflows/validate.yml` (new snapshot/CRC tests inherited from that
candidate), README, changelog and this technical note/docs index.

- 13 tests execute the actual hub class lease/event/stop/join methods, with
  only imports/device worker replaced. Covers old cache, idle/live owners,
  two concurrent idle snapshots, identical JPEG generations, both deadlines,
  cancellation during acquisition/wait/release, repeated cancellation,
  media failure, unload during acquisition/wait, repeated requests.
- Six CRC packaging/diagnostic tests, 11 resource-registration regression
  tests: **30 tests passed in HA Core Python 3.14.6**. The first remote bundle
  omitted manifest.json and failed one fixture read; corrected bundle passed.
- Real CRC reference and DataChannel loopback passed in that interpreter.
- Connect 2: candidate's five Python files installed on the owner's HA after
  matching installed source hashes against the public baseline; Core restarted.
  Two real `camera.snapshot` calls saved valid 720x576 JPEGs under `/media`.
  The user changed the scene with video closed; the second image visibly
  shows the obstruction, unlike the first, and has a later camera timestamp.
- Connect 2: third JPEG saved during the owner's open live video. The owner
  confirmed the video kept playing and microphone audio remained audible.
  Philips initially reported busy after closing; a read-only Core process
  check subsequently found zero `welcomeeye-media` threads (only ring
  listening), and the owner confirmed Philips worked again. Release was not
  immediate; its exact duration and cause were not measured. An HA standard
  camera preview was briefly opened/closed by the investigator during this
  interval, so HLS retention is a hypothesis, not a demonstrated snapshot leak.
- The existing 17-test doorbell regression suite initially needed its
  manually constructed hub fixture to include the two shutdown events.
  Only fixture initialization changed; no doorbell behavior was modified.
  All 17 tests then passed under local Python 3.12.
- V1 physical validation is pending. No connection to the tester's HA.

No gate/strike commands were invoked. Protocol, encryption, media profile,
video decoding, talkback and output-command files are unchanged from baseline.

Rollback: restore the installed beta.2 `camera.py`, `hub.py`, `diagnostics.py`
from `c933b4c` (or reinstall the official 0.4.2-beta.2 package), remove the
candidate-only `snapshot.py` and `crc32c_diagnostics.py`, then restart Core.
No config-entry migration, credentials change or firmware setting needs undoing.
Test JPEGs are ordinary files in `/media`, not public `/local` URLs.

Remaining release gates: V1 physical snapshot/lease/microphone tests; precisely
measure the transient busy interval with no standard HLS preview open; native
photo/clip network resource ownership and retrieval. HACS/Hassfest hosted jobs
have not been run for this local branch. Do not promote to stable on the basis
of the automated tests alone.
