# 0.4.4-rc.5 — confirmed media and controls

RC5 is a release candidate; **0.4.3 remains the published stable release**.
Validated media and opening features now use normal labels in French, English
and Spanish. Internal configuration keys, entry/unique IDs, custom names,
credentials and saved approvals are retained.

R002 video, sound, microphone and openings were confirmed on hardware, including
[the RC4 report of 16 October](https://github.com/Mins95/WelcomeEye/issues/7#issuecomment-6216554333).
The tested Connect 3 A331 installation has confirmed both video sources and
primary sound, microphone and openings. These reports do not establish every
secondary speaker destination or physical relay mapping on another installation.

## Cloud doorbells

Connect 3 and R002 can enroll an independent Home Assistant notification client
from **Reconfigure → Cloud doorbell notifications**. Select the app account's
authorized device explicitly; local intercom credentials are not cloud credentials.
See the [short configuration and test guide](qv-cloud-doorbell.md).

Each configured entrance has its own doorbell sensor. Fresh outdoor-call messages
are device/channel checked and deduplicated before an event; no camera or snapshot
is started. Disable/removal only cleans HA's own subscription. Existing phone
tokens are never copied or replaced.

The Connect 3 native TCP listening diagnostic received heartbeats without a proven
bell notification. Cloud delivery and bell 1/2 mapping still need the first
hardware check; no heartbeat or arbitrary notification is emitted as an HA ring.
Secondary microphone routing still requires an explicit, observed test.

The existing V1 cloud route and Connect 2 R001 local bell route keep their current
behavior. Automatic photos retain the known possibility of stopping the monitor's
ringing around five seconds after a press. Disable ring capture if needed.

## Upgrade and rollback

1. Enable prereleases in HACS, redownload **0.4.4-rc.5**, restart Home Assistant
   and fully reload the dashboard. Keep existing entries and credentials.
2. YAML-managed card resources use
   `/welcomeeye_local/welcomeeye-card.js?v=0.4.4-rc.5`.
3. Confirm the existing camera, custom labels and selected opening commands.
   Each output keeps its fixed target under both camera views. Saving settings
   does not start media, microphone or a physical opening.

TCP/certificate consent, separate opening codes, secondary target confirmations
and single-attempt controls remain required. A changed target still requires
fresh approval. An uncertain physical result is never automatically retried.

To roll back, disable cloud notifications first, redownload **0.4.4-rc.4**,
restart HA and reload the dashboard.
Keep the entry; existing identity, media configuration and approvals remain valid.
RC4 restores its former display wording. Do not use stable 0.4.3 as a direct
rollback for a newer TCP-selected Connect 3 entry without disabling that entry.
