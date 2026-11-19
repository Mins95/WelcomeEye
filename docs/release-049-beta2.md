# WelcomeEye 0.4.9-beta.2

## Install

In HACS, open **Philips WelcomeEye**, select version **v0.4.9-beta.2** and restart Home Assistant. Use your existing WelcomeEye entry and card. If you manage the JavaScript resource manually, update its query string to `?v=0.4.9-beta.2` and reload the dashboard.

## Try passive HomeKit previews on a Connect 2 R001 or V1

1. Open **Settings → Devices & services → Philips WelcomeEye** and select **Reconfigure** for the intercom.
2. In **Photo management**, enable **Use passive camera previews (HomeKit)**. If automatic ring photos are enabled and you want the monitor to keep ringing while HA video is closed, also enable **Take a ring photo only when this entrance is already live**.
3. Close live video in HA and Apple Home. To test, ring once and open the camera tile overview in Apple Home. Keep the live player closed. Check whether the indoor monitor completes its ringing cycle.

Passive preview requests use an image from that entrance taken within the last 60 seconds. Before the first image or after it expires, HA returns no new thumbnail. Apple Home may still display an earlier image from its own cache. The WelcomeEye card’s **Photo** button can explicitly acquire video and can interrupt the indoor monitor. R002 and Connect 3 already serve passive previews. [Capture settings](captures.md)

## Other checks

The WelcomeEye sidebar offers private opening history on cloud-connected R002 and Connect 3 devices. R001 badge detection still needs a verified source. The standard Home Assistant player supports the main QV camera microphone with **Two-way audio** enabled in HA Labs and control permission on the camera. [Badge support](badge-catalog.md) · [Camera and microphone](card.md)

## Rollback

Select **0.4.8** in HACS, restart Home Assistant and refresh the dashboard. If you manage the resource URL manually, restore `?v=0.4.8`.
