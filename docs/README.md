# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Prerelease for testing: 0.4.2-beta.4

Dashboard resources managed through the HA interface remain registered and updated automatically. Existing manual entries are reused. Restart HA and fully reload the frontend after upgrading. YAML-managed resources remain manual.

The candidate adds an opt-in, persistent **Capture sur sonnerie / Ring image capture** switch, initially OFF even when upgrading from beta.3 without a saved preference. ON schedules one fresh acquisition at T+4 with no photo retry loop; OFF does no photo work and leaves supported ring detection intact. The manual `welcomeeye_local.capture_snapshot` action and the card's Photo button use the same fresh-image backend, with a separate last-snapshot entity. See [captures and private Media storage](captures.md).

On Connect 2, `Sonnette` remains `on` for **5 seconds** after each distinct decoded ring. A second distinct ring restarts that window; duplicate deliveries remain deduplicated. The `welcomeeye_local.ring` event is still immediate, regardless of the photo switch.

**V1 local doorbell is not currently functional.** Hardware trials authenticated the experimental `0/3/0` listener and observed stable TLV traffic and keepalives, but no usable local ring event. The beta.4 candidate disables that listener by default. A cloud path is possible but has not been demonstrated. The [Connect 2-path trial](v1-doorbell-connect2-trial.md) is retained as historical evidence.

Beta.4 physical validation is pending. The [consolidation audit](audit-beta4.md) distinguishes newly run software checks from inherited hardware validation. CRC32C packaging/runtime remains unchanged: there is no embedded native binary, monkey patch or warning suppression.

## Current release: 0.4.1 — stable

[Card setup and required dashboard resource](../README.md#-intercom-card-configuration--041). Register `/welcomeeye_local/welcomeeye-card.js?v=0.4.1` as a JavaScript module, then reload the frontend.

Current validation / development status:

| Device | Video / audio in/out | Door strike | Gate | Doorbell |
| --- | --- | --- | --- | --- |
| **WelcomeEye Connect 2, validated firmware** | ✅ Validated | ✅ Validated | ✅ Validated | ✅ Local detection |
| **WelcomeEye Connect V1 / DES9900VDP** | ✅ Validated from 0.4.1, including microphone / talkback | ✅ Validated | ⚠️ Physical validation pending | ❌ Not supported / no local ring detected |

The **DES9901VDP / V401.R002.A302.00.G0058.B002** firmware variant without UDP 1500 is unsupported and remains a separate investigation. No speculative transport on port 8765 or random OWSP probes are included.

Microphone access requires HTTPS with a trusted certificate plus app/browser permission. A local HTTP URL blocks `getUserMedia` in the dashboard card.

The standard camera continues to expose Home Assistant Stream/HLS. The bundled **WelcomeEye — Interphone** card uses WebRTC for interactive video/audio and microphone support. On restrictive networks, the interactive path can require suitable ICE/TURN connectivity.

## Current technical references

- [Captures and Media storage](captures.md): switch, manual service, card controls, authenticated files, persistence and retention.
- [Beta.4 consolidation audit](audit-beta4.md): verified changes, current test results and remaining hardware checks.
- [Snapshot and CRC32C technical note](snapshot-crc32c.md): shared-worker fresh still capture, APK renderer evidence and native `google-crc32c` packaging diagnostics.
- [Intercom configuration and native protocol evidence](intercom-beta1.md): current two-way-audio implementation, microphone protocol evidence, connection diagnostics and hardware validation notes.
- [V1 doorbell Connect 2-path trial](v1-doorbell-connect2-trial.md): historical beta.2 test plan; later trials did not establish V1 local ring support.
- [V1 live idle investigation](v1-live-idle-investigation.md): analysis of V1 live-session idle behavior.
- [V1 stalled-session investigation](v1-stalled-session-investigation.md): analysis of stalled V1 media sessions and cleanup.
- [V1 startup-query field test](v1-startup-query-field-test.md): current V1 startup/query field-testing notes.
- [Project README](../README.md): installation, supported features, current hardware status, transport behavior, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history.

## Stabilization baseline

- [Full stabilization audit — beta 8](stabilization-beta8.md): evidence for the native 5005/5009 distinction, confirmed cleanup/race/recovery defects, the software validation matrix and explicit hardware limitations at that stage.

Beta 8 final validation: **135 tests plus 3 subtests** passed on Python **3.12.14** and **3.14.7**; compilation, HACS and Hassfest were green. Later releases added additional transport, frontend, V1 connection and intercom validation on top of that baseline.

## Previous references

- [V1 media/control stability — beta 7](v1-stability-beta7.md): reference for the Home Assistant mDNS preload fix, initial PyAV H.264 decoder recovery, pending-output idle preservation, command-confirmation resilience and hardware validation procedure.
- [V1 doorbell / compatibility investigation — beta 6](v1-doorbell-beta6.md): reference for the unresolved V1 local-doorbell path and beta 6 stale-discovery recovery.
- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): reference for the V1 `16/1/2` output-control path and one-shot command safety.

The beta.2 listener experiment did not establish V1 device-to-Home-Assistant ring delivery. The LT SDK's ability to parse TLV 510 and its inner OWSP payload is not evidence that the tested V1 emits a usable local ring event. No reliable local doorbell path was identified in the current trials; cloud involvement remains possible but unproven.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For current behavior, prefer the root README, CHANGELOG, current V1 investigation notes and the intercom documentation.

## Safety and privacy

Door/gate commands are single-shot: the integration never automatically retries an accepted action after the command may have been sent. Pre-login discovery/TCP recovery is allowed because no output packet has been transmitted at that stage.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
