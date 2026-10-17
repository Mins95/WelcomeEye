# Cloud doorbells — Connect 3 and Connect 2 R002

Version 0.4.4 adds an independent Home Assistant notification registration for
**Philips Door Connect accounts on Connect 3** and **WelcomeEye accounts on R002**.
Video, sound, microphone and opening commands remain local. A cloud notification
never starts video or takes a snapshot.

## Setup

1. Install 0.4.4 through HACS, restart HA and retain existing entries.
2. Open **Reconfigure → Cloud doorbell notifications → Configure**.
3. Enter the official app account and approve its use by HA. This is neither the
   local intercom password nor the opening code.
4. Select the intercom from the account’s authorized devices and confirm that it
   matches this HA entry.
5. Keep **Second outdoor panel** enabled when two panels are installed.

A local QR/password alone does not provide a cloud account. The account must
contain the device. This method cannot yet be used without that account. HA does
not guess credentials or replace the device’s association in the Philips app.

## First hardware test

Close HA and Philips video players. Press entrance 1 once, let its cycle finish,
then test entrance 2 separately. Check the HA doorbell sensors, indoor monitors
and phone notifications. Download integration diagnostics and report the order
of the presses.

Automation events use `welcomeeye_local.ring`, with `source: cloud_qv` and
`channel: 1` or `2`. Old messages, duplicates and other devices are ignored.
Local heartbeat messages never become doorbell events.

**Validation status:** the APK paths are implemented and tested with simulated
services. The public service directory was verified. Account login, real delivery,
entrance attribution and phone coexistence still require the first hardware test.
Server subscription acceptance alone does not prove delivery of a ring.

## Disable and rollback

Choose **Reconfigure → Cloud doorbell notifications → Disable**. HA removes only
its own subscriptions. If the provider is unavailable, cleanup remains pending;
reload the entry to retry. Local functions remain available without cloud access.
After three connection failures, the diagnostic asks for an entry reload instead
of retrying indefinitely.

Before rolling back to RC4, disable cloud, redownload RC4 through HACS and restart
HA. Keep the entry and its credentials. Earlier releases are not overwritten.

## Privacy and transport

The account, selected device and FCM registration remain in HA’s private
configuration/storage. They are excluded from entity attributes and diagnostics.
HA has its own installation identity and does not copy or replace phone tokens.
Only calls from the authorized outdoor panel are considered; indoor-monitor calls
do not become outdoor doorbell events.

The credential-free directory at `sh2.qvcloud.net:443` uses the APK’s dedicated
public vendor CA, with chain and date verification. Its certificate has no DNS
name, so this trust authenticates the vendor authority rather than a DNS-name
match. That policy is restricted to the exact directory endpoint. Account and
notification services use ordinary HTTPS certificate and DNS-name verification.
No trust policy is changed globally and no password is sent to the directory.
