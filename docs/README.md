# Documentation — 0.4.3

Start with the [project homepage](../README.md) for compatibility, installation, the camera card and known limitations.

## User guides

- [Photos and authenticated Media storage](captures.md): capture switch, manual photos, entities and automation events. Automatic photos can interrupt the monitor's ringing around five seconds after the press.
- [Connect V1 cloud doorbell](v1-cloud-doorbell.md): validated notification delivery, optional setup and privacy. Local V1 doorbell detection remains unsupported.
- [Intercom card](intercom-beta1.md): microphone, opening controls, resources and HTTPS.
- [Connect 3](connect3-audio-controls.md): local access, video/audio, controls and remaining doorbell investigation.
- [Connect 2 R002 investigation](r002-investigation.md): current experimental status and explicit diagnostic actions.
- [Stable 0.4.3 validation and rollback](stable-043.md).
- [Native CRC32C correction](../tools/crc32c/AIORTC-DERIVATIVE.md).
- [Security and privacy](../SECURITY.md).

## Protocol evidence and research

- Connect 3: [APK analysis](connect3-analysis.md), [video](connect3-beta7-video-evidence.md), [audio](connect3-audio-evidence.md), [microphone](connect3-talk-evidence.md), [outputs](connect3-control-evidence.md).
- R002: [APK analysis](r002-apk-analysis.md), [QV transport evidence](r002-beta11-apk-evidence.md).
- V1: [media/control stability](v1-stability-beta7.md), [startup investigation](v1-startup-query-field-test.md), [local doorbell audit](v1-doorbell-reinvestigation-beta11.md).
- Photos: [passive observation](ring-passive-observation.md), [earlier delayed-capture trials](ring-image-delayed-candidate.md).

These research records describe the implementation and observations at their date. Earlier claims such as “cloud unproven” or “stable 0.4.2” are historical; the homepage and current user guides describe 0.4.3.

## Release history

The `release-043-beta*.md` files, [stable 0.4.2 report](stable-042.md), older beta audits and stabilization reports are preserved as historical evidence, not current setup instructions. See the [changelog](../CHANGELOG.md) for the release sequence.
