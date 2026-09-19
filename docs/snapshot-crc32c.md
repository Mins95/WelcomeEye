# Snapshot and CRC32C technical note

This note documents the two changes prepared for the next 0.4.2 candidate. It does not change the WelcomeEye device protocol or the V1 doorbell path.

## Fresh camera snapshots

The official APK's `com.quvii.compathlt.QvLtPlayerCore.snapShot(...)` delegates to the active renderer's `mRender.snapShotCompat(...)`. `PreviewModel.snapshot(...)` calls that player method and then saves the resulting file locally on the phone. The inspected source does not demonstrate a separate network still-image command.

The integration therefore keeps one media owner in `WelcomeEyeHub`:

1. `camera.async_camera_image()` records the current frame generation.
2. It acquires a short-lived lease through the existing `hub.acquire()` method.
3. The existing HLS/WebRTC worker is reused when already active; otherwise that same worker is started temporarily.
4. The request waits for an image with a strictly newer generation. It never returns the previous JPEG as the answer to a new request.
5. The lease is released in `finally`, so an idle snapshot cannot leave a media session running.

If no new decoded frame arrives before the timeout, Home Assistant receives no image and the diagnostic counters identify the timeout. No second media session, capture command, or output command is created.

## `google-crc32c`

`aiortc==1.15.0` requires `google-crc32c>=1.1`, but the integration previously left the final version to Home Assistant's resolver. The warning shown by Home Assistant means the package imported its pure-Python fallback because the native `cext` module was unavailable or could not be loaded; it does not identify a WelcomeEye packet or CRC value error.

The candidate pins `google-crc32c==1.8.0`. Its published CPython 3.14 wheels cover the common x86_64 and aarch64 manylinux runtimes used by Home Assistant. The diagnostic report records only package version, Python/runtime architecture, module path, selected implementation and the native-import error type. It contains no device credentials, endpoint, media or protocol payload.

If a target image still reports `implementation: "python"` after the integration requirements are reinstalled, the remaining cause is the target image's native wheel/libc/loader environment. The integration does not suppress the warning, alter CRC behavior or monkey-patch the package.
