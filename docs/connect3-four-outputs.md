# Connect 3 — names, four outputs and doorbells (0.4.4)

[Français, procédure détaillée](connect3-four-outputs.fr.md) · [Connect 3 setup](connect3-auto-tls.md)

Keep the existing integration entry and WelcomeEye card. Its graphical editor
now provides six independent names, each limited to 64 characters:
`channel_1_name`, `channel_2_name`, `strike_1_name`, `strike_2_name`,
`gate_1_name`, `gate_2_name`. They are dashboard presentation settings; entity
IDs and physical routing never depend on the names. YAML remains supported.
Secondary fields are hidden on single-panel installations.

| Target | Channel | Native output | Status |
| --- | --- | --- | --- |
| `strike_1` | 1 | 1 | Existing A331 hardware-confirmed path |
| `gate_1` | 1 | 2 | Existing A331 hardware-confirmed path |
| `strike_2` | 2 | 1 | Separate target approval; check on site |
| `gate_2` | 2 | 2 | Separate target approval; check on site |

There is no assumed common gate. Each selected command keeps its fixed channel
and relay under both camera views. The integration uses that channel's
existing media manager and single reader. Both secondary physical mappings
still require owner confirmation. They use the device's separately configured
Philips opening code, never its connection password.

Enable **Second outdoor panel**, video and opening controls first. Select the
wanted commands in **Reconfigure → Opening commands** and confirm each secondary
channel/relay separately. The existing authorizations remain in **Advanced options**.
Saving settings never operates a relay. Secondary controls retain a visible
physical-validation warning; each has its own consent.
An endpoint, credential or pin change requires renewed target consent. Disabling
the second panel, video or outputs revokes its secondary authorizations.

The controller sends at most one physical request per click and never retries.
Unknown results block further commands on that session. Native ACKs are distinct
from a physical observation or position sensor. Switching cameras never operates
a relay. A command for the other channel first releases this card's video and
microphone; another user's incompatible session is not forcibly disconnected.

## Hardware test

1. Check all six names, video switching and audible sound on both entries.
2. Enable only **Wicket gate 2**; confirm `(2, 1)`. Press once, verify that only
   that pedestrian gate reacts and download diagnostics immediately.
3. Separately enable **Vehicle gate 2**; confirm `(2, 2)`. Press once, verify all
   other relays remain inactive and download fresh diagnostics.
4. Check the primary controls still target their own relays. Do not repeat an
   ambiguous secondary result; report it and stop physical tests.
5. Close HA video and confirm Philips Door Connect can regain access.

## Doorbells and optional local observation

[Cloud doorbell notifications](qv-cloud-doorbell.md) can be configured separately
with the Door Connect app account. Real delivery and panel attribution still
need a physical test. Cloud events do not open video or take photos.
The local diagnostic below observes an existing video session only.

Open channel 1 manually. An administrator can then run:

```yaml
action: welcomeeye_local.connect3_observe_doorbell
target:
  entity_id: sensor.your_connect3_sensor
data:
  operation: start
  channel: 1
  duration: 120
```

Press that outdoor bell once. Immediately run the same action with
`operation: mark`, then `operation: status` to read the response. Download
diagnostics before the next observation. Use `operation: stop` to stop early;
closing video or reaching the deadline also stops it. Repeat separately with
channel 2 already open and `channel: 2` in the actions.

The recorded channel identifies the **observed media session**, not a proven
ringing-panel identity. Markers report manual actions; order 23 remains a
candidate, never an emitted HA ring. No raw notification text, autonomous
listener or automatic photo is enabled. Report whether the monitor kept ringing
and the Philips phone notification arrived.

The channel 2 microphone has a separate, disabled-by-default trial option;
its physical speaker destination remains unconfirmed. Native alarm listening
received heartbeats without a proven bell event. The earlier
[SDK evidence](connect3-rc1-sdk-evidence.md) records the RC1 investigation.

Manual **Photo** capture requires the open live stream. Saved images can be
viewed and removed through the administrator **Photos WelcomeEye** library;
see [photos and retention](captures.md).

## Install / rollback

Install stable **0.4.4** through HACS, restart HA and fully reload
the browser/Companion frontend. Existing entries, camera IDs, credentials and
TLS approvals are retained. No real device is contacted by automated tests.

For rollback, disable cloud notifications first and follow the
[0.4.4 release instructions](release-044.md#upgrade-and-rollback). Keep the entry
and its credentials; older versions may not support the selected TCP transport.
