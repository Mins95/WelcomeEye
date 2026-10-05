# Explicit legacy SDK experiments (beta.7)

These actions are off by default and require an identified administrator with
control permission on the targeted legacy camera, plus `confirm: true`. They
are not run at setup, after media failure, or during ordinary camera use.
R002 and Connect 3 targets are rejected by the legacy backend.

An active media consumer, snapshot task, control action or ring listener returns
`busy` without sending anything. The listener is never automatically paused or
reconnected. A diagnostic holds the existing media and control coordination
locks until its socket is closed. Each path allows one attempted experiment per
loaded entry; an error does not cause a retry.

## Firmware query: 509 / 469 / 510 / 470

`welcomeeye_local.experimental_version_info` targets a legacy camera and takes
only the confirmation flag. It uses a separate temporary **control** session
only when the normal media and control workers are inactive.

Static evidence in WelcomeEye APK 6.1.58.24:

- `com.quvii.compathlt.device.IProtocolResponse`: firmware request 469, reply 470.
- `LtDeviceManager`: `addTask(469, new byte[4], false, callback)`.
- `ProtocolTaskHelper`: `sendData(executeType, data)` and reply type = request + 1.
- `LtResponseBean._TLV_V_VERSIONSTR`: `AppCom`, `SolCom`, `ReleaseTime`, `HDVersion`.
- ARM32 `libglnkio.so`, `DataChannel::sendData2` at `0x8902c`: first creates the
  inner OWSP packet, then invokes `packetPriProReqData` at `0x8911e` with type 509.

The query is an inner sequence-zero OWSP containing TLV 469 and four reserved
zero bytes, enclosed by the existing authenticated private-509 builder. Only
a decoded private-510 response containing the exact SDK pair 470 is correlated.
Alarm TLV 14854 and messages seen before the query do not satisfy the query.
There is no invented request ID or presumed sequence echo: the temporary session
has one outstanding query. It does not modify firmware or device settings.

The explicit response contains only allowlisted version metadata, counts,
stages and durations. Neither raw private data nor decoded metadata is copied
to ordinary integration diagnostics, entity attributes, logs or config options.
Home Assistant can retain an explicitly requested service response in a script
trace; review metadata before sharing it publicly.

## Additional-camera candidate

`welcomeeye_local.experimental_additional_camera` takes the same target and
confirmation flag. One login uses channel 18, stream 1, mode 2; one Start AV uses
selector `0x12`. This does **not** alter the normal V1 profile 16/1/2.

The APK's `QvLtPlayerCore.setMetaData` maps live channel to channel + 15; the
additional-camera candidate comes from SDK camera ID 3. This remains a candidate,
not a confirmed external-camera mapping on a physical unit.

The response distinguishes login acceptance, Start AV response, media packet
count, decoded frame count and dimensions. At most three H264 frames are decoded
in memory, using the existing H264 normalization / V1 complete-image parser.
No JPEG is created, persisted, returned or published as an entity. Audio,
microphone, outputs and subscriptions are excluded by the packet allowlist.

Cleanup attempts the existing protected Stop AV 5009 once, records 5010 if it
arrives while the reader remains usable, sends native session stop 5005 once,
and closes TCP on every path. Missing acknowledgment is not claimed as physical
device release. `source_mapping_status` remains `not_validated` even if frames
decode, until a specifically authorized physical comparison establishes their
source. No real-device experiment was performed for this release.

## Fixed budgets

One UDP 1500 discovery request (3 s), one TCP connect (3 s), one login (6 s),
observation (4 s), cleanup (2 s), global deadline (18 s). The first 16 s are
reserved for discovery/login/observation; the last 2 s are reserved for cleanup.
Every send uses a timeout of at most 0.5 s within its remaining deadline. No discovery
cache is read or modified. There is no connection-refusal recovery in this
experimental session. Receive bytes are limited to 2 MiB, complete OWSP size to
1 MiB, frame reads to 64 and decoded images to three. The H264 decoder has a
4096² pixel limit before decoding/allocation. Existing keepalive 49 is
allowed at most twice. A partially written request consumes its single attempt.
Cancellation/unload wakes the reader and waits for cleanup before releasing
coordination locks. A canceled observation cannot start a login or Start AV.

## UDT / punch port: offline only

The SDK contains UDT code, but absence of UDP1500 discovery does not establish
a punch endpoint or authorize an invented request. This release has no active
UDT network probe. The isolated tool accepts an already available ELF file:

```powershell
python tools/inspect_experimental_udt.py PATH_TO_LIBGLNKIO.so --confirm --protocol-family legacy_owsp --legacy-discovery-absent
```

It validates bounded ELF section/symbol tables and reports only three fixed
defined SDK symbols. Arbitrary strings, filenames, identities and raw ELF bytes
are not returned. It accepts legacy/R002 context and explicitly reported absence
of legacy discovery; Connect 3 context is rejected. Output remains
`status: not_validated`, `active_probe_available: false`, `requests_sent: 0`.

The original APK library SHA256 is
`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.
The tool observed defined `UdtConnection` constructor, `getFwdUdtInfo` and
`getLocalPort` symbols in that library. This is static SDK evidence only.

## Software validation

`tests/test_experimental_diagnostics.py` uses explicitly synthetic responses and
ELF fixtures. It checks opt-in and permissions, busy rejection without listener
pause/resume, protocol allowlists, one UDP/TCP/login, no retry after partial send,
470 correlation, privacy, timeout/EOF/budgets, concurrent callers, cancellation,
unload, bounded real H264 encoding/decoding and native cleanup. Synthetic frames
do not validate camera source mapping or a physical intercom.
