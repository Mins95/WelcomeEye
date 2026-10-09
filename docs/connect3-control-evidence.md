# Connect 3 opening controls: original APK evidence

The implementation follows the live Preview control path of Philips Door
Connect 1.0.123.3(2). Primary-channel outputs have owner confirmation on A331;
the two secondary-channel outputs are opt-in RC1 trials, not hardware-confirmed.
A success response alone is not a physically verified activation. No device command was sent during
this analysis. The separate CGI `set.device.opendoor` path is not a fallback.

Source: classes2/classes4 DEX and ARM64 `liblive_player.so`, SHA256
`bb375c04df0f9b15407a8c158163ff121dc517be15ce55def0a4be2064019c68`.
Addresses below are DEX code-item/instruction offsets or native ELF addresses,
not fabricated Java line numbers. Tests use explicitly synthetic wire fixtures.

## User controls and code

`PreviewUnlockController` click handler assigns output 1 to `iv_unlock1`
(`0x1a9f80..0x1a9f94`) and output 2 to `iv_unlock2`
(`0x1a9fb8..0x1a9fca`). `PreviewHUnlockController` has the same assignments.
In `preview_item_preview_unlock.xml`, these IDs (`0x7f0902a2/3`) use
`selector_btn_unlock1/2` (`0x7f0803fb/fc`). Their unpressed resources are
`btn_unlock1/2` (`0x7f0800d7/d9`). Inspection of those original images confirms
the pedestrian door/strike and double-leaf gate icons respectively. Proprietary
images are not copied into this repository. HA output 0 therefore maps to
native output 1 (strike), and HA output 1 to native output 2 (gate).

`PreviewPresenter.unlock` (classes4 code item `0x19ff28`) accepts manual user
input or reads `Device.getUnlockPassword()` in direct mode. This is a separate
opening credential. The integration requires the owner to provide it explicitly
and never substitutes authCode. Outputs require the independent experimental
outputs option and enabled experimental video; both otherwise fail closed.

`PreviewPresenter.startUnlock` (`0x19f7b8`) passes current channel and output to
`PreviewModel.unlock` (`0x19b238`). The latter calls
`QvPlayerCore.unlock(channel, output, code)` at `0x19b25e`. RC1 rechecked the
presenter's `Device.getCurrentChannel()` call at `0x19f810`. The selected live
player carries the command. This supports explicit `(1,1)`, `(1,2)`, `(2,1)`
and `(2,2)` protocol candidates; it does not prove the physical secondary map.

`QvPlayerCore.unlock` (classes2 `0x28ef60`) calls `EncodeDevicePassword`
(`0x28ef70`) and `SendUnlockData(output, channel, true, encoded)` (`0x28ef7a`),
then sends order **4** (`0x28ef82`). `QvEncrypt.EncodeDevicePassword`
(`0x2f0988`) uses lowercase SHA256 of UTF-8 for nonempty strings shorter than
64 Java UTF-16 units; longer strings pass through. The existing normalized
credential function implements that transform, but receives the separate
opening code here.

`DeviceOrderHelper.SendUnlockData` (classes4 `0x21bbc8`) constructs:

| Payload offset | Meaning |
| --- | --- |
| 0 | Native output 1 or 2 |
| 1 | Zero |
| 2 | Channel, one byte |
| 3 | Boolean true, byte 1 |
| 4–15 | Twelve zero bytes |
| 16 onward | Encoded code bytes, without a terminating NUL |

## Existing live transport

`QvPlayerCore.sendDeviceOrder` (`0x28dcd8`) calls `transparentEx`
(`0x28eecc`). JNI `QvJniFunc_transparentEx` (`0x454ccc`, call `0x454d4c`)
invokes `QVPlayerTransparent(order, NULL, 0, payload, length)`.
`IQUIIStreamLive.Transparent` (`0x545530`) uses the existing stream;
`CQUIIStreamLive.Transparent` (`0x4a9410`) checks active state 4 and queues the
command. It does not open a second video connection or reader.

