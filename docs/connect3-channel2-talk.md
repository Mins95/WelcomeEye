# Connect 3: voluntary secondary microphone trial

The secondary camera can expose a microphone after the separate
`experimental_channel2_microphone` opt-in. It follows the application's selected
live-player context with talk selector **65535**. It never invents talk selector
2, sends an opening command, changes a panel setting or switches cameras itself.
The physical destination must still be checked by the tester on both panels.

## Additional APK evidence

This review re-read the original Philips Door Connect `1.0.123.3(2)` DEX, rather
than treating the older WelcomeEye Java decompilation as Connect 3 evidence.
The following files match byte-for-byte those inside `com.extel.philipsdoorconnect.apk`:

- XAPK SHA256: `f38b6a783f57b06fab95f40a519ce2f14d006d7cd871e9c566528c2cedf09b3c`.
- `classes2.dex`: `9753902e6c3f8fed50e944bca55cbb0dfa44129ef9ee89a6979f20859bc38fd1`.
- `classes4.dex`: `f17451fb2e79312dd8157d9351fb81f34866b05798990904cb4f8ae2f33d724a`.

`PreviewPresenter.setCurrentFocus`, `classes4.dex`, code item `0x19ee54`,
stops the previously focused player's talk and calls `breakTalkConnection` at
`0x19eed8`. For the newly focused non-XVR player already in playback state 4,
it calls `buildTalkConnection()` at `0x19ef2a`. This path sends no FE order 9.
The no-argument builder in `classes2.dex` loads 65535 at `0x28cb90`.

This complements `PreviewModel.play` selecting the live channel before
`startPlay`, described in [the RC1 evidence](connect3-rc1-sdk-evidence.md).
`PreviewModel.talkStart` calls the no-channel overload, which loads 65535 at
`0x28ea94`. The explicit-start overload rebuilds a missing talk connection at
`0x28eb1a`/`0x28eb20`; automatic preconnection is therefore not required to start
talk after the video is already active.

Native `CQUIITalk::ParseURL`, `0x550868`, reads the URL selector into member
`+0x70c`; absent/zero selectors become 65535. No hidden replacement with the
selected live channel is added to our wire format. The existing setup/open,
codec negotiation, audio framing and cleanup are unchanged.

The distinct FE9 channel-switch branch requires the device's CallHold ability.
It is not sent speculatively and its reply is not treated as proof of a speaker
destination. Likewise, the XVR-specific `vsuTalkStart` is not applied to a
Connect 3 door panel.

## Software boundary and test

Starting this trial requires an existing, decoded channel-2 video with accepted
PLAY and verified CGI, the same live session, the parent's exclusive media lease,
an unchanged endpoint/pin profile and the explicit option. TCP also retains its
existing separate microphone/control approval and AES/SHA negotiation guards.
The talk socket uses that session's ephemeral credentials. No extra stream-key
query or video session is created. A replaced, closed or reconfigured context
cannot continue sending audio; closing video closes talk before releasing the
channel lease. The first channel and other models keep their existing behavior.

Synthetic tests cover TLS and TCP, selector 65535, no opening/FE9 command,
negative setup/authorization paths, configuration preservation, profile/session
replacement, video release, switching back to the primary camera and unload.
These prove the software sequence, not physical microphone routing.

The HA runtime checks also exercise the browser WebRTC microphone, channel-2
talk and image capture on the existing live session. One AAC encoder edge case
was reproduced with synthetic input: silence can produce a packet too short
for the negotiated encrypted prefix. Such nonempty AAC packets are counted in
`short_audio_packets_dropped` and skipped without padding or terminating talk;
the encoder clock still advances and a subsequent valid packet resumes sending.
This can omit a silence interval. Other invalid codecs and framing remain
errors, and the packet builder retains its original strict length checks.

For the voluntary hardware test: enable the second-panel microphone option in
Connect 3 reconfiguration → Advanced; open only the second camera, enable its
microphone briefly and check which outdoor speaker emits the voice. Stop the
microphone, close the video and check the first camera and Philips app. Collect
diagnostics whether the expected speaker works or not. Diagnostics distinguish
`requested_channel: 2`, `talk_selector: 65535`, protocol acceptance and audio
packet counts from `physical_route_verified: false`.
