# Explicit legacy SDK observations (beta.8)

These are administrator-triggered experiments, not new device capabilities.
They do not run at startup, after a media error or in ordinary camera use.
Each requires `confirm: true`, an identified administrator and control permission
on the targeted legacy camera. R002 and Connect 3 entries are not targets for
these legacy actions. An active media consumer, snapshot, control command or
ring listener returns `busy`; the listener is never paused or reconnected to
make an experiment possible.

The firmware and additional-camera observations retain their beta.7 protocol
and budgets. The new UDP action performs only a bounded SCT connection
handshake. It does not send credentials, OWSP, Start AV, output commands, cloud
requests or port scans. Software tests use synthetic local peers; physical
validation of this new handshake remains pending.

## Original APK evidence

The following evidence is from the ARM32 `libglnkio.so` extracted from the
official WelcomeEye APK 6.1.58.24. Its SHA256 is:

`25063e0678644f907df3d7706d1968004c5e94114f31e9b8161d6e917d418992`.

Addresses below are ELF virtual addresses, with the Thumb bit removed. They
identify inspected instructions, not a claim that every firmware implements
every SDK path.

### Firmware 469 / response 470: unchanged

The Java SDK sends request 469 with four zero bytes. The native
`DataChannel::sendData2` at `0x8902c` first calls `packetOWSP`, then passes the
**complete** OWSP pointer and length into `packetPriProReqData` at `0x8911e`.
Instructions `0x8910e` and `0x89110` load the buffer pointer and its full size;
there is no pointer adjustment by eight bytes and no length subtraction.

`packetOWSP` at `0x8ea54` constructs the big-endian length, sequence and TLV.
`packetPriProReqData` at `0x904cc` adds the private timestamp before that complete
inner packet. Thus the inner query remains:

```text
0000000c 00000000 d5010400 00000000
length   sequence TLV469   four reserved zero bytes
```

The existing authenticated private builder wraps this as request 509. On
response 510, `parsePriProRspData` at `0x907ec` removes only the private
timestamp. The native parser subsequently reads the inner TLV at offset eight.
Only the corresponding inner response 470 is accepted by this experiment.
This evidence does not justify removing the inner OWSP envelope.

A specifically authorized local hardware check of this unchanged query
succeeded on 2026-10-05, with the HA entry disabled and its workers absent.
The report recorded 172 ms, login accepted, response 470 correlated, and one
allowlisted metadata field. Request counts were 40:1, 501:1, 509:1 and 5005:1;
session stop was sent, TCP was closed and cleanup reported no errors. No media
session or TLV 505 was sent. The HA entry was reactivated afterwards. This
validates this query on that device only, not every firmware or UDP transport.

### Additional camera 18 / selector 0x12: candidate

The APK maps live channel to channel + 15. SDK camera ID 3 therefore motivates
the explicit candidate login channel 18, stream 1, mode 2 and Start AV selector
`0x12`. This does not change the normal V1 profile 16/1/2. Receiving or decoding
a frame alone cannot confirm which physical camera produced it.

The beta.7 cleanup, memory limits and packet allowlist remain in force: one
login, one Start AV, bounded decoding, Stop AV 5009, native session stop 5005
and TCP close. Neither this action nor the firmware query sends TLV 505.

## Discovery UDP port to the native LAN transport

This is a traced data flow, not a port inferred from a class name:

1. `LanSearchIndepHandler::parse` (`0x97848`) reads little-endian ports at
   decrypted discovery offsets 32 and 34. The protected branch loads them at
   `0x979ae` and `0x979aa`; the plaintext branch uses packet offsets 36 and 38.
2. `LanDevice::setParams` (`0x69320`) stores the first port at object offset 8
   and the second at offset 10.
3. `GlnkDevice::getLanDeviceAddr` (`0x69c30`, selection at `0x69c64`–`0x69c6c`)
   selects offset 8 for `ConnectMode == 2`; other inspected LAN mode uses the
   TCP port at offset 10.
4. `DataChannel::onSpecifiedMode` (`0x89810`) checks the known LAN device,
   obtains that address/port (`0x89886`) and passes them to the channel's
   `setFlowConnectMode` (`0x8989c`). The virtual-table entry resolves to
   `ConnChannelPeer::setFlowConnectMode` (`0x8d3f4`).
5. That setter stores the port at channel offset `0x30` and address at `0x32`.
   `ConnChannelPeer::openChnConnection` (`0x8d478`), mode-2 branch
   `0x8d58e`–`0x8d5b0`, passes those fields to `UdtConnection`.
6. `UdtConnection::openConnection` (`0x6f4f6`) uses the stored remote address
   and port when calling `SCTConnectNoBlock` (`0x6f562`).

