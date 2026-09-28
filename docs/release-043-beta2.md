# 0.4.3-beta.2 — R002 rejection diagnostics and certificate metadata

Branch: `feature/043-r002-investigation`. Base: beta.1
`60ba539f61e52c1141ad95acbadccbfb234a9ae8`. The published tag, release notes and
checksum asset identify the final commit/archive. Stable 0.4.2, main, beta.1 and
the CRC32C dependency are not replaced. No device has been contacted by this
development/CI work. Hardware confirmation follows publication.

## Evidence and scope

Issue #7 comments [5867661024](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-5867661024)
and [5867677770](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-5867677770)
report successful beta.1 setup on HA 2026.7.3 Container / Python 3.14.6 amd64,
DES9901VDP `V401.R002.A302.00.G0058.B002`. Types 14/15 failed at response_header;
26 returned 20 zero bytes; 28 returned an empty body. **No raw 14/15 prefix was
provided.** Earlier interpreted body lengths are reports, not captured fixtures.

Beta.2 does not announce “14/15 parsing fixed.” It separates the one existing
read, the provisional interpretation, and strict validation. `<HHHHI` is still
the beta.1 hypothesis; offsets 8–11 are called `field_8_11_u32`, not confirmed
padding. A nonzero value still rejects the response. All detected validation
errors are returned, preserving the first error's existing exception class.

Unchanged: allowed types 14/15/26/28, eight-byte request `u16LE(type) + six zero
bytes`, TCP 8765, one request per connection, max four distinct types, one active
investigation per entry, no retries, three-second request deadline plus bounded
close, 4096-byte body limit. No body read follows a rejected prefix. No media,
login, output, ring, CGI or speculative framing changes.

## One probe, as an administrator

Install **0.4.3-beta.2** using HACS prereleases, restart HA and keep the existing
R002 entry. Updating/restarting does not automatically re-run its setup fingerprint.
Use the actual protocol-status sensor in **Developer tools → Actions → YAML**:

```yaml
action: welcomeeye_local.r002_probe
target:
  entity_id: sensor.SON_CAPTEUR_R002
data:
  types: [14, 15, 26, 28]
  include_header: true
```

Execute **once** and copy the displayed action response (results are nested under
the target entity ID). Save each type's status, last_stage, last_error_type,
raw_header_hex, prefix_bytes_received, decoded_candidate,
header_validation_errors and elapsed_ms. Do not test another type, ring or output.

Alternatively, choose this script route **instead of** the developer-tools probe:

```yaml
sequence:
  - action: welcomeeye_local.r002_probe
    target:
      entity_id: sensor.SON_CAPTEUR_R002
    data:
      types: [14, 15, 26, 28]
      include_header: true
    response_variable: r002_result
  - stop: Return the single probe response
    response_variable: r002_result
```

Run it manually under an administrator account; inspect the returned response or
the action's changed variables in the script trace. If the caller has no identified
administrator context, detailed access is refused **before any probe**. Ordinary
entity-control permission remains required; do not bypass permissions. Do not
run both routes just to retrieve the same result.

`include_header` defaults to false. Raw mode returns at most the 12 prefix bytes
already delivered by the existing read, never a body or certificate. The name
`raw_header_hex` is convenient but **does not prove the header boundary**. EOF
returns only actual partial bytes, without padding, and IncompleteReadError.
A timeout before readexactly delivers a prefix returns zero delivered bytes;
private StreamReader buffers are not inspected or drained for extra data.

**Privacy:** review the prefix and decoded numbers before sharing publicly: an
unknown field may contain identifying data. The integration's persistent
`_per_type`, diagnostics, sensor attributes and DEBUG logs retain only allowlisted
status/reason/size/timing/counter fields, not raw bytes or decoded_candidate.
No detailed result is written to config/options or repository files. HA itself
can retain an explicit service response in script traces; this is not a promise
of zero persistence outside the integration.

## Separate certificate recheck

On the same existing entry, explicitly run this different action once:

```yaml
action: welcomeeye_local.r002_check_certificate
target:
  entity_id: sensor.SON_CAPTEUR_R002
data: {}
```

It performs a single bounded TCP/TLS connection to 443, no application bytes,
UDP discovery or 8765 probe. It shares the investigation concurrency/unload guard.
No entry deletion, duplicate, identity change or periodic check is required.
The safe result is also available in newly downloaded diagnostics until reload.
The original stored setup classification is preserved; this is a metadata recheck,
not a device identity migration or proof of trust.

