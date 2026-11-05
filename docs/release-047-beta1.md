# 0.4.7-beta.1 — auxiliary camera input

Thanks to [@rpouetpouet](https://github.com/rpouetpouet) for [PR #16](https://github.com/Mins95/WelcomeEye/pull/16) and the Connect V1 / DES9900VDP video and snapshot results in [issue #15](https://github.com/Mins95/WelcomeEye/issues/15). The original contribution is retained, with configuration and routing safeguards added.

## Install and test

1. Back up Home Assistant. In HACS, enable pre-release versions for Philips WelcomeEye, select **v0.4.7-beta.1**, download it and restart HA.
2. Keep the existing integration entry. Open **Settings → Devices & services → Philips WelcomeEye → Reconfigure**.
3. Enable **Second video source** and select **Auxiliary camera (input 3)** if a camera is physically connected to that monitor input. Save. No new credentials or network scan are needed.
4. Use the existing WelcomeEye card to open its second source. Check that it shows the auxiliary camera, then take a **Photo** while the video remains open. Check the saved image and its source. The second source's name remains editable in the card editor.
5. Close that video, reopen the main source, then close HA playback and check that the Philips app can use the device. Download the integration diagnostics whether the test succeeds or fails.

With a second outdoor panel instead, keep **Second outdoor panel (input 2)**. Without a second source, leave the option disabled. Stable 0.4.6 remains available and unchanged.

## What changes

The second source uses wire channel 17 for input 2 or 18 for input 3, with stream 1 and mode 2. The HA camera identity stays the same. Media, photos and observations remain isolated from the main source; only one source uses the device media worker at a time. The auxiliary photo reuses the open live session.

An auxiliary camera is not a second door station: its microphone and opening commands are unavailable. Main-panel commands keep their original targets. Switching inputs clears secondary microphone/relay approvals; switching back to input 2 does not silently restore them. No command is sent during setup or switching.

The ring-capture guard still rejects secondary doorbell selectors even when input 3 is selected. It does not map a second doorbell to the auxiliary camera or change the known monitor-ringing interruption caused by opening a media session.

English, French and Spanish source labels are included. R002 and Connect 3 retain their existing QV channel selection.

## Validation and limits

Regression coverage includes form normalization, retained identities/credentials, consent revocation, blocked auxiliary controls, pending-command cancellation, local/cloud ring identity, fresh input-3 photos, and source changes during capture. The actual Home Assistant harness also exercises the auxiliary path through loopback WebRTC with synthetic video/audio, private photo storage, permissions and cleanup. Automated tests do not contact a physical intercom or operate a relay.

The contributor confirmed auxiliary video/photos on a protected Connect V1 before this integration. The final beta package and other legacy hardware still require field confirmation. Receiving synthetic audio in a test does not establish audio capability on an auxiliary camera.

## Return to stable

In HACS, select **v0.4.6** and restart HA. Keep the entry and dashboard. Stable 0.4.6 ignores the new input selector and uses input 2 for its second source, so disable that source if you only have an auxiliary camera. To restore all earlier settings exactly, use the backup taken before installation.
