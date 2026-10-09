# Connect 3 RC1 — names, four outputs and doorbell observation

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
| `strike_2` | 2 | 1 | Separate opt-in physical trial |
| `gate_2` | 2 | 2 | Separate opt-in physical trial |

There is no assumed common gate. The SDK passes the selected channel and
output independently to its current live player. RC1 uses that channel's
existing media manager and single reader. Both secondary physical mappings
still require owner confirmation. They use the device's separately configured
Philips opening code, never its connection password.

Enable **Second outdoor panel**, video and physical outputs first. Then choose
the required secondary trial in **Reconfigure → Advanced options** and confirm
its exact channel/relay. Saving settings never operates a relay. The new HA
button and card control are visibly marked as trials; each has its own consent.
An endpoint, credential or pin change requires renewed target consent. Disabling
the second panel, video or outputs revokes its trial enablements.

The controller sends at most one physical request per click and never retries.
Unknown results block further commands on that session. Native ACKs are distinct
from a physical observation or position sensor. Switching cameras never operates
a relay. A command for the other channel first releases this card's video and
microphone; another user's incompatible session is not forcibly disconnected.

## Hardware test

1. Check all six names, video switching and audible sound on both entries.
2. Enable only **Strike 2 trial**; confirm `(2, 1)`. Press once, verify that only
   that pedestrian gate reacts and download diagnostics immediately.
3. Separately enable **Gate 2 trial**; confirm `(2, 2)`. Press once, verify all
   other relays remain inactive and download fresh diagnostics.
4. Check the primary controls still target their own relays. Do not repeat an
   ambiguous secondary result; report it and stop physical tests.
5. Close HA video and confirm Philips Door Connect can regain access.

## Doorbell observation

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

Channel 2 microphone remains unavailable. Its separate SDK connection still
lacks proven destination selection. Native alarm listening was traced further,
but its own login/subscription is not established for the current A331 CGI/QV
path. See [SDK evidence and exact blockers](connect3-rc1-sdk-evidence.md).

## Install / rollback

Enable prereleases in HACS, install **v0.4.4-rc.1**, restart HA and fully reload
the browser/Companion frontend. Existing entries, camera IDs, credentials and
TLS approvals are retained. No real device is contacted by automated tests.

Rollback: disable both secondary output trials, select **v0.4.4-beta.9** in HACS,
restart and reload. Do not delete entries. Beta.9 does not load the secondary
output buttons and ignores the new presentation fields. Stable **v0.4.3** is
unchanged. V1/R001/R002 keep their existing physical-output behavior.
