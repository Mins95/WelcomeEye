# 0.4.3-beta.12 — experimental V1 cloud doorbell

Prerelease for testing. Stable remains **0.4.2**. This release adds optional
cloud doorbell notifications for **Connect V1 / DES9900VDP**. **Actual delivery
and coexistence with the phone's notifications are not hardware-validated.**

## Install and enable

1. In HACS, select **0.4.3-beta.12**, restart Home Assistant and keep the existing
   WelcomeEye entry and local credentials.
2. For an entry already identified as V1, open **Settings → Devices & services
   → WelcomeEye → Reconfigure**. Enable **Enable V1 cloud doorbell notifications
   (experimental)**. The option is OFF by default; upgrading does not register
   the device for cloud notifications.
3. Download integration diagnostics and check `v1_cloud_doorbell` registration,
   subscription and connection states. Readiness is not proof of ring delivery.

No Philips account/password or phone token is requested. HA creates its own
installation and receiver credentials. The device UID and this new
installation/token are sent to the manufacturer's push service; Google/Firebase
and that service must remain reachable. No incoming port is required.

Only V1 doorbell notifications use this path. Video, sound, microphone,
snapshots and door/gate controls retain their local behavior. The option does
not start video or a local V1 listener, take a photo, or send an opening command.
Connect 2 R001's local ring and the R002/Connect 3 paths are unchanged.

## First hardware check

Keep HA and Philips video players closed. Once the subscription and receiver
are ready, ring once and check both HA's **Sonnette** state/event and the normal
phone notification. Repeat after returning to idle, then after reloading the
HA entry. Disable the option through **Reconfigure** and confirm that the
phone still receives notifications and local HA video still works.

Return fresh diagnostics and separate results for HA delivery, phone delivery
and behavior after reload/disable. If registration fails, collect diagnostics
before retrying. Do not share tokens, private storage or local unlock codes.

The implementation accepts only recent call notifications for the configured
device, filters duplicates and stores receiver credentials in HA private
storage. It makes bounded connection attempts and disables only its own
subscription on cleanup. Full setup, error states and privacy details are in
[V1 cloud doorbell](v1-cloud-doorbell.md).

## Validation and rollback

Automated coverage includes LT request encoding, encrypted FCM messages,
duplicate/malformed deliveries, token rotation, cancellation, cleanup and
privacy. The Validate workflow checks Python 3.12/3.14, frontend, packaging,
HACS/Hassfest and actual Home Assistant APIs with mocked cloud transport.
Consult the run for the release revision; software tests do not establish
real provider acceptance, ring delivery or phone coexistence.

To roll back, turn the V1 option OFF while beta.12 is installed. If diagnostics
show `cleanup_pending`, restore connectivity and reload the disabled entry
to retry cleanup before downgrading. Redownload **0.4.3-beta.11** in HACS and
restart HA. Keep the entry and its local credentials; beta.11 retains local
media and controls without this cloud doorbell option.
