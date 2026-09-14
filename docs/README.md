# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Current development: v0.3.1-beta.6

- [V1 doorbell / compatibility investigation — beta 6](v1-doorbell-beta6.md): current development reference for the V1 local-doorbell investigation, stale discovery endpoint recovery, WebRTC/dnspython preload and safety constraints.
- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): released beta 5 reference for the V1 `16/1/2` output-control path and one-shot physical-command safety.
- [Project README](../README.md): installation, supported features, hardware status, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history and current beta candidate notes.

### Beta 6 status

WelcomeEye Connect 2 keeps its validated local video, output-control and doorbell behavior.

WelcomeEye Connect V1 video remains hardware validated. Beta 6 preserves the beta 5 `16/1/2` live-media output path and adds connection recovery for a stale cached discovery endpoint: when TCP actively refuses the cached port, the cache is invalidated and one fresh discovery/TCP attempt is allowed before login. This recovery occurs before any physical output packet exists and does not change the no-retry rule for strike/gate commands.

Beta 6 also preloads dnspython record handlers through Home Assistant's executor before the WebRTC platform can create an `RTCPeerConnection`, avoiding dynamic record-type imports on the HA event loop.

The V1 doorbell remains on standby. Reverse engineering shows that the LT SDK can parse TLV 510 and its inner OWSP payload, but the complete V1 local event delivery chain has not yet been demonstrated. The official FCM cloud-push path remains evidence of a cloud path only, not proof that no local path exists.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For current behavior, prefer the root README, CHANGELOG and the beta 6 technical note above.

## Safety and privacy

Physical door/gate commands are single-shot: the integration never automatically retries an accepted action after the physical command may have been sent. Pre-login discovery/TCP recovery is allowed because no physical output packet has been transmitted at that stage.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
