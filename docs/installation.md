# WelcomeEye 0.4.4 — quick start

1. Install or update **Philips WelcomeEye 0.4.4** in HACS, restart Home Assistant, then reload the dashboard.
2. Keep existing entries and use **Reconfigure**. For a new installation, open **Settings → Devices & services → Add integration → Philips WelcomeEye**.
3. Select the model and enter the requested local credentials. Connect 3 retrieves certificates automatically; approve the device when prompted. A reachable IP works across routed VLANs without mandatory broadcast discovery.
4. Enable **Second outdoor panel** only when installed. Select each required strike or gate under **Opening commands**. The opening code is separate from the local connection password and cloud account.
5. Add the **WelcomeEye — Interphone** card through the dashboard editor and select the device’s actual camera entity.

```yaml
type: custom:welcomeeye-card
entity: camera.your_camera
name: WelcomeEye
```

Replace `camera.your_camera` with the entity offered by HA. The card locates the associated second camera automatically. Its graphical editor lets you rename both entrances, strikes and gates. Every enabled opening button remains visible under both camera views and retains its fixed physical target.

**Language:** Reconfigure → WelcomeEye language offers French, English and Spanish for the card and its messages. HA forms follow your Home Assistant profile language.

**Doorbell:** Connect 2 R001 uses local detection. V1 retains its existing cloud option. For Connect 3/R002, choose **Cloud doorbell notifications → Configure**, enter the official app account, then select the correct account device. This is not the local connection password. [Cloud setup and test](qv-cloud-doorbell.md).

**Photos:** browse saved images in **Media → WelcomeEye**. Administrators use the **Photos WelcomeEye** sidebar to delete a selection, a day, a device’s photos or empty folders, after confirmation. **Reconfigure → Photo management** offers unlimited retention (default), 7, 30 or 90 days. Existing storage paths and files are retained.

**Known limits:** automatic V1/R001 snapshots can stop the monitor’s ringing after about five seconds; phone notifications continue. New Connect 3/R002 cloud delivery still needs physical confirmation. These cloud notifications never start video or snapshots. Secondary physical targets need confirmation on each installation.

Close HA video before opening Philips. Microphone access requires HTTPS and browser or Companion permission.

[Compatibility](../README.md#-supported-devices) · [0.4.4 notes and rollback](release-044.md)
