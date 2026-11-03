# 0.4.6-beta.3 — older Connect V1

This beta adds the unprotected LT variant reported by DES9900VDP / Connect V1
in issue #14. The APK has a separate login for this firmware. Previously setup
rejected its discovery response before sending a password.

## Install and test

1. In HACS, allow beta versions and select **v0.4.6-beta.3**, then restart Home Assistant.
2. Add **WelcomeEye Connect V1 / Connect 2 R001** with the device IP, username
   and **local connection password** used by the Philips app (not the cloud account).
3. If the older unprotected protocol is detected, review and approve its warning.
   The password is transmitted without encryption on the local network. Approval
   is private and bound to this address and device identifier. It does not prove
   device identity. Passwords exceeding the native 15-byte field are rejected,
   never silently truncated. No fallback changes an existing protected device.
4. Close the Philips live player. Test HA video, listening, then microphone separately.
   Closing the HA player must let Philips resume normally.
5. Test the portillon and gate individually, one voluntary click each, with the
   physical result visible. Do not repeat an ambiguous command. Native acceptance
   is not proof that a relay actually operated.
6. Test a manual photo. For ringing, enable the existing **V1 cloud doorbell**
   option in **Reconfigure**, then press once and check the cloud/subscription sensors.
   Automatic ring photos remain optional; their known monitor-call interruption
   still applies. No ring capture or output is triggered by installation.
7. Download integration diagnostics after the tests, including failures. Send
   the firmware displayed on the monitor too, if the automatic firmware read is
   unavailable. Do not share passwords, QR codes or device identifiers.

All V1 functions are available in this beta; their operation on this newly
supported older variant still requires physical confirmation. A second panel
keeps the existing separate opt-in. Stable 0.4.5 and protected V1/R001, R002 and
Connect 3 transport selection remain unchanged.

## Firmware and diagnostics

During approved legacy setup, one read-only firmware query is allowed for at
most two seconds / 16 receive frames. It starts no video and sends no relay
command. `device.firmware.fields` contains only the SDK's safe version fields
(`AppCom`, `SolCom`, `ReleaseTime`, `HDVersion`) when returned. `status` says
`received`, `not_reported`, `failed` or `response_limit`; no value is invented.
This is the firmware observed at setup, retained across restarts, not a live poll.

The transport diagnostics identify `legacy_plain`, login response 42, result
and reason, AV replies and existing media/microphone/output counters. They never
include credentials, the approval's address/UID or raw payloads.

## APK evidence and limits

APK 6.1.58.24 `QvLtPlayerCore.startPlaying` uses the raw local password for its
non-encrypted branch, with channel + 15, stream 1, mode 2. `GlnkLoginHelper`
`packLoginPacket` (0x8dcec) / `requestLogin` (0x8e198) build TLV 41;
`parseData` (0x8e762) reads result/reason from TLV 42.

`DataChannelIOCtrl.startGetVideoStream` (0x8f86e) and `stopGetVideoStream`
(0x8fb0c) use four-byte clear parameters for TLVs 5007/5009. Their reply parser
(0x91a12 / 0x91a3e) requires four-byte results. `sendTalkCmd` (0x8ed04) uses
331/332 through the direct `sendData2` branch (0x890a0). Old-device unlock in
`QvLtPlayerCore.unlock` uses `TCRequestBean.initUnlock` with 425/426, the existing
44-byte payload builder, and the current media session. Each action retains the
single writer, explicit output target, one-attempt limit and uncertain-result lock.
`IProtocolResponse` / `ProtocolTaskHelper` identify firmware query 469/470.

These are protocol evidence and local synthetic tests, not a claim of hardware
validation. No real device is contacted during automated checks.

## Roll back

Select v0.4.6-beta.2 or stable v0.4.5 in HACS and restart HA. Existing supported
devices retain their entries. Older unprotected devices will again be rejected
by those versions; no firmware or device settings have been changed.
