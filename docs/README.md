# Documentation

This directory contains the technical notes used to develop and validate Philips WelcomeEye support.

## Current release: v0.3.1-beta.5

- [V1 control and doorbell investigation — beta 5](v1-control-events-beta5.md): current technical reference for WelcomeEye Connect V1 output control, the live-channel `16/1/2` path, one-shot safety rules, software validation and the remaining hardware checks.
- [Project README](../README.md): installation, supported features, hardware status, limitations and privacy notes.
- [Changelog](../CHANGELOG.md): public release history.

### Beta 5 status

WelcomeEye Connect 2 keeps its validated local video, output-control and doorbell behavior.

WelcomeEye Connect V1 video is validated on real hardware. Beta 5 changes V1 door/gate control to use the active media worker, matching the path identified in the official application. The software path is validated by the full automated suite, but physical strike/gate actuation still requires confirmation on hardware.

The V1 doorbell remains on standby. Reverse engineering identified an official cloud push-registration path, but no reliable local V1 subscription sequence has yet been demonstrated. The Home Assistant integration itself remains local at runtime and does not use that vendor cloud path.

## Historical research notes

The following documents are preserved as development history. They describe intermediate hypotheses and diagnostics that were valid at the time but are **not** the current implementation reference:

- [Beta 15 review](beta15-review.md): historical coordination design involving the V1 persistent doorbell listener and dedicated control sessions.
- [Beta 15 V1 video fix](beta15-v1-video-fix.md): historical video-receive investigation that led to the hardware-validated V1 video path.

For current behavior, prefer the root README, CHANGELOG and the beta 5 technical note above.

## Safety and privacy

Physical door/gate commands are single-shot: the integration never automatically retries an accepted action. A positive protocol acknowledgement is not treated as proof of relay movement.

Do not publish passwords, device identifiers, private IP addresses, cloud notification identifiers, packet captures or raw media/alarm payloads in public issues. See [SECURITY.md](../SECURITY.md).
