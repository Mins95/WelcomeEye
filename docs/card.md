# WelcomeEye card and HomeKit camera source

[Documentation](README.md) · [Installation](installation.md) · [Settings](settings.md)

## Add the card

Install WelcomeEye, restart HA and reload the frontend. The presentation, saved-photo history and playback options below require **0.4.6-beta.1 or later**; 0.4.5 remains the stable release. In **Edit dashboard → Add card**, select **WelcomeEye — Interphone** and choose your primary camera. You can also use a Manual card with your actual camera entity:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

With **Second outdoor panel** enabled in Reconfigure, the same card finds the second camera through integration metadata. There is no required `channel_2_entity` field and no need to create a second card. On a single-panel installation, no source selector is shown.

## Change the theme or use compact mode

These are **card settings**, not integration Reconfigure settings:

1. Open your dashboard and choose **Edit dashboard**.
2. Select **Edit** on the WelcomeEye card.
3. Set **Appearance / Apparence** to **WelcomeEye** for its teal styling, or **Home Assistant theme / Thème Home Assistant** to inherit the colors of your active HA theme.
4. Set **Layout / Présentation** to **Full / Complète** or **Compact / Compacte**. This is independent of the appearance choice.
5. Save the card and dashboard.

The theme option follows the current HA theme; it does not install a theme or provide a list of named themes. Change the active HA theme in your Home Assistant profile if you want different inherited colors. Full and compact layouts both keep Sound and Microphone as separate controls.

For YAML, add these fields directly to the WelcomeEye card, at the same indentation as `entity`:

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
layout: compact
appearance: theme
```

Use `layout: full` for the full layout and `appearance: welcomeeye` for the original colors. Existing cards default to full/WelcomeEye; their configuration is not rewritten.

### Cards inside stacks and Browser Mod popups

For a `vertical-stack` or `grid`, edit the nested `custom:welcomeeye-card`, not the stack. If the graphical editor does not reach the nested card, switch that parent card to its YAML editor.

If your existing Browser Mod popup creates its content with JavaScript, place the same options **inside the object passed to `popup`**. JavaScript uses quoted values and commas:

```javascript
await popup({
  type: "custom:welcomeeye-card",
  entity: "camera.your_camera",
  name: "WelcomeEye",
  layout: "compact",
  appearance: "theme",
  channel_1_name: "Main entrance",
  channel_2_name: "Side entrance",
  strike_1_name: "House wicket gate",
  strike_2_name: "Office wicket gate",
  gate_1_name: "Driveway gate",
  gate_2_name: "Garage gate"
});
```

This is the content block for an existing popup helper, not a complete Browser Mod installation. Keep its existing wrapper, camera entity and custom labels. A `layout: horizontal` on the Mushroom button that opens the popup controls that launcher only. Save, then close and reopen the popup to apply the WelcomeEye options.

## Presentation and playback options

All these options are available in the card's graphical editor and YAML:

| YAML field | Default | Choices / effect |
| --- | --- | --- |
| `layout` | `full` | `full` or `compact`; changes spacing and presentation, not device resolution. |
| `appearance` | `welcomeeye` | `welcomeeye` or `theme`; card colors or the active HA theme. |
| `show_last_photo` | `true` | Show the most recent saved photo for the selected entrance behind the play button. |
| `show_history` | `true` | Show the History button and up to 12 saved photos for this entrance. |
| `autoplay` | `false` | Start live video when the card becomes visible. Starts muted, with the microphone OFF. |
| `auto_close` | `0` | `0` (disabled), `30`, `60` or `120` seconds after the live connection starts. |

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
layout: compact
appearance: theme
show_last_photo: true
show_history: true
autoplay: false
auto_close: 60
```

**Autoplay opens an actual device media session.** It can interrupt monitor ringing and occupy the connection needed by Philips. Leave it OFF unless wanted. The editor preview never starts it; manually closing video does not immediately reopen it. Automatic closing stops the player and microphone. In HLS fallback, the upstream release remains managed by HA. No setting enables the microphone automatically.

## Saved-photo preview and history

The background is a **dated saved image**, not a live thumbnail. Reading it or opening **History / Historique** does not start a camera stream or take another photo. History shows the selected entrance's latest 12 accessible photos; click a thumbnail to enlarge it, use Refresh to update the list, or follow the Media link for older photos.

An administrator can browse this device's saved history. Other users need permission to read the camera and the relevant latest-image entities; history is filtered accordingly. If no accessible saved photo exists, the card keeps its ordinary empty player. Deletion remains in the separate administrator photo library.

The **Photo** button still requests a new capture and retains its existing media requirements and known call-interruption behavior.

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

Start the video with Play, unless you have explicitly enabled autoplay. **Sound and Microphone are independent:** listening never turns on the microphone, and the microphone always needs its own deliberate click. Sound starts muted. Changing sources closes the old media/microphone first. A bell event does not automatically switch sources or open this card. All enabled opening commands stay available under either camera view, with fixed targets. The status line omits an entrance name on a single-source card and uses your custom entrance name on a two-source card. A video retry button retries the video connection only, never a physical opening command.

The **Photo** button saves a fresh image. Connect 3/R002 require the selected live stream already open. Manual and automatic ring photos have separate latest-image entities. [Photo guide](captures.md).

## Card resource and updates

HA-managed resources update automatically. If the editor or labels look outdated, fully reload the dashboard or Companion frontend after restarting HA. Avoid adding a second resource for the same card.

For YAML-managed dashboard resources, maintain one JavaScript module entry in the existing Lovelace resource configuration (not in an individual card):

```yaml
resources:
  - url: /welcomeeye_local/welcomeeye-card.js?v=0.4.6-beta.1
    type: module
```

Use trusted HTTPS for the microphone, including in Companion, and grant browser/app permission. Closing or hiding the player stops its microphone. Do not leave another incompatible player open in Philips.

If the new appearance/layout fields are missing, first check that 0.4.6-beta.1 is installed, restart HA, then reload the dashboard completely (Ctrl+F5 on desktop, or fully reload the Companion frontend). A Browser Mod popup must also be closed and reopened. Keep only one card resource.

## HomeKit Bridge

The standard HA camera source provides on-demand local H.264 video and AAC sound to compatible HA consumers such as HomeKit Bridge, across supported WelcomeEye families. The source uses the same managed device session and releases its media lease when the consumer disconnects. It does not start a permanent camera stream simply because the integration loads.

Add **HomeKit Bridge** from **Settings → Devices & services → Add integration**, then expose the existing camera entity. In YAML-managed HomeKit configuration, the camera's `entity_config` can enable `support_audio: true` and link its actual bell sensor with `linked_doorbell_sensor`. Follow the [official HomeKit Bridge guide](https://www.home-assistant.io/integrations/homekit/) for pairing, filtering and network requirements; use **HomeKit Bridge**, not HomeKit Device.

Do not copy the private loopback stream URL into another machine or a separate Homebridge container: it is scoped to the HA runtime and can change. Use the HA camera entity as the source. The custom card remains the interface for WelcomeEye microphone and physical controls.

Avoid **Preload stream** and continuous consumers if Philips must regain access. A consumer that keeps reading keeps its media lease; a HomeKit thumbnail request can also acquire an image and affect an ongoing monitor call. Close the HomeKit viewer before using Philips. This export does not provide automatic recording or guarantee every HomeKit client behavior.
