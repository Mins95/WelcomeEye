# 0.4.7-beta.3 — legacy video recovery

This optional update fixes H.264 recovery for **Connect V1 / Connect 2 R001**.
It retains [beta 2's features](release-047-beta2.md), entries, entity IDs and
settings. Stable remains **0.4.6**.

The old byte-stream parser could detect a corrupt image one image late, then
discard the following valid recovery image when resetting. Complete LT images
are now decoded directly. Rejected images are not sent to MPEG-TS playback,
and cached codec headers are preserved for the next valid recovery image.

Diagnostics under `media.codec` now include `video_recovery_pending`,
`video_recoveries` and `video_last_recovery_ms`. A recovery is counted only
after an image is decoded; accepting a packet alone is not confirmation.
Existing error counters and warnings remain available.

The defect and recovery are reproduced with synthetic H.264 images. This does
not establish the cause of the original device's invalid data or constitute a
hardware test. Connect 3/R002 decoders, microphone routing, physical commands
and automatic-photo timing are unchanged.

## Update and check

1. In HACS, select **v0.4.7-beta.3**, install it and restart Home Assistant.
   Keep your existing integration and card. Fully reload the frontend.
2. Close Philips playback, open the existing HA camera, listen to the sound
   and take a manual photo while live video is already open.
3. Close the HA video and verify that the Philips app can reconnect.
4. Download integration diagnostics after the check, especially if the image
   freezes. No opening-command test is needed. Do not deliberately corrupt
   device traffic or change network settings.

For manually managed dashboard resources, update the existing module URL to
`/welcomeeye_local/welcomeeye-card.js?v=0.4.7-beta.3` without adding a duplicate.

## Rollback

Select **v0.4.7-beta.2** in HACS and restart HA; keep existing entries and cards.
Use the matching version in a manually managed resource URL. For a return to
stable, follow the [beta 2 rollback instructions](release-047-beta2.md#rollback).
