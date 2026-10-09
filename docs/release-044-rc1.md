# v0.4.4-rc.1

- Six independent card labels: two entries, two strikes and two gates.
- Four explicit Connect 3 output routes. Secondary strike `(2,1)` and gate
  `(2,2)` each require a separate opt-in and remain physical trials.
- Automatic card refresh after a second-panel setting change or HA reconnect.
- Doorbell observation on either existing live channel, with relative manual
  markers and sanitized candidate diagnostics. No false HA ring or background
  video is started.
- Existing video/audio, TLS/TCP and primary controls retained. No guessed
  channel-2 microphone routing or independent alarm subscription.

Hardware confirmed: A331 channel-1 video, sound, microphone and outputs;
channel-2 video and card switching. Secondary audible sound and both secondary
relay mappings still need physical feedback. Software tests do not establish
these outcomes.

[Français : installation et essais](connect3-four-outputs.fr.md) ·
[English: installation and tests](connect3-four-outputs.md) ·
[SDK research](connect3-rc1-sdk-evidence.md)

Install with HACS prereleases enabled, restart HA and reload the frontend.
Keep entries and credentials. Roll back to **v0.4.4-beta.9** through HACS after
disabling secondary relay trials. Stable **v0.4.3** remains unchanged.
