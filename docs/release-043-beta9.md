# 0.4.3-beta.9 — Connect 3 media parsing

> Historical record: see the [current 0.4.3 guide](../README.md) for support, setup and known limitations.

Based on beta.8 (`793bb8e`). Stable remains **0.4.2**.

## Fix and evidence

The [first beta.8 hardware test](https://github.com/Mins95/WelcomeEye/issues/1#issuecomment-5995545065)
accepted authentication, pinned TLS, setup and play, then stopped at
`media_offset`. The old diagnostic did not retain the rejected header's lengths.
Zero accepted media packets did not mean that no media header had arrived.

Rechecking the official Door Connect APK showed that `PackFrame` restores the
decrypted extension **in place**, then reads from `body + offset`. It does not
require the offset to follow the entire extension. Beta.9 follows these native
operations, with separate bounds on extension length and media offset. It does
not add the extension length to the offset or change the keys, cipher, stream
profile, TLS verification, commands or connection lifecycle.

Evidence in the previously identified ARM64 `liblive_player.so`:
`0x4a5d94–0x4a5ddc` copies the decrypted extension back;
`0x4a5e48–0x4a5e50` and `0x4a5f0c–0x4a5f14` select `message + 32 + offset`.
Independent synthetic fixtures reproduce the old rejection and exercise the
corrected overlap. They are not the tester's packet. The exact beta.8 hardware
header remains unknown; moving video with beta.9 is not yet hardware-validated.

## Receive diagnostics

The downloaded Connect 3 `media` section now distinguishes received headers,
rejected headers and accepted media packets. Rejected headers are counted even
when their body is intentionally not read. `bytes_received` includes consumed
setup bytes and rejected headers, plus partial bytes returned at EOF. It is a
counter of application bytes consumed after TLS (which may still be encrypted
by the media protocol), not an Ethernet/TLS traffic measurement.
`messages_received` counts complete messages, including setup; its definition
therefore differs from beta.8.

| Field | Meaning |
| --- | --- |
| `headers_received` | Complete 32-byte prefixes, including setup and rejected prefixes |
| `media_headers_received` | Prefixes identified as media, even if rejected |
| `media_headers_rejected` | Media prefixes rejected before reading their body |
| `media_packets_accepted` / `media_packets` | Decoded outer media packets accepted after play; not decoded video frames |
| `media_packets_rejected` | Media header/body/protocol failures; interrupted reads are not automatically protocol errors |
| `last_receive_stage` | Read/decode stage or rejection location |
| `last_media_header` / `last_rejected_media_header` | Allowlisted layout metadata; the latter also includes `rejection_reason` |

The last media header and last rejected media header retain only allowlisted
protocol metadata: media command, body length, extension length, media offset,
encryption flag, validation booleans and fixed rejection reasons. They contain
no raw prefix, payload, image, device address, timestamp, UID, password or key.
An accepted media packet still does not prove that an H264 image was decoded:
check `decoded_frames` and `media_received` separately.

No extra request, retry, connection or background probe is added. R001/V1,
R002, UDT, ring photos, physical controls and the CRC32C dependency are unchanged.

## Test once

1. Install **0.4.3-beta.9** in HACS and restart HA. Keep the existing Connect 3
   entry, password, certificate pin and Experimental live video setting.
2. Close Philips Door Connect. Open the HA camera once.
3. If it fails, download fresh integration diagnostics before retrying. If it
   works, confirm that the picture moves, close with X and check Philips video.
4. Share the diagnostics and outcome. No microphone, ring or output test is
   needed. Review the file before posting.

Rollback: close the player, redownload **0.4.3-beta.8** in HACS and restart HA.
No entry removal, password/pin change or device reset is needed.
