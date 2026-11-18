# WelcomeEye 0.4.9-beta.1

This candidate improves cloud-history resilience and the shared WebRTC player.
Existing entries, entity IDs, saved photos, credentials, certificate approvals,
output permissions and card settings are retained. Stable 0.4.8 is unchanged.

## Changes

- **First video frame:** the shared card keeps its saved photo while WebRTC
  connects, then reveals the native video only once the current session has
  decoded an image. Events from a previous session cannot reveal stale video.
  This targets Android/WebView startup placeholders and applies to supported
  camera families using the native WebRTC card. The HA fallback player retains
  its existing lifecycle. Sound and microphone remain independent.
- **Session cleanup:** a reproduced WebRTC session-ID reuse defect is corrected.
  Cleanup must finish before the same ID is reused; a fresh viewer no longer
  inherits a completed cleanup task that would leave its media lease open.
- **Connect 3/R002 cloud history:** handle a narrow set of malformed descriptive
  XML fields, while keeping identity, authorization and authentication strict.
  A history-only HTTP 404 allows one authorization check and alarm-session
  renewal before retrying the page. This does not replace the account session,
  re-register FCM, change subscriptions or generate a ring event. See the
  [history guide](cloud-history.md) for limits and diagnostics.

## Quick validation

Once this candidate is published, select **0.4.9-beta.1** in HACS and restart
Home Assistant. Reload the dashboard and Companion frontend. Keep existing
entries and cards. Manually managed resources should use the matching module:

```text
/welcomeeye_local/welcomeeye-card.js?v=0.4.9-beta.1
```

1. Open the card on your usual browser/tablet. The saved photo should remain
   visible during connection and give way to live video when its first frame
   arrives. Close, reopen, and switch sources if a second camera is configured.
2. Check sound and microphone separately, then close the video. Confirm that the
   Philips application can use the intercom again.
3. On a Connect 3/R002 with cloud notifications enabled, run the existing
   **Read cloud history** action. Export diagnostics if it fails. No new account,
   certificate approval, physical opening or network scan is needed.

Software checks use synthetic responses/media and local test peers. They do not
establish a new hardware or cloud-provider validation. No automatic video,
microphone, opening command or photo is added. Photo stabilization remains
three seconds; acquiring a video session for a photo can still interrupt the
indoor monitor call. This candidate does not change that behavior.

## Rollback

Select stable **0.4.8**, restart Home Assistant and reload the frontend. Preserve
entries, credentials and photos. Use `?v=0.4.8` for a manually managed JavaScript
resource. No configuration/storage migration is required.
