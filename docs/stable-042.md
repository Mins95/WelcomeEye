# 0.4.2 stable promotion — 2026-09-24

**Subsequent same-version correction:** the maintainer explicitly approved the
tested aiortc derivative and requested republishing it in 0.4.2. See the
[CRC32C runtime fix and installation instructions](../tools/crc32c/AIORTC-DERIVATIVE.md).
The version-only package comparison below describes the initial stable publication.

The maintainer requested promoting the latest beta to stable and removing the superseded GitHub prereleases. The source is the corrected `v0.4.2-beta.4`, commit `88c023fea8b1ca07abf34b21e50d6d43bc72b0c9`. Its published ZIP SHA256 is `038e61976ab1c33e8fb0b7b2f5b4a233f8234d2227a6a55540919a54ce6057ab`.

The stable package changes only the version in `manifest.json` and `const.py`; other integration files must match that beta archive byte for byte. Documentation and release tooling are updated outside the package. Publication requires successful Validate CI on the exact main commit and a separate explicit stable-promotion dispatch.

## Field evidence

On the corrected beta.4, the Connect 2 owner reported working video, microphone, gate and the monitor's native ring photo. The subsequent missing-HA-photo report was traced to the automatic capture switch being OFF. After enabling it, read-only checks found the preference stored as `true`, the last-ring ImageEntity `ready` with `source=fresh_snapshot`, sequence 2 and a Media reference, with no save error. The saved JPEG was verified as 52,703 bytes and 720 × 576 pixels. The owner then confirmed that it worked. No backend fix was needed for this report.

These results supplement the [historical snapshot trials](ring-image-delayed-candidate.md) and [beta.4 software audit](audit-beta4.md). They do not certify every item of the broader hardware checklist. Automated release checks do not issue physical output commands.

## Remaining limits

- Automatic ring snapshots remain experimental and OFF by default. Native monitor-photo loss after opening/closing video was previously reproduced even without automatic capture and remains unresolved. Live-video ring capture and repeated lifecycle scenarios still need broader field coverage.
- HA takes its own fresh image through the shared media worker; it does not retrieve the monitor's stored native photo.
- Local V1 doorbell is unsupported. Its validated video, microphone, strike and gate paths are unchanged.
- The Google/HA wheel-builder patch remains an unsubmitted upstream draft. The republished 0.4.2 instead installs the explicitly approved aiortc derivative using a separately distributed native CRC32C dependency.

## Upgrade and rollback

Install `0.4.2`, restart Home Assistant and fully reload the browser or Companion frontend. Enable **Capture sur sonnerie** to opt into automatic ring photos. UI-managed dashboard resources update automatically; YAML users must update their module URL to `v=0.4.2`.

The previous stable `v0.4.1` and its release archive remain available for rollback. Beta Git tags and commits remain in Git history after removal of the superseded prerelease pages/assets.
