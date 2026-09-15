# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Current release candidate: v0.3.1-rc.1

- RC1 changes the **frontend transport policy** only: Home Assistant now uses
  the integration's existing `stream_source()` / Stream-HLS path instead of the
  native WelcomeEye WebRTC handler.
- The change is based on a real restrictive enterprise-Wi-Fi test where beta 8
  delivered healthy H.264 into Home Assistant but ICE remained stuck in
  `checking` with STUN available and no TURN. Disabling native WebRTC made the
  same stream work through Home Assistant's HTTP stream path without any extra
  service, TURN relay, firewall change or container.
- The native WebRTC implementation remains in the source tree but is not
  advertised by the camera in RC1. This keeps a path open for a future,
  separately validated automatic transport selector while RC1 prioritizes
  compatibility.
- Initial Stream/HLS playback may need a few seconds of buffering before it
  stabilizes. That is currently preferred to a permanently stuck WebRTC viewer
  on restrictive networks.
- Diagnostics expose `frontend_transport=home_assistant_stream` and
  `native_webrtc_advertised=false`.

### Protocol and hardware scope

RC1 does **not** change WelcomeEye device protocol behavior. Connect 2 media,
output control and doorbell behavior are unchanged. V1 keeps the hardware-validated
`16/1/2` media path, single-shot TLV 505 output safety, delayed 506 handling,
5009 + native 5005 teardown, bounded H.264 SPS/PPS recovery and doorbell standby.

The existing loopback MPEG-TS proxy remains bound to `127.0.0.1`; Home Assistant's
Stream integration consumes that internal source and exposes the frontend stream.

## Beta 8 stabilization baseline

- [Full stabilization audit — beta 8](stabilization-beta8.md): evidence for the
  native 5005/5009 distinction, confirmed cleanup/race/recovery defects, the full
  software validation matrix and explicit hardware limitations.
- [Project README](../README.md): installation, supported features, current hardware
  status, transport behavior, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history.

Beta 8 final validation: **135 tests plus 3 subtests** passed on Python **3.12.14**
and **3.14.7**; compilation, HACS and Hassfest were green. RC1 adds focused
transport/version regression checks on top of that suite.

## Previous references

- [V1 media/control stability — beta 7](v1-stability-beta7.md): reference for the Home Assistant mDNS preload fix, initial PyAV H.264 decoder recovery, pending-output idle preservation, command-confirmation resilience and hardware validation procedure.
- [V1 doorbell / compatibility investigation — beta 6](v1-doorbell-beta6.md): reference for the unresolved V1 local-doorbell path and beta 6 stale-discovery recovery.
- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): reference for the V1 `16/1/2` output-control path and one-shot command safety.

The V1 doorbell remains on standby. Reverse engineering shows that the LT SDK can parse TLV 510 and its inner OWSP payload, but the complete V1 local event delivery chain has not yet been demonstrated. The official FCM cloud-push path remains evidence of a cloud path only, not proof that no local path exists.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For current behavior, prefer the root README, CHANGELOG, this RC1 transport note and the beta 8 stabilization audit.

## Safety and privacy

Door/gate commands are single-shot: the integration never automatically retries an accepted action after the command may have been sent. Pre-login discovery/TCP recovery is allowed because no output packet has been transmitted at that stage.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