The first discovery port is therefore the remote UDP endpoint for this native
LAN path. The UDP 1500 discovery service itself, QV UDP 5000 discovery, local
bound source port and cloud/punch endpoints are different concepts.
`getFwdUdtInfo` and `getLocalPort` alone do not establish a remote endpoint.

For this beta, the action still requires an explicitly supplied, independently
established `udp_port`. It does **not** perform UDP 1500 discovery, automatically
use a cached port, guess a missing port or switch normal media to UDP.

## Native SCT handshake

`UdtConnection` calls the SCT implementation defined in this same library;
the connection is not established merely by finding a UDT constructor symbol.
The inspected exchange is a UDT4-shaped **64-byte** control handshake:

| Offset | Native value / meaning |
| --- | --- |
| 0 | control word `0x80000000` |
| 4, 8 | zero additional information / timestamp in this handshake |
| 12 | destination socket ID, initially zero |
| 16 | version 4 |
| 20 | socket type 1 |
| 24 | client's initial sequence number |
| 28 | offered MSS 1386 |
| 32 | offered window 1000 |
| 36 | request 1 initially, then -1 |
| 40 | client socket ID |
| 44 | cookie zero initially, then the observed server cookie |
| 48–63 | address field supplied by the native socket state |

`socketpatrol` constructs this packet at `0xa72dc`–`0xa7358`; `sendctrl`
(`0xa38a8`) serializes its 32-bit words in network byte order. Initial state
`0xb` produces request 1 and cookie zero. After the correlated server challenge,
state `0xc` produces request -1 with the returned cookie. A correlated final
response completes the connection; the probe does not equate an arbitrary UDP
reply with successful authentication.

The parameters are established independently: `UdtConnection::openConnection`
calls `SCTSetOption(3, 1400)`. `SCTSetOption` at `0xa3358`–`0xa3360` stores
1400 minus 14, yielding MSS 1386. `newsctsocket` initializes window 1000 at
`0xa2e24`–`0xa2e2c`. `sl_connect` copies both values into the handshake state
at `0xa3858`–`0xa3862`. The wrapper reads via `SCTRecvTimeOut` (`0x6f588`)
and writes via `SCTSendTimeOut` (`0x6f5a8`, trampoline `0x15d414` to
`0x161440`), rather than `SCTDiscreteSend`. This is the SDK's byte-stream API;
it does not prove complete interoperability with every standard UDT4 stack.

The native close path constructs a **20-byte** control packet with word
`0x80050000`, the peer socket ID and a zero four-byte body
(`0xa6df8`–`0xa6e40`). It sends that same shutdown packet three times. The
diagnostic deliberately attempts at most **one** such cleanup send to keep its
network budget bounded. A cleanup send is recorded as an attempt; it is not
reported as proof of physical device release.

## Explicit beta.8 action

`welcomeeye_local.experimental_udt_probe` targets one legacy `camera` entity.
It requires all three inputs:

```yaml
action: welcomeeye_local.experimental_udt_probe
target:
  entity_id: camera.YOUR_LEGACY_WELCOMEEYE
data:
  confirm: true
  legacy_discovery_absent: true
  udp_port: 12345 # Replace with the independently established device UDP port.
```

`legacy_discovery_absent` confirms an observed absence or unusable result of
UDP 1500 discovery; it is not inferred from the model. Do not enter 1500 or
5000 merely because those ports are known discovery services.

The probe allows one attempted handshake per loaded entry and a global
five-second budget: one induction request, one conclusion request after the
correlated challenge, and at most one shutdown datagram after a correlated
final response. No handshake request is retransmitted.
It uses one UDP socket, validates the remote peer and correlates the response
socket/sequence fields. There is no retransmission loop. Cancellation and unload
finish socket cleanup before releasing the existing coordination locks.
An error or a partial send consumes that attempt; it never causes an automatic
retry or a login.

Send attempts are counted before socket writes; successful sends have a
separate counter. The explicit report contains stages, statuses, sizes, counts, validation
booleans and elapsed times. It contains no endpoint address, port, raw packet,
socket identifier, sequence value, cookie, UID or credentials. No packet or
ambiguous field is persisted in ordinary integration diagnostics, entity
attributes, logs, configuration options or files. Home Assistant may retain an
explicit service response in a script trace.

## Validation limits

The parser's MSS/window limits are conservative probe policy. They are not a
claim to reproduce every permissive branch of the SDK's full transport.

The tests exercise synthetic local packets, peer/correlation rejection,
malformed/oversized/truncated input, timeouts, opt-in and permissions, busy and
concurrent calls, single attempts, cancellation and privacy. They validate the
software boundary, not a real intercom. No cloud service or physical output was
contacted during these tests. The UDP probe was not run on the local device:
legacy discovery works there, so its explicit discovery-absence guard was not
satisfied. Handshake availability, subsequent authenticated
OWSP transport and device release remain separate hardware questions. This
release implements none of the latter two and does not advertise new UDP media
or physical-control support.
