# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Current development: v0.3.1-beta.8 candidate (not released)

- [Full stabilization audit — beta 8](stabilization-beta8.md): current evidence,
  native 5005/5009 distinction, confirmed cleanup/race/recovery defects, test
  results and explicit hardware limitations. Beta7 notes below are the baseline.

## Published baseline: v0.3.1-beta.7

- [V1 media/control stability — beta 7](v1-stability-beta7.md): current reference for the Home Assistant mDNS preload fix, PyAV H.264 decoder recovery, pending-output idle preservation, command-confirmation resilience and hardware validation procedure.
- [V1 doorbell / compatibility investigation — beta 6](v1-doorbell-beta6.md): current reference for the unresolved V1 local-doorbell path and beta 6 stale-discovery recovery.
- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): reference for the V1 `16/1/2` output-control path and one-shot command safety.
- [Project README](../README.md): installation, supported features, hardware status, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history and current beta candidate notes.

### Beta 7 status

WelcomeEye Connect 2 keeps its validated local video, output-control and doorbell behavior unchanged.

WelcomeEye Connect V1 keeps the hardware-validated `16/1/2` media path and the beta 5/6 output architecture. Beta 7 addresses failures observed on Home Assistant 2026.9.x / Python 3.14:

- aioice mDNS can request dnspython class `32769` (`IN | 0x8000`), so beta 7 warms that exact class/type cache in Home Assistant's executor before WebRTC starts;
- a PyAV `InvalidDataError` from one H.264 access unit no longer tears down the entire V1 TCP/media session. The bad access unit is dropped, the H.264 decoder is recreated, dependent P frames are ignored and decoding resumes from a later keyframe;
- while a sent TLV 505 is waiting for TLV 506, a completely clean V1 OWSP-header timeout may be treated as idle time on that same authenticated session. The guard is limited to the pending-command window and never reconnects or resends the physical command.

Tester diagnostics showed a single TLV 505 being sent successfully before the media session closed while waiting for TLV 506. Beta 7 does not invent a retry for that uncertain command. Regression tests reproduce both identified software failure modes around this window and verify that the same worker/session can recover and later accept TLV 506 without sending a second TLV 505.

The full suite is run on both Python 3.12 and Python 3.14 with PyAV 17.0.1 and dnspython 2.8.0. The beta 7 candidate contains **101 tests plus 3 subtests**. HACS and Hassfest must remain green before publication.

The V1 doorbell remains on standby. Reverse engineering shows that the LT SDK can parse TLV 510 and its inner OWSP payload, but the complete V1 local event delivery chain has not yet been demonstrated. The official FCM cloud-push path remains evidence of a cloud path only, not proof that no local path exists.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For candidate behavior, prefer the root README, CHANGELOG and the beta 8 audit above.

## Safety and privacy

Door/gate commands are single-shot: the integration never automatically retries an accepted action after the command may have been sent. Pre-login discovery/TCP recovery is allowed because no output packet has been transmitted at that stage.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