`CQUIIStreamLive.OnSendData` (`0x4a9838`) emits a 32-byte header:

| Header offset | Meaning |
| --- | --- |
| 0 | `0xFE`, transparent command |
| 1–8 | LE u64 Unix-seconds timestamp |
| 9–10 | LE u16 extension length |
| 11–12 | LE u16 payload length |
| 13 | Order 4 |
| 14–31 | Zero for this JNI path (first-data length is zero) |

Payload begins at packet offset 32. Existing negotiated SHA256 covers the plain
header plus payload; extension padding and independent header/body AES-CBC use
the already established session material and sixteen ASCII `0` IV bytes. No
cipher negotiation, password, nonce, profile or request is changed for outputs.

The Python controller acquires/releases a consumer of the existing shared media
manager. It uses the session's single reader and serializes writes with existing
keepalive/teardown writes. A previously open viewer retains its own consumer.

## Reply and safety boundary

`OnRecvCommand` (`0x4a464c`) validates/decrypts the transparent reply.
`OnRecvTransprent` (`0x4aa3b8`) emits callback signal `0x854`.
`QvCamera.callback` (`0x44c490..0x44c520`) extracts parameter length at header
offset 11, order at 13 and parameter bytes at packet offset 32. Therefore FE's
generic header `result/action` bytes are not treated as an opening result.

`QvPlayerCore.u` (classes2 `0x28b2e0`, order-4 branch at `0x28b5a8`) calls
`DeviceOrderHelper.ReceiveUnlockData` (classes4 `0x21bec4`):

- fewer than two parameter bytes: invalid response;
- parameter byte 0 equals zero: native result 0 (accepted);
- otherwise parameter byte 1 equals 2: native result -10029;
- otherwise: native result -1.

No unproven textual meaning is assigned to -10029. An accepted reply is a device
confirmation, not a gate-position sensor or proof of physical activation.

The observed response path exposes no request identifier or output correlation.
Consequently only one output can be pending on a session. Only an order-4 reply
received after its write attempt may satisfy it. Other/unsolicited replies are
not retained for future commands. The ACK timeout is 15 seconds, matching the
app's `PreviewPresenter.startUnlock` timeout (`0x19f828`).

The physical attempt counter advances **before** `writer.write()`. One action
contains one call to that writer and no loop/retry/reconnection. If timeout,
write/drain error, cancellation, malformed ACK or connection close occurs after
an attempt, the session is marked uncertain and rejects subsequent output
commands. A late response cannot satisfy another action on that session. Only a
new explicit media session resets that latch; it never replays the earlier
command. HA advises the user to inspect the hardware before another action.
Closing before the write rechecks the boundary under the shared write lock and
does not attempt an output. Unload drains the owned task and releases its lease.

Diagnostics contain fixed status/reason names, counters and the three native
result values only. They contain no opening code/hash, key, UID, address, raw
packet, timestamp, SDP or physical-position claim.

## Software tests and remaining validation

`test_connect3_control.py` independently builds synthetic expected FE/4 wire
bytes and checks code transform, both output mappings, negotiated crypto modes,
SHA integrity, ACK results, one pending action, late ACK isolation, timeout,
partial write/drain failure, cancellation, EOF and closing before a write.
It verifies shared media remains open for its original viewer, consumer cleanup
and absence of secrets in diagnostics. These are not real-device fixtures.

Secondary physical activation and the exact firmware's acceptance remain to be confirmed
by an owner performing explicit tests. No physical output is automatically
tested, retried, probed at setup, or inferred from a successful media session.

RC1 centralizes these four targets and gives the two secondary trials separate
consents. The parent controller serializes all outputs; secondary writes need
an action-bound grant for the exact child session, channel and output. The grant
is rechecked under the writer lock. Direct child/session calls, disabled targets
and changed credentials/configuration cannot bypass it. The original primary
output API and other protocol families retain their existing behavior.

[Physical test / FR](connect3-four-outputs.fr.md) · [Physical test / EN](connect3-four-outputs.md)
