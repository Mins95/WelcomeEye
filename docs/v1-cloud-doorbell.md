# V1 cloud doorbell and ring photos

**Stable 0.4.3:** optional V1 / DES9900VDP cloud ring notifications and automatic HA photos have been physically validated. The cloud option is **OFF by default**. Video, sound, microphone and strike/gate controls continue to use local protocols.

## Enable the optional cloud route

Install **0.4.3** in HACS, restart HA and keep the existing entry. For an identified Connect V1 / DES9900VDP, open **Reconfigure** and enable **Enable V1 cloud doorbell notifications (experimental)** / **Activer la sonnette V1 via le cloud (expérimental)**. The wording is the current HA option label. An upgrade with cloud OFF does not register a receiver.

HA creates its own installation and FCM receiver, then associates that receiver's token and the device UID through the manufacturer's LT push service. This requires Internet access to the manufacturer and Google/Firebase, with no incoming port. No Philips account/password or phone token is requested, and the local unlock code is not sent. No local V1 bell listener is enabled.

A valid recent notification for this device produces an immediate `welcomeeye_local.ring` event with `source: cloud` and a five-second ring-sensor pulse. Stale, unrelated, malformed and duplicate messages cannot trigger a ring or photo. The ring sensor is unavailable until the subscription and receiver are ready.

## Automatic photos and the ringing limitation

Enabling V1 cloud also exposes **Ring image capture / Capture sur sonnerie** and **Last ring**. Capture defaults **ON** unless a saved OFF preference exists. One fresh local snapshot starts from **T+4 seconds after HA accepts the ring**, using the shared media session. It updates Last ring and attempts a save in **Media → WelcomeEye**. This is HA's own fresh image, not the monitor's stored/native photo.

**Known limitation:** this acquisition can stop the native monitor call and outdoor-unit ringing around **five seconds after the press**. Smartphone notifications and ringing in the Philips app continue. Set **Capture sur sonnerie OFF** to avoid automatic photo acquisition while retaining cloud ring detection and events. The choice persists across reloads/restarts; live players and camera-thumbnail requests can still acquire media. See [capture behavior and storage](captures.md).

## Privacy and lifecycle

Cloud opt-in sends the device UID and the separate HA receiver identity/token to the push services. Receiver credentials stay in HA's private `.storage`, outside entity attributes, events, logs and diagnostics. This storage is not encrypted at rest; treat backups as sensitive. Deduplication retains bounded hashes rather than notification text. Media and physical output commands stay local.

Connection attempts are limited to three per entry load. After `failed_reload_required`, restore connectivity and reload the entry. Disable/unload stops callbacks and attempts to disable only the HA subscription. Failed cleanup is retained for a later reload, without creating a new receiver while disabled. Remote data deletion and every provider multi-client behavior are not guaranteed.

Diagnostics report readiness, accepted delivery and fixed rejection reasons without exposing UIDs, tokens, raw timestamps or payloads. `delivery_observed` means an accepted ring was received. Automatic capture diagnostics distinguish media acquisition from Media-save failures.

## Historical development evidence

The [beta.13 release notes](release-043-beta13.md) record the timestamp correction, offline test results and the validation status **before** the subsequent physical confirmation. Their pending tester checklist is historical, not the current stable support status. Beta.12 had the timestamp bug and no V1 automatic ring capture.
