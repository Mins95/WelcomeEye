# 0.4.3-beta.1 release audit

Branch: `feature/043-r002-investigation`, based on current main `35d55c9cb2ec333e143610f4718d395a18a24047`. The final commit and archive checksum are recorded in the immutable prerelease assets/notes. No merge into main, stable promotion or replacement of an existing tag is authorized by this release.

## Scope

Central conservative capabilities, exact registry cleanup and isolated R002 fingerprint/probe/diagnostics. The [matrix and tester procedure](r002-investigation.md) and [APK evidence](r002-apk-analysis.md) describe the implementation and its limits. R002 hardware support is **not validated**; no real intercom or relay was accessed in this task.

Baseline: 164 offline Python tests (one Windows-only platform skip), 21 frontend tests. Candidate: **206 Python tests**, **24 frontend tests**; the same platform skip applies on Windows. The added tests cover capability/entity matrices, registry selection, deferred classification reload, config flow, synthetic fragmented framing, timeout/EOF/cancellation, probe restrictions, privacy and V1 send-once behavior. Linux CI runs Python 3.12 and 3.14, compilation, package validation, Hassfest, HACS and frontend tests.

Actual HA 2026.9.3 isolated containers additionally exercise ImageEntity/JPEG handling, capture switch/service/permissions/Media storage, and R002 setup, real registry migrations, response service and denied permissions, confirmation form and unload/re-register. Device networking is mocked. Early validation attempts exposed missing registry initialization in the standalone test harness; both HA registries are now initialized explicitly. Publication requires a successful Validate run on the **exact final branch commit**, including this harness. This is not a claim of physical video/audio validation on the candidate.

## Legacy invariants

Compared with the base commit, `protected.py`, `protocol.py`, `v1_control.py`, `media.py`, `rtc.py`, `lifecycle.py`, `talkback.py`, `ring.py`, `snapshot.py` and `manual_snapshot.py` are unchanged. `control.py` changes only the V1 selection to the central enum. `client.py` adds a discovery-specific exception and classifies OS-delivered UDP refusal/reset as discovery absence; TCP/auth and packet builders are unchanged.

The V1 send path claims a queued request under its lock, marks it sent, removes packet bytes from the pending object and increments the attempt count **before** the socket call. Repeated worker iterations, a failed write or a different session cannot send it again. Three new synthetic tests verify successful repeated calls, timeout/BrokenPipe/reset followed by reconnect, and cancellation before claim. No test builds or transmits a physical command to hardware.

The aiortc requirement is byte-for-byte unchanged from stable: `aiortc-1.15.0+welcomeeye.crc1`, wheel SHA256 `75f7d14e598dfd2b3e97bd4b9342e9b675185b6a3d83d5a5083c43250b6eee9d`. No CRC monkey patch, warning suppression, embedded binary or new pin.

## Publication and rollback

The existing stable release workflow is intentionally unchanged and remains restricted to main / 0.4.2. This beta is published explicitly from the feature branch after successful exact-commit CI: new tag `v0.4.3-beta.1`, prerelease true, latest false, verified `welcomeeye_local.zip` and SHA256 file. Any subsequent published defect requires beta.2, never retagging beta.1.

Stable preservation reference: main `35d55c9cb2ec333e143610f4718d395a18a24047`; v0.4.2 `b7ae868b7e174951926ffdfa056b5f218e9c1a26`; stable ZIP SHA256 `f763d17884527753b471e892e037be5a7b9a79a9a55be44d4b0b22fd2c11e51e`. The stable release's CRC wheel and ZIP are not replaced.

Rollback: remove R002 investigation entries before installing stable 0.4.2, then restart HA. Legacy config entries are backward compatible; unsupported registry entities removed by this beta may be recreated by the older stable code. Capture files are not removed and no device setting is changed.

## Files changed

- `.github/workflows/validate.yml`
- `CHANGELOG.md`
- `README.md`
- `custom_components/welcomeeye_local/__init__.py`
- `custom_components/welcomeeye_local/binary_sensor.py`
- `custom_components/welcomeeye_local/button.py`
- `custom_components/welcomeeye_local/camera.py`
- `custom_components/welcomeeye_local/capabilities.py`
- `custom_components/welcomeeye_local/client.py`
- `custom_components/welcomeeye_local/config_flow.py`
- `custom_components/welcomeeye_local/const.py`
- `custom_components/welcomeeye_local/control.py`
- `custom_components/welcomeeye_local/diagnostics.py`
- `custom_components/welcomeeye_local/frontend/welcomeeye-card.js`
- `custom_components/welcomeeye_local/hub.py`
- `custom_components/welcomeeye_local/image.py`
- `custom_components/welcomeeye_local/manifest.json`
- `custom_components/welcomeeye_local/player.py`
- `custom_components/welcomeeye_local/r002/__init__.py`
- `custom_components/welcomeeye_local/r002/fingerprint.py`
- `custom_components/welcomeeye_local/r002/hub.py`
- `custom_components/welcomeeye_local/r002/protocol.py`
- `custom_components/welcomeeye_local/r002/transport.py`
- `custom_components/welcomeeye_local/ring_image.py`
- `custom_components/welcomeeye_local/sensor.py`
- `custom_components/welcomeeye_local/services.py`
- `custom_components/welcomeeye_local/services.yaml`
- `custom_components/welcomeeye_local/strings.json`
- `custom_components/welcomeeye_local/switch.py`
- `custom_components/welcomeeye_local/translations/en.json`
- `custom_components/welcomeeye_local/translations/fr.json`
- `docs/r002-apk-analysis.md`
- `docs/r002-investigation.md`
- `docs/release-043-beta1.md`
- `tests/frontend/welcomeeye-card.test.cjs`
- `tests/load_integration.py`
- `tests/test_capabilities.py`
- `tests/test_capture_entities.py`
- `tests/test_doorbell_trial.py`
- `tests/test_fresh_snapshot.py`
- `tests/test_hub_lifecycle.py`
- `tests/test_output_single_shot.py`
- `tests/test_r002.py`
- `tests/test_r002_config_flow.py`
- `tests/test_ring_image.py`
- `tests/test_transport_lifecycle.py`
- `tools/verify_capture_runtime.py`
- `tools/verify_r002_runtime.py`
