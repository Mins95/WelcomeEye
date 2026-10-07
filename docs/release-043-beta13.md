# 0.4.3-beta.13 — V1 cloud timestamp fix and ring photos

> Historical record: see the [current 0.4.3 guide](../README.md) for support, setup and known limitations.

Prerelease for testing. Stable remains **0.4.2**. The beta.12 V1 diagnostic
recorded one received FCM message, then `stale_messages=1` and `rings_received=0`.
The phone still notified the tester, but **no accepted HA cloud ring or automatic
V1 photo has yet been hardware-validated**.

## Changes

- Preserve incoming FCM timestamps in epoch milliseconds; remove the incorrect
  multiplication by 1,000. Keep the 120-second age limit and reject missing,
  invalid or conflicting timestamps. Diagnostics expose safe rejection reasons.
- For a V1 with cloud notifications enabled, expose **Ring image capture** and
  **Last ring**. Capture defaults ON unless an OFF preference was saved. An
  accepted ring fires immediately and schedules one fresh local snapshot from
  T+4 seconds, using the shared media worker and authenticated HA Media storage.
  Rejected messages cannot trigger photos. This does not retrieve the monitor's
  native photo.

The V1 cloud option remains opt-in and OFF by default. Media and physical
controls keep their local behavior. Connect 2 R001 and the QV families are
unchanged. [V1 setup, scope and privacy](v1-cloud-doorbell.md).

## Tester procedure

1. Install **0.4.3-beta.13** in HACS and restart HA. Keep the existing entry and
   credentials. Keep V1 cloud notifications ON in **Reconfigure** and set
   **Ring image capture** ON for this test.
2. Close HA and Philips video players. Wait for the cloud subscription and
   receiver to be ready, then press the bell once. An old acknowledged message
   will not be replayed by this fix.
3. Check the HA ring, the phone notification, the monitor's own saved photo,
   then HA's **Last ring** image and its saved image in **Media → WelcomeEye**.
   Allow the T+4 delay and fresh-image acquisition to complete.
4. Download fresh integration diagnostics and report those outcomes separately.
   Do not test strike/gate for this change or share tokens/private storage.

## Validation and rollback

Offline coverage includes timestamp units, rejection reasons, duplicate filtering,
V1 capture scheduling, shared-session cleanup, image updates and authenticated
Media access. These checks do not establish provider delivery or monitor-photo
coexistence. The release revision's Validate run supplies the CI result.

To roll back, optionally turn **Ring image capture** OFF, redownload
**0.4.3-beta.12** in HACS and restart HA. Keep the entry and cloud option enabled
if desired. Beta.12 retains the timestamp bug and has no V1 automatic ring
capture. Turning cloud notifications OFF remains available in **Reconfigure**.
