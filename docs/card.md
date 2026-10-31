# WelcomeEye card and HomeKit camera source

[Documentation](README.md) · [Installation](installation.md) · [Settings](settings.md)

## Add the card

Install 0.4.5, restart HA and reload the frontend. In **Edit dashboard → Add card**, select **WelcomeEye — Interphone** and choose your primary camera. You can also use a Manual card with your actual camera entity:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

With **Second outdoor panel** enabled in Reconfigure, the same card finds the second camera through integration metadata. There is no required `channel_2_entity` field and no need to create a second card. On a single-panel installation, no source selector is shown.

## Rename the entrances and opening buttons

1. Open **Edit dashboard**, then **Edit** on the WelcomeEye card.
2. In its graphical editor, change the entrance and opening labels you need.
3. Save the card and dashboard. This changes presentation only; HA entity IDs and physical routes remain unchanged.

The six independent fields are `channel_1_name`, `channel_2_name`, `strike_1_name`, `strike_2_name`, `gate_1_name` and `gate_2_name`. Each supports up to 64 characters. Secondary fields are hidden without a second panel. Their values persist in the dashboard configuration through integration updates.

Example for two outdoor panels:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
channel_1_name: Main entrance
channel_2_name: Side entrance
strike_1_name: House wicket gate
strike_2_name: Office wicket gate
gate_1_name: Driveway gate
gate_2_name: Garage gate
```

Custom names do not enable an output. Choose the buttons to display under **Integration → Reconfigure → Opening commands**, and complete any required output authorization. All enabled commands appear under both video views, keeping their fixed physical targets. [Opening settings](settings.md#opening-commands).

## Video, sound, microphone and photos

Start the video deliberately. Sound is initially muted; enable it or the microphone when wanted. Changing sources closes the old media/microphone first. The card has no automatic playback or source switch on a bell event. All enabled opening commands stay available under either camera view, with fixed targets. The status line omits an entrance name on a single-source card and uses your custom entrance name on a two-source card.

The **Photo** button saves a fresh image. Connect 3/R002 require the selected live stream already open. Manual and automatic ring photos have separate latest-image entities. [Photo guide](captures.md).

## Card resource and updates

HA-managed resources update automatically. If the editor or labels look outdated, fully reload the dashboard or Companion frontend after restarting HA. Avoid adding a second resource for the same card.

For YAML-managed dashboard resources, maintain one JavaScript module entry in the existing Lovelace resource configuration (not in an individual card):

```yaml
resources:
  - url: /welcomeeye_local/welcomeeye-card.js?v=0.4.5
    type: module
```

Use trusted HTTPS for the microphone, including in Companion, and grant browser/app permission. Closing or hiding the player stops its microphone. Do not leave another incompatible player open in Philips.

## HomeKit Bridge

The standard HA camera source provides on-demand local H.264 video and AAC sound to compatible HA consumers such as HomeKit Bridge, across supported WelcomeEye families. The source uses the same managed device session and releases its media lease when the consumer disconnects. It does not start a permanent camera stream simply because the integration loads.

Add **HomeKit Bridge** from **Settings → Devices & services → Add integration**, then expose the existing camera entity. In YAML-managed HomeKit configuration, the camera's `entity_config` can enable `support_audio: true` and link its actual bell sensor with `linked_doorbell_sensor`. Follow the [official HomeKit Bridge guide](https://www.home-assistant.io/integrations/homekit/) for pairing, filtering and network requirements; use **HomeKit Bridge**, not HomeKit Device.

Do not copy the private loopback stream URL into another machine or a separate Homebridge container: it is scoped to the HA runtime and can change. Use the HA camera entity as the source. The custom card remains the interface for WelcomeEye microphone and physical controls.

Avoid **Preload stream** and continuous consumers if Philips must regain access. A consumer that keeps reading keeps its media lease; a HomeKit thumbnail request can also acquire an image and affect an ongoing monitor call. Close the HomeKit viewer before using Philips. This export does not provide automatic recording or guarantee every HomeKit client behavior.
