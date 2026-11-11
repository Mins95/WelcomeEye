# 0.4.8-beta.2 — Stabilized photos

Photos now wait for three seconds of camera startup after the first decoded
video frame, then use a newly received image. This covers WelcomeEye V1,
Connect 2 R001/R002 and Connect 3, including their supported secondary sources.
Manual photos, automatic ring photos and camera previews use the same stabilized
JPEG output. Live video and sound remain immediate; an already established
stream does not wait an extra three seconds for each photo.

On the owner's Connect 2 R001, a bounded observation with the previous version
received a monochrome first frame at 0.79 seconds after opening the connection,
colour by 1.25 seconds, and more stable colour around 3–4 seconds. That observation
motivates this change; it does not establish the necessary delay on every firmware
or prove night-light timing. Legitimate monochrome night images remain valid.

## Installation and validation

1. Select **v0.4.8-beta.2** in HACS/Marketplace prereleases and restart Home Assistant.
2. Keep existing entries, credentials, certificate approvals and dashboard cards.
3. After closing video, request a photo and check the resulting image. On a
   legacy primary camera, a HomeKit preview can acquire video and should now
   wait for stabilization instead of returning its first monochrome startup frame.
4. With video already running for several seconds, request another photo; it
   should use the established stream without a new startup wait or connection.
5. Check again after dark and provide diagnostics if the photo still precedes
   the camera lighting transition. No relay test is required.

QV and secondary-camera previews keep their existing passive behaviour: they
do not start a video session simply to produce a thumbnail. Their fresh JPEG
becomes available once a voluntarily opened stream has stabilized. Previously
saved photos in Media and the last-photo entities are retained.

The delay configured after a doorbell press is independent and unchanged.
This change does **not** resolve the known monitor-call interruption caused by
acquiring video for a photo. No background video or repeated acquisition is added.
Timeout and cancellation still release the capture's own session lease.

Software validation covers startup-frame rejection, a new frame after warmup,
unchanged live-video delivery, independent channels, restarts and cancellation,
with synthetic media and local simulated peers. It is not a hardware validation
claim for every model.

## Rollback

Select **v0.4.8-beta.1** or stable **v0.4.7** in HACS/Marketplace and restart HA.
Keep the same entries and saved photos; this change requires no migration.
