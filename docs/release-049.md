# WelcomeEye 0.4.9

## Install or update

Select **Philips WelcomeEye 0.4.9** in HACS, restart Home Assistant and reload the dashboard. Keep your existing integration entry, cameras and cards. If you registered the card resource manually, update its URL to `/welcomeeye_local/welcomeeye-card.js?v=0.4.9`.

Home Assistant **2025.12.0** is the minimum. Older versions pinned PyAV 13, below the dependency's supported range, and the HACS listing now reflects that limit.

## Video and microphone

The integration uses aiortc 1.17.0 for WebRTC, with the native CRC32C dependency used by WelcomeEye. Upstream changes cover track replacement, renegotiation and reporting frames sent by the video encoder. [Upstream changelog](https://aiortc.readthedocs.io/en/latest/changelog.html)

To check a blank picture, enable **Show live stream quality** in the card editor. Download the integration diagnostics while that entrance is open. With a single viewer, compare `webrtc_outbound_video` for the selected channel (`frames_encoded` and `frames_sent`) with the frames received and decoded in the card. Take two readings a few seconds apart to see which counters advance. The HA counters combine any active viewers of that entrance. An unavailable counter means that the browser or sender did not report it.

For a playback check, open the live view, enable sound, then turn on the microphone separately. Close the viewer, reopen it and check the image and audio again. Close it before returning to the Philips app. The automated checks use simulated intercoms. A test on a physical iPhone or intercom is a separate validation.

## Keep camera previews passive

On Connect V1 and Connect 2 R001, enable **Reconfigure → Photo management → Use passive camera previews (HomeKit)** to serve a recent image from the requested entrance without opening the intercom. Once the image is older than 60 seconds, HA has no new preview to return. Apple Home may display its own cached image. This setting is optional.

If automatic ring capture is enabled, **Take a ring photo only when this entrance is already live** prevents a ring photo from acquiring the intercom while its indoor monitor is ringing. The card's **Photo** button can still request a fresh image. A newly opened stream waits three seconds after its first decoded frame before selecting a fresh photo. Opening media during a call can interrupt the monitor or make the Philips app report an answered call. [Photo settings](captures.md)

## Other changes since 0.4.8

- Select a Home Assistant media directory for new photos and choose which opening buttons ask for confirmation.
- R002 TCP media requires verified CGI and the expected QV credential protection. R002 and Connect 3 respect shared-account alarm channels and offer private opening history. Identification of the badge used on a Connect 2 R001 remains unconfirmed.
- Supported main R002 and Connect 3 cameras can use the standard HA player microphone when HA two-way audio is enabled and the user has control permission. [Camera settings](card.md)

## Roll back

In HACS, select **0.4.8**, restart HA and reload the dashboard. If you manage the resource manually, restore `?v=0.4.8`. The previous stable release used active thumbnails on the primary V1/R001 camera. [0.4.8 release](https://github.com/Mins95/WelcomeEye/releases/tag/v0.4.8)