Expected **if the reported hardware certificate matches the tested structures**:
tls_443_handshake_ok=true, certificate_metadata_status=parsed,
certificate_serial_status=non_positive, certificate_parser=bounded_der_metadata_v1,
subject/issuer labels eziotest, certificate_trust_authenticated=false.
Please return the actual result, even if it differs. No exact hardware certificate
fixture has been supplied or tested here.

## Certificate implementation and limits

The bounded DER reader independently walks Certificate/TBSCertificate, serial,
matching signature algorithm identifiers, both Names, validity structure, SPKI
and optional fields/extensions. It reads the actual CN attribute OID 2.5.4.3,
not an arbitrary occurrence of `eziotest`. It accepts a single matching CN;
duplicate/other CNs do not identify the device. Only `eziotest`/`other` labels and
serial sign leave the parser. Neither the complete serial nor DER is returned.

Limits: 64 KiB, depth 12, 1024 nodes, bounded OID/CN/serial values; definite minimal
DER lengths, complete container bounds, integer/OID/bit-string validity and
certificate field structure. Malformed/truncated/oversized/unsupported metadata
produces a sanitized CertificateMetadataError and no recognized fingerprint.
Keys, signatures and extension contents are not cryptographically validated;
this is intentionally **not an X.509 trust validator**. No serial rewrite,
warning suppression, global SSL change, new dependency or cryptography pin.
The existing isolated fingerprint TLS context remains self-signed/unverified;
TLS handshake, metadata parsing, serial conformity and trust are distinct fields.

`tools/verify_r002_certificate_versions.py` compares cryptography 42.0.8, 46.0.7
and 50.0.1 in isolated CI environments, and the versions installed in HA test
images. It records whether the X.509 loader warns or rejects; the new metadata
reader is additionally tested while that loader is forced to raise ValueError.
This avoids depending on a [deprecated tolerance](https://github.com/pyca/cryptography/blob/main/CHANGELOG.rst).
Synthetic structures include positive, zero, negative, unrelated CN and decoy
text, malformed/truncated/oversized cases. They are not hardware evidence.

## Synthetic response example — NOT captured from a device

```json
{
  "status": "failed",
  "last_stage": "response_header",
  "last_error_type": "R002ProtocolError",
  "raw_header_hex": "0e0001000000060078563412",
  "prefix_bytes_received": 12,
  "decoded_candidate": {
    "response_type": 14,
    "version": 1,
    "flags": 0,
    "declared_length": 6,
    "field_8_11_u32": 305419896
  },
  "header_validation_errors": ["nonzero_field_8_11"],
  "header_valid": false,
  "elapsed_ms": 1
}
```

The persisted summary of this example omits raw_header_hex and decoded_candidate.
Regression tests check their absence, the marker integer/hex absence, sensor
attributes, diagnostics, options, internal history and DEBUG logs. Actual HA
service tests verify admin-only detail and ordinary entity permission checks.

## Validation and rollback

Baseline: 206 Python tests. Candidate suite includes prefix/EOF/fragmentation,
strict rejection/no-body-read, privacy, certificate structure and lifecycle tests.
CI runs all tests on Python 3.12/3.14, 24 frontend tests, compile, reproducible
packaging, HACS, Hassfest, actual HA 2026.7.3 and 2026.9.3 services/permissions with
mocked device networking, plus the certificate-version matrix. The release is
published only after Validate succeeds for its exact commit; consult that run
for final counts/results. R001/V1 entity matrix and single-shot output tests
remain in the full suite. The physical send path and CRC code are unchanged.

Rollback to beta.1: in HACS choose **0.4.3-beta.1**, redownload, restart HA. Keep
the R002 entry and provisional identity. Remove beta.2-only fields/actions from
any saved probe script. Beta.1 retains the old strict rejection and certificate
warning. No device setting or production installation was modified by this task.

## Message prepared for tinymop21 (not posted)

Hi @tinymop21, [beta.2](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.3-beta.2)
adds rejection diagnostics and a fix for non-positive certificate serials.
After updating and restarting HA, please run the probe **once**, as admin:

```yaml
action: welcomeeye_local.r002_probe
target:
  entity_id: sensor.YOUR_R002_SENSOR
data:
  types: [14, 15, 26, 28]
  include_header: true
```

Please send the action response, including each type's status, error/stage,
raw_header_hex, prefix_bytes_received, decoded_candidate,
header_validation_errors and elapsed_ms. **Review the bytes before public
sharing; unknown fields may contain identifying data.** Types 14/15 may still
fail; this beta explains the rejection without guessing the framing.
Then separately run `welcomeeye_local.r002_check_certificate` on the same sensor
with empty data and send its response. No other type, doorbell or output test
is needed. You can keep your existing entry. Thank you!
