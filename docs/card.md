# WelcomeEye card and HomeKit camera source

Install 0.4.5, restart HA and reload the frontend. Add **WelcomeEye — Interphone** from the card picker, or use your actual camera entity:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

With **Second outdoor panel** enabled, the same card finds the second camera through integration metadata. Its graphical editor offers six independent names: `channel_1_name`, `channel_2_name`, `strike_1_name`, `strike_2_name`, `gate_1_name`, `gate_2_name`. Each supports up to 64 characters; names never alter entity IDs or physical routes.

Start the video deliberately. Sound is initially muted; enable it or the microphone when wanted. Changing sources closes the old media/microphone first. The card has no automatic playback or source switch on a bell event. All enabled opening commands stay available under either camera view, with fixed targets. The status line omits an entrance name on a single-source card and uses your custom entrance name on a two-source card.

The **Photo** button saves a fresh image. Connect 3/R002 require the selected live stream already open. Manual and automatic ring photos have separate latest-image entities. [Photo guide](captures.md).

HA-managed resources update automatically. For YAML-managed resources, maintain one JavaScript module entry:

```yaml
resources:
  - url: /welcomeeye_local/welcomeeye-card.js?v=0.4.5
    type: module
```

Use trusted HTTPS for the microphone, including in Companion, and grant browser/app permission. Closing or hiding the player stops its microphone. Do not leave another incompatible player open in Philips.

## HomeKit Bridge

The standard HA camera source provides on-demand local H.264 video and AAC sound to compatible HA consumers such as HomeKit Bridge, across supported WelcomeEye families. The source uses the same managed device session and releases its media lease when the consumer disconnects. It does not start a permanent camera stream simply because the integration loads.

Expose the existing camera using HA's HomeKit Bridge configuration. Do not copy the private loopback stream URL into another machine or a separate Homebridge container: it is scoped to the HA runtime and can change. Use the HA camera entity as the source. The custom card remains the interface for WelcomeEye microphone and physical controls.

Avoid **Preload stream** and continuous consumers if Philips must regain access. A consumer that keeps reading keeps its media lease; a HomeKit thumbnail request can also acquire an image and affect an ongoing monitor call. Close the HomeKit viewer before using Philips. This export does not provide automatic recording or guarantee every HomeKit client behavior.
