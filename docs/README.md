# Documentation

Start with the [project homepage](../README.md) for compatibility, installation, the camera card and known limitations.

## User guides

- [Quick start](installation.md): setup, card, commands, languages, photos and doorbells.

- [0.4.5 stable notes and rollback](release-045.md): cloud bells, photos, local video/audio export and photo settings.
- [Output guide (FR)](connect3-four-outputs.fr.md) / [Output guide (EN)](connect3-four-outputs.md): six custom labels, four explicit output routes and per-channel doorbell observation. [Automatic setup](connect3-auto-tls.md). Earlier stable releases remain available.

- [Photos and authenticated Media storage](captures.md): capture switch, manual photos, entities and automation events. Automatic photos can interrupt the monitor's ringing around five seconds after the press.
- [Connect V1 cloud doorbell](v1-cloud-doorbell.md): validated notification delivery, optional setup and privacy. Local V1 doorbell detection remains unsupported.
- [Connect 3/R002 cloud doorbells](qv-cloud-doorbell.md): app account enrollment, separate HA subscription, two entrances, diagnostics and rollback. Real delivery is confirmed on both families.
- [Intercom card and HomeKit source](card.md): microphone, opening controls, resources and HTTPS.
- [Connect 3](connect3-audio-controls.md): local access, video/audio, controls and cloud doorbells.
- [Connect 2 R002](r002-investigation.md): confirmed video, sound, microphone and openings; manual IP setup, photos and cloud doorbells.
- [Stable 0.4.3 validation and rollback](stable-043.md).
- [Native CRC32C correction](../tools/crc32c/AIORTC-DERIVATIVE.md).
- [Security and privacy](../SECURITY.md).

## Protocol evidence and research

- Connect 3: [APK analysis](connect3-analysis.md), [video](connect3-beta7-video-evidence.md), [audio](connect3-audio-evidence.md), [microphone](connect3-talk-evidence.md), [outputs](connect3-control-evidence.md), [multichannel evidence and limits](connect3-multichannel-evidence.md), [RC1 native alarm and routing research](connect3-rc1-sdk-evidence.md).
- R002: [APK analysis](r002-apk-analysis.md), [QV transport evidence](r002-beta11-apk-evidence.md).
- V1: [media/control stability](v1-stability-beta7.md), [startup investigation](v1-startup-query-field-test.md), [local doorbell audit](v1-doorbell-reinvestigation-beta11.md).
- Photos: [passive observation](ring-passive-observation.md), [earlier delayed-capture records](ring-image-delayed-candidate.md).

These research records describe the implementation and observations at their date. Earlier claims such as “cloud unproven” or “stable 0.4.2” are historical; the homepage and user guides describe the current stable release; historical terminology does not describe its current interface.

## Release history

The `release-043-beta*.md` files, [stable 0.4.2 report](stable-042.md), older beta audits and stabilization reports are preserved as historical evidence, not current setup instructions. See the [changelog](../CHANGELOG.md) for the release sequence.
