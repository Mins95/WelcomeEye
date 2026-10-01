"""QV discovery AESSecret 1.0.0 / 256-bit derivation, pure and reentrant.

These are public wire-algorithm lookup constants, not device credentials.
Seven synthetic inputs were checked against the original ARM64 math under
isolated emulation. See docs/connect3-analysis.md for offsets and limitations.
"""
_BASE = bytes.fromhex(
    '57244069a66ccd063c8f9e5aad2bc4d578465522b1e11fb51678465522b1e11f'
)
_FIRST = bytes.fromhex(
    'd3786c15c1646cffa1264c2a46f159a38b88ccaa178816822c7ab5ebc4834c49'
    'a4993120798611722f986864f6b006a5811a1c4042dc8ad1ce5a2d3315657224'
    'f462f858620ac5f24d269cfbfb745002697ff6d26a796c722dfd36e1a8f110d7'
    '0670834c36b0ff9a500f8c7e1b2874db40c7e11d8f87e096222a3de4552864d9'
    '6f597b5af12627eecf44335ee70ff9b080c73ccbcb871a31836b2f23502c1447'
    'add1fab123c3ce62979b9581390932f383c37729846884a9a728fcb2c7da78c0'
    'db658cf74d75eb8c6627513ec61bef605652e819e89d5f817890f458dbc665ec'
    '173ff959463624e88f240d16a7d7b83840af6bf9e9cffae278cdcacb740ed540'
)
_STEP = bytes.fromhex(
    'd378c1268b7a791af47f362a809b4d3fd388795a690ff1d1db3f8de5f365aa6b'
    'd399620f80284678a143aa896ce85bc9d31a366b56ab743f9c33a6ff1feb964c'
    'd362f127748baa1de98bd3a4184e3154d37f23cdf344ed4877fe5b4ed1f8a3ad'
    'd3704de546876cbf644c768b9b271872d3c7468b0fe82d158cdae87c6208eeea'
    'd3598dfa6c3c764c7520390a399fb13dd3c71c8bb2555bc6d75618e2a3d7b41d'
    'd3d1742ad0a4d8c76f84fb8d1e572fddd3c366681f2d31423072d40f7ff6e343'
    'd36586e8ab9a5b4da79b04b61e0c8464d352aac480f1b52a347664c0d335fc0a'
    'd33fe52018a6392e5fa6450cf0a6f336d3af3689db746f26dc97fc61da777102'
)


def derive_key(seed: bytes) -> bytes:
    if not isinstance(seed, bytes) or len(seed) > 1024:
        raise ValueError('Invalid QV discovery seed size')
    initial = bytearray(_BASE)
    # Deliberate native behavior: an empty seed or one longer than 32 bytes
    # leaves the initial key unchanged. Do not apply this to authentication.
    if 1 <= len(seed) <= 32:
        for index, value in enumerate(seed):
            initial[index] = (initial[index] + value) & 255
    box = [_FIRST[initial[0]]]
    for _ in range(255):
        box.append(_STEP[box[-1]])
    schedule = bytearray(initial)
    for word in (8, 9):
        temp = list(schedule[-4:])
        if word == 8:
            temp = [box[value] for value in temp[1:] + temp[:1]]
            temp[0] ^= 1
        schedule.extend(schedule[(word - 8) * 4 + index] ^ value
                        for index, value in enumerate(temp))
    return bytes(schedule[8:40])
