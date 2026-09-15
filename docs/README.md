# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Current release: v0.3.1-beta.8

- [Full stabilization audit — beta 8](stabilization-beta8.md): evidence for the
  native 5005/5009 distinction, confirmed cleanup/race/recovery defects, the full
  software validation matrix and explicit hardware limitations.
- [Project README](../README.md): installation, supported features, current hardware
  status, WebRTC/STUN/TURN behavior, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history.

### Beta 8 status

WelcomeEye Connect 2 keeps its previously validated local video, output-control and doorbell behavior. The beta 8 non-regression suite is green and does not alter the validated Connect 2 command/framing paths.

WelcomeEye Connect V1 keeps the hardware-validated `16/1/2` media path and single-shot output architecture. Beta 8 adds the native zero-payload session-stop TLV **5005** after the existing Stop AV **5009** and before TCP close. Native analysis did not demonstrate a mandatory 5010 wait. Whether 5005 clears the real V1 busy display remains a hardware test item.

V1 output safety remains strict: one accepted action permits at most one TLV 505 send attempt. Delayed TLV 506 confirmations at 0, 1, 5 and 9 seconds are covered on the same simulated session without replay. Physical strike/gate actuation and real TLV 506 reception remain unvalidated on the current candidate.

V1 H.264 recovery now retains a bounded SPS/PPS pair so decoding can resume after a decoder reset when the next genuine IDR omits those parameter sets. This recovery is V1-only; the normal Connect 2 decoding path remains unchanged.

WebRTC continues to use Home Assistant-provided ICE servers. STUN/TURN settings supplied by Home Assistant are supported and no external TURN server is hardcoded. Real TURN relay traversal on the restrictive enterprise Wi-Fi remains to be validated separately.

Final release validation: **135 tests plus 3 subtests** pass on Python **3.12.14** and **3.14.7**; compilation, HACS and Hassfest are green.

## Previous baseline: v0.3.1-beta.7

- [V1 media/control stability — beta 7](v1-stability-beta7.md): reference for the Home Assistant mDNS preload fix, initial PyAV H.264 decoder recovery, pending-output idle preservation, command-confirmation resilience and hardware validation procedure.
- [V1 doorbell / compatibility investigation — beta 6](v1-doorbell-beta6.md): reference for the unresolved V1 local-doorbell path and beta 6 stale-discovery recovery.
- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): reference for the V1 `16/1/2` output-control path and one-shot command safety.

The V1 doorbell remains on standby. Reverse engineering shows that the LT SDK can parse TLV 510 and its inner OWSP payload, but the complete V1 local event delivery chain has not yet been demonstrated. The official FCM cloud-push path remains evidence of a cloud path only, not proof that no local path exists.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For current behavior, prefer the root README, CHANGELOG and the beta 8 audit above.

## Safety and privacy

Door/gate commands are single-shot: the integration never automatically retries an accepted action after the command may have been sent. Pre-login discovery/TCP recovery is allowed because no output packet has been transmitted at that stage.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
