# Badge catalog

`welcomeeye_local.read_badge_catalog` reads the configured badge catalog from a
Connect 3 or Connect 2 R002 through its existing CGI HTTPS endpoint. The configured
local password, device identification and TLS trust checks still apply. Firmware
support has not been confirmed on every device; a failed read is inconclusive.
Connect V1 and Connect 2 R001 are unsupported by this action.

In the inspected APK, `QvDeviceSDK.getRFIDList` routes legacy devices through
`QvDeviceLtApi`, which delegates to `QvDeviceDefaultApi.callNotSupport`
(`FAIL_FUNCTION_NOT_SUPPORT`, -10025). The proximity-card information method
follows the same unsupported path. This is not proof of a hardware limitation,
but no alternative remote read command is established for V1/R001.

In **Developer tools → Actions**, select **WelcomeEye: Read badge catalog** and
the device's status sensor. An identified administrator with control permission
for that entity must run the action.

```yaml
action: welcomeeye_local.read_badge_catalog
target:
  entity_id: sensor.your_welcomeeye_status
data:
  include_details: false
```

Replace the example entity ID with the sensor selected in Home Assistant. With
`include_details` omitted or `false`, the response reports `badge_count` without
card identifiers or names. Explicitly setting it to `true` adds `badges`, with
each record's `card_id`, `name` and `native_channels`. Identifiers keep their
leading zeroes and original case. Native channel numbers are returned as reported,
without assuming a physical panel or relay mapping.

Badge identifiers and names are sensitive. They appear only in the explicitly
requested action response and are excluded from diagnostic exports. Home Assistant
script traces may retain that response; review it before sharing.

The SDK command `get.rfid.info` describes a configured-card catalog. There is no
verified link between a catalog entry and an opening event; successful reads
report `badge_event_correlation_verified: false`. A recorded name does not prove
who presented a badge or whether a relay physically moved.

This action does not add, rename or delete badges, operate a relay, start video or
poll in the background. The CGI read has a 12-second deadline and accepts at most
256 cards. Malformed or oversized catalogs are rejected rather than returned as
an apparently complete list.
