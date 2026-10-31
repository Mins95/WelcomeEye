# Connect 3 — names, four outputs and doorbells (0.4.5)

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
existing media manager and single reader. Each secondary target has its own
explicit authorization. They use the device's separately configured
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

## Doorbells, microphone and photos

Use [cloud doorbells](qv-cloud-doorbell.md) for normal Connect 3 notifications. Reception is confirmed; each configured entrance has a sensor and event channel. The native local alarm action remains a separate diagnostic, not a standby bell detector.

The second microphone has its own activation; check speech on the selected speaker. Manual **Photo** requires the selected live stream. Optional automatic ring photos can interrupt the monitor's ringing. [Photos and retention](captures.md).

## Update and rollback

Install **0.4.5 stable** through HACS, restart HA and reload the frontend. Existing entries, camera IDs, credentials and approvals are retained. [Release and rollback](release-045.md).
