# Documentation

Start with the [project homepage](../README.md) for compatibility, installation, the camera card and known limitations.

## User guides

- [RC1 guide (FR)](connect3-four-outputs.fr.md) / [RC1 guide (EN)](connect3-four-outputs.md): six custom labels, four explicit output routes and per-channel doorbell observation. [Automatic setup](connect3-auto-tls.md). Stable 0.4.3 remains available.

- [Photos and authenticated Media storage](captures.md): capture switch, manual photos, entities and automation events. Automatic photos can interrupt the monitor's ringing around five seconds after the press.
- [Connect V1 cloud doorbell](v1-cloud-doorbell.md): validated notification delivery, optional setup and privacy. Local V1 doorbell detection remains unsupported.
- [Intercom card](intercom-beta1.md): microphone, opening controls, resources and HTTPS.
- [Connect 3](connect3-audio-controls.md): local access, video/audio, controls and remaining doorbell investigation.
- [Connect 2 R002 investigation](r002-investigation.md): current experimental status and explicit diagnostic actions.
- [Stable 0.4.3 validation and rollback](stable-043.md).
- [Native CRC32C correction](../tools/crc32c/AIORTC-DERIVATIVE.md).
- [Security and privacy](../SECURITY.md).

## Protocol evidence and research

- Connect 3: [APK analysis](connect3-analysis.md), [video](connect3-beta7-video-evidence.md), [audio](connect3-audio-evidence.md), [microphone](connect3-talk-evidence.md), [outputs](connect3-control-evidence.md), [multichannel evidence and limits](connect3-multichannel-evidence.md), [RC1 native alarm and routing research](connect3-rc1-sdk-evidence.md).
- R002: [APK analysis](r002-apk-analysis.md), [QV transport evidence](r002-beta11-apk-evidence.md).
- V1: [media/control stability](v1-stability-beta7.md), [startup investigation](v1-startup-query-field-test.md), [local doorbell audit](v1-doorbell-reinvestigation-beta11.md).
- Photos: [passive observation](ring-passive-observation.md), [earlier delayed-capture trials](ring-image-delayed-candidate.md).

These research records describe the implementation and observations at their date. Earlier claims such as “cloud unproven” or “stable 0.4.2” are historical; the homepage and user guides identify stable and beta features separately.

## Release history

The `release-043-beta*.md` files, [stable 0.4.2 report](stable-042.md), older beta audits and stabilization reports are preserved as historical evidence, not current setup instructions. See the [changelog](../CHANGELOG.md) for the release sequence.
