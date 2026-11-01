# 0.4.6-beta.2 — cloud connection and subscription status

This prerelease adds two diagnostic entities for **Connect V1, Connect 2 R002 and Connect 3**, using their existing cloud listeners. It includes all beta 1 card improvements. Stable 0.4.5 is unchanged.

## Install and check

1. Select **v0.4.6-beta.2** in the Philips WelcomeEye HACS version dialog with prereleases enabled, then download and restart Home Assistant. Keep your existing devices and cloud configuration.
2. Open **Settings → Devices & services → Philips WelcomeEye → your device → Diagnostic**.
3. Check **Cloud connection / Connexion cloud** and **Doorbell subscription / Abonnement au visiophone**. When reception is ready, expect Connected and Confirmed. Cloud disabled remains Disconnected / Disabled; these entities never enable it automatically.
4. If investigating a missed ring, note these states and the time of the press, then download the integration diagnostics immediately. Also report whether the Philips application received the call.

**Cloud connection** follows the existing receiver readiness, independently of the local video connection. A detected cloud disconnect stays visible as Disconnected rather than hiding the entity as unavailable. Entities become unavailable when the integration is unloaded.

**Doorbell subscription** reports the last known subscription result: not started, subscribing, accepted pending verification, confirmed, unverified, mismatch or pending unsubscription. It is not a continuous server-side check. Confirmed can coexist with Disconnected and does not prove that every button press reached HA.

There are no additional cloud requests, permanent video sessions or automatic photos from these entities. Names and states are translated into English, French and Spanish. The two entities are per device, shared by its entrances; existing ring sensors remain separate.

## Card and rollback

The [card editor and YAML guide](card.md#change-the-theme-or-use-compact-mode) explains WelcomeEye / HA theme appearance, full / compact layout and Browser Mod popups. Sound and microphone remain independent.

To return to the previous behavior, install **v0.4.6-beta.1** or **v0.4.5** through HACS, restart HA and reload the frontend. Keep existing entries. The new diagnostic entities may remain unavailable in the registry after rollback; they do not affect the cameras or ring sensors.
