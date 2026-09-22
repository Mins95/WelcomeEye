# Draft only — not submitted

Title: Publish CPython 3.14 musllinux_1_2 wheels for x86_64 and aarch64

Target: https://github.com/googleapis/google-cloud-python/issues
Package source: https://github.com/googleapis/google-cloud-python/tree/main/packages/google-crc32c

## Body

Could the google-crc32c release matrix include `cp314-cp314-musllinux_1_2`
for x86_64 and aarch64?

On Home Assistant Core 2026.9.3 (CPython 3.14.6, Alpine 3.24.1/musl),
google-crc32c 1.8.0 is installed as `py3-none-any`, with implementation `python`.
`import google_crc32c.cext` raises ModuleNotFoundError for `_crc32c`.
The manylinux/glibc CPython 3.14 wheels cannot satisfy this runtime's musllinux
tags. The HA wheel index also contains a portable fallback; the historical
installation log is unavailable, so we do not assert its exact download origin.

Minimal build reproduction inside the HA image:

```sh
apk add --no-cache build-base crc32c-dev patchelf
python -m pip install auditwheel
CRC32C_PURE_PYTHON=0 python -m pip wheel --no-cache-dir --no-deps \
  --no-binary google-crc32c -w raw google-crc32c==1.8.0
auditwheel repair --plat musllinux_1_2_$(uname -m) -w repaired raw/*.whl
```

Install in a second clean container without the build dependencies:

```sh
python -m pip install --no-deps --force-reinstall repaired/*.whl
python -W error -c 'import google_crc32c, google_crc32c.cext; assert google_crc32c.implementation == "c"; assert google_crc32c.value(b"123456789") == 0xe3069283'
```

The unchanged official sdist builds successfully on both architectures.
Validation includes five known CRC32C vectors, incremental API, 700 concurrent
checks and three aiortc SCTP DataChannel/audio/video open-close cycles in clean
HA musl containers. No Python fallback warning, replacement module, monkey
patch or checksum bypass is used.

Reproduction and tests:
https://github.com/Mins95/WelcomeEye/tree/feature/042-ring-image-native-crc/tools/crc32c

Existing successful two-architecture evidence:
https://github.com/Mins95/WelcomeEye/actions/runs/35781219732

The repaired wheel bundles libcrc32c and GCC runtime libraries. Release builds
must include the corresponding redistribution notices/source arrangements,
not just retag a glibc wheel. We are preparing a separate HA wheel-builder
change as an ecosystem-side correction; official musllinux wheels would remove
the need for every musl consumer to repeat that build.

Would maintainers accept a musllinux build-matrix contribution for these targets?
