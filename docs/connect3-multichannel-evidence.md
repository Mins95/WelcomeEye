# Connect 3 channel selection — beta 9

Static evidence from Philips Door Connect 1.0.123.3(2), rechecked against the
local DEX/native disassembly. Addresses below are code-item/native addresses,
not device endpoints. This does not establish a physical panel mapping on
every firmware.

| Path | Evidence | Consequence |
| --- | --- | --- |
| Live selector | `PreviewModel.play`, classes4 `0x19a3d0`, reads `Channel.channelNum`. Native `CQUIIStreamLive.OnSendPlay` `0x4a8384` writes channel at byte 13 and stream at byte 16. | A second camera can use PLAY channel 2 / stream 1 on the same endpoint. |
| App channel list | `Device.getChannelList`, classes4 `0x1c14cc`, reads the app database and falls back to a list containing channel 1. `getChannelNum` `0x1c0790` returns that list's size. | An app count can be stored configuration; it is not necessarily fresh hardware discovery. |
| LAN advertised count | `Java_com_quvii_p2pv2_QvP2PV2Api_lanSearchDeviceGet` `0x29bce4` reads decoded record offset `0x1a4`, normalized to at least 1, into `QvLanSearchInfo.channel`. | This unauthenticated advertisement is a reported channel count, not proof that two outdoor panels exist or that PLAY 2 is available. |
| CGI reported count | `HttpDeviceManager.getChannelNumNew`, classes4 `0x243c7c`; `DeviceRequestHelp.getDeviceSystemInfo` `0x2313d4` builds `get.system.info`. Response consumers `l4` `0x24b998` and `M2` `0x24ad14` read `body/content/system/info/channelnum`; the SDK has alternate CGI retries and a fallback of 1. | There is a local read path in the SDK, but support and the count's physical meaning are not established for A331. Beta 9 adds no automatic request or retry for it. |
| Normal microphone | `PreviewModel.talkStart` invokes the no-channel `startSendTalkData(listener)` overload. `QvPlayerCore.buildTalkConnection()` `0x28cb80` and `startSendTalkData(listener)` `0x28ea84` use channel 65535. | Do not assume microphone channel 2 from the live-video selector. |
| Other microphone branch | `QvPlayerCore.vsuTalkStart` `0x28efb8` can choose `mChannelNo` for a non-default mode, called from `PreviewModel.xvrTalkSwitchStart`. | This XVR route is not established as the Philips intercom route. Channel-2 microphone stays unavailable. |
| Outputs | The established `DeviceOrderHelper.SendUnlockData` / transparent order 4 carries the output and a separate channel byte; see [output evidence](connect3-control-evidence.md). | Existing controls remain bound to channel 1. No channel-2 output mapping is inferred. |

Beta 9 enables the second camera through the explicit second-panel option,
or a previous successful channel-2 decoded stream under the same connection
profile. Changing host, ports, transport or certificate pins invalidates that
saved observation. Disabling the option always wins.

Video and downstream audio share the selected channel's existing QV session.
Channels have separate listeners and diagnostics; only one channel owns the
device media session at a time. `selected_channel` means an accepted PLAY
selector followed by decoded video, not physical identification of a panel.

## Legacy WelcomeEye V1 / Connect 2 R001

The WelcomeEye APK's `CLPreviewModel.java:59` supplies the selected
`PlayerItem.getChannel().getChannelNum()` to `QvLtPlayerCore.setChannel`.
`QvLtPlayerCore.startPlaying`, line 623, creates its live channel with
`setMetaData(..., channel + 15, 1, 2)`. Thus logical channel 2 uses **wire
channel 17 / stream 1 / mode 2**. The separate additional-camera selector 18
corresponds to logical channel 3 and is not substituted here.

The opt-in second-panel camera uses the same hub, credentials and sole media
worker with that fixed profile. It has no automatic profile fallback/retry,
separate video/audio listeners and a separate cached image. Main snapshots,
microphone and output commands cannot borrow the second-panel session.
Existing main-camera profiles and V1 cleanup remain unchanged by default.
Selecting channel 17 does not prove the physical image mapping on untested
hardware; the tester still needs to identify the scene.
