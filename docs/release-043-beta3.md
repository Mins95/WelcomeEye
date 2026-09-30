# 0.4.3-beta.3 candidate — bounded short-response observation

Prepared on `feature/043-r002-investigation`, from beta.2 commit
`3784c68fd0e517393cb24ec221a8d67bc6126a59`. This task prepares the code, package and
exact-commit CI; it does **not** create a beta.3 tag or GitHub release. No production
installation, real-device request, main/stable update or issue comment is made.

## APK findings included with this candidate

The [September 30 native audit](r002-apk-analysis.md#follow-up-2026-09-30--qv-discovery-and-media)
adds a concrete alternative discovery path found in the official app:

- the configured QV P2Pv2 manager sends the 18-byte ASCII request
  `ASZENO.SEARCH.V4.1` to broadcast UDP 5000 and requests local receive ports
  5001/5003;
- its encrypted discovery response contains explicitly named CGI, media and
  TLS media port fields, traced through JNI into Java device metadata;
- the generic local preview path retrieves `get.device.streamkey` through
  `/tdkcgi`, then passes the key to the native QV player;
- selected QV media functions construct/read 32-byte headers, separate from
  the unresolved TCP 8765 framing.

These findings include source locations, native addresses and binary hashes.
They do **not** demonstrate that the tester's R002 selects QV, that its credentials
work with this route, or that TCP 8765 carries QV media. The new discovery path
is documented only: beta.3 sends no UDP 5000 probe, CGI, login or media request.
The next tester action remains the single four-type TCP observation below.
No APK, proprietary library or decompiled source is included in the ZIP.

## Hardware evidence and unresolved questions

Tinymop21's [2026-09-30 report](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-5914474400)
provides these **actual 12-byte prefixes only**:

| Requested type | Reported prefix |
| --- | --- |
| 14 | `0e00 0100 0000 0600 0000 0e00` |
| 15 | `0f00 0100 0000 0e00 0000 0f00` |
| 26 | `1a00 0100 0000 1400 0000 0000` |
| 28 | `1c00 0100 0000 0000 0000 0000` |

Only `nonzero_field_8_11` rejects 14/15. Offsets 10–11 contain the requested type;
the block cannot be required to be zero for these real replies. That does **not**
establish whether the header is 10 or 12 bytes. The role of the service and the
fields remains unknown. No continuation of these hardware prefixes is invented
in tests or examples. Complete responses used below/in tests are synthetic.

The same report confirms the certificate metadata fix: parsed CN/issuer eziotest,
non-positive serial, no deprecation warning, trust_authenticated=false. Its code
and tests remain unchanged. No further certificate test is requested here.

## Observation mode

`include_response: true` explicitly selects observation instead of the normal
parser read path. Normal mode is unchanged and exports no raw bytes by default;
the beta.2 `include_header` opt-in remains available. Observation already includes
the prefix details, so both options together do not run two read paths.

For each of the allowed types 14/15/26/28, the same single eight-byte request
(`u16LE(type) + six zeros`) is sent once to TCP 8765 on one connection. One reader
loops over `read(remaining_capacity)` until server EOF, the absolute **3.0-second
network deadline**, or **64 bytes**. No header/body length controls collection.
No retry, peek for byte 65, extra connection, idle timer or background work.
Connection and sending time consume that same deadline; it is not restarted
for each fragment. The existing bounded close adds at most one second, included
in elapsed_ms. Four sequential requests therefore have a bounded total duration.

The bytes returned by each read are retained immediately. If the connection stays
open, reaching the deadline returns the collected bytes. An OS socket timeout
is distinguished from expiry of our asyncio deadline. Client close is never
reported as server EOF. A deadline/cap is **not a demonstrated message boundary**.

## Response fields

The result remains nested under the target sensor, then `protocol`/`results` and
the requested type. Each observation has:

- `requested_type`, `response_hex`, `bytes_collected` (0–64), `elapsed_ms`;
- `collection_end_reason`: `remote_eof`, `deadline`, `size_limit`, `network_error`;
- `observation_status`: `collected` or `empty` after collection, `not_started`
  if the deadline expires during connect/send, `network_error` for a socket error;
- `status`: `observed` after normal collection (including a collection deadline),
  otherwise `failed`; `last_stage`, `last_error_type` distinguish early failures.
  A collection deadline has no last_error_type. It is not a malformed-response verdict;
- `raw_header_hex`, `prefix_bytes_received`, `decoded_candidate`, `header_valid`,
  `header_validation_errors`, `beta2_error_type`: the **unchanged beta.2** validation
  applied afterwards to the first at most 12 bytes of that same buffer. This
  comparison is not the observation's network status;
- `header_10_candidate`, `header_12_candidate`: provisional layouts described below;
- `framing_assessment` and `framing_confirmed: false`.

Each candidate reads declared_length from offsets 6–7 if those bytes exist, then
reports expected_total = header_size + declared_length, body_bytes_available
(all collected bytes beyond that candidate header), missing_bytes to reach the
expected total, extra_bytes and extra_hex beyond it. body_candidate_hex is only
filled when the whole declared candidate body is available, and contains exactly
that many bytes; missing bytes are never padded. Unknown lengths/counts are null.
has_enough_bytes expresses byte availability only, not protocol validation.

If both layouts have enough bytes, framing_assessment is **ambiguous**, including
when one leaves two extra bytes. If only the 10-byte layout has enough bytes,
the label is `header_10_has_enough_bytes`, not “validated”; a 12-byte response could
still be incomplete. If neither has enough bytes, it is `insufficient_data`.
A repeated requested type in a candidate body does not validate that layout.
No field is labelled configuration, acknowledgement, padding or command accepted.

Synthetic example: an 18-byte buffer constructed as header-12 + ASCII `abcdef`,
with the mock server closing. This is **not** the hardware's missing body:

```json
{
  "requested_type": 14,
  "status": "observed",
  "observation_status": "collected",
  "response_hex": "0e0001000000060000000000616263646566",
  "bytes_collected": 18,
  "collection_end_reason": "remote_eof",
  "elapsed_ms": 1,
  "header_10_candidate": {
    "header_size": 10,
    "declared_length": 6,
    "expected_total": 16,
    "body_bytes_available": 8,
    "body_candidate_hex": "000061626364",
    "missing_bytes": 0,
    "extra_bytes": 2,
    "extra_hex": "6566",
    "has_enough_bytes": true
  },
  "header_12_candidate": {
    "header_size": 12,
    "declared_length": 6,
    "expected_total": 18,
    "body_bytes_available": 6,
    "body_candidate_hex": "616263646566",
    "missing_bytes": 0,
    "extra_bytes": 0,
    "extra_hex": "",
    "has_enough_bytes": true
  },
  "framing_assessment": "ambiguous",
  "framing_confirmed": false
}
```

The full response also includes the beta.2 comparison fields listed above.

## Permissions and privacy

Either raw option requires an identified HA administrator and the existing entity
control permission. Denial happens before networking. The normal default has
neither option enabled. A probe is never run automatically at setup or reload.
Only one investigation may run per entry, including the existing certificate check.
Unload cancels/drains it, closes the original socket and suppresses late callbacks.

The `_per_type` summary has an explicit allowlist for sizes, counters, statuses,
rejection reasons and durations. It excludes response_hex, prefixes, candidate
objects, raw bodies and decoded ambiguous numbers. Standard diagnostics, sensor
attributes, config/options and DEBUG logs never receive them. A completed
observation increments `observations`, not the parser `successes` counter; sensor
state `probe_observed` only means observation ended, not a command was accepted.
An observation deadline has its own counter and is not a malformed-frame count.

**HA may retain explicit action responses in script traces.** Raw replies may
contain unknown identifying data; read them before public sharing. No promise of
zero persistence outside the integration. No certificate or media bytes are collected.

## Next test — one campaign only, after installation of the candidate

Keep the existing entry, close HA/Philips video, leave the device idle. As admin,
in **Developer tools → Actions → YAML**, replace the sensor ID and execute once:

```yaml
action: welcomeeye_local.r002_probe
target:
  entity_id: sensor.YOUR_R002_SENSOR
data:
  types: [14, 15, 26, 28]
  include_response: true
```

Copy the displayed response for all four types, including collection end reasons,
response_hex, both candidates and beta.2 comparison. Do not rerun to get a second
interpretation: both are already computed from the same bytes. No other types,
ring, physical outputs, media, CGI, scan or repeat certificate check is requested.
If using a script **instead of** Developer Tools, add `response_variable: r002_result`
to the action and read it from that execution's trace; do not execute both routes.
Review the response for identifying data before sharing publicly.

## Validation, changed files and rollback

Baseline: 223 Python tests. Candidate: 240, with one Windows-only skip; 24 frontend
tests. New tests cover the exact four hardware prefixes, synthetic complete 10/12
layouts, fragmentation, truncated/extra bytes, EOF/open server, cap, absolute
deadline/retained fragments, OS errors, single requests, unload/concurrency and
privacy. Actual HA 2026.7.3/2026.9.3 service tests add admin-only response access
and download-diagnostics/sensor/options checks after a full observation. Existing
R001/V1 capability, output single-shot and certificate regressions remain included.
CI includes Python 3.12/3.14, frontend, HACS, Hassfest and reproducible packaging;
use the final branch commit's Validate run as the authoritative result.

Functional files: r002/observation.py (pure buffer analysis), r002/transport.py
(bounded opt-in read loop), r002/hub.py (safe summary/status), services.py,
services.yaml and sensor.py (option/permissions). Version, translations, docs and
tests/runtime verifier accompany them. No certificate/parser, media, output,
doorbell or CRC code is changed. The existing workflow runs the expanded tests.

Rollback: redownload beta.2 and restart HA without deleting the entry. Remove
`include_response` from any saved script; beta.2 does not support that option.
The working certificate fix is retained. No firmware or device configuration was
modified by candidate preparation. Protocol framing remains unresolved.
