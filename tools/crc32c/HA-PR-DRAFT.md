# Draft only — not submitted

Target: https://github.com/home-assistant/wheels
Base: `0593342304ed90da140c7cb613acf9aa8251b8da` (HEAD rechecked 2026-09-22)
Patch: [home-assistant-wheels.patch](home-assistant-wheels.patch)

Title: Build native google-crc32c and rebuild cached pure-Python fallback wheels

## Body

On HA Core 2026.9.3 / CPython 3.14.6 / Alpine 3.24.1, an installed
google-crc32c 1.8.0 `py3-none-any` wheel lacks `_crc32c` and aiortc uses the slow
Python CRC32C implementation. The existing index contains that portable wheel,
so adding headers alone is insufficient if a cached wheel is reused.

Add crc32c-dev, require the package's native build with CRC32C_PURE_PYTHON=0,
reject portable google-crc32c wheels through the existing forced-source rebuild
path, and bypass pip's cached fallback on that rebuild. Other packages retain
their current cache policy. The existing auditwheel repair path handles musl
dependencies; there is no CRC algorithm substitution or warning suppression.

Validation on x86_64 and aarch64:

- Actual patched Dockerfile built from HA Core 2026.9.3.
- 42 builder tests; portable-wheel rejection, source rebuild and repair exercised.
- WHEEL tags, RECORD hashes, all ELF DT_NEEDED/RPATH entries checked.
- auditwheel show reports musllinux_1_2 and no external libraries beyond the
  platform policy; every non-libc dependency is bundled.
- Installation in a separate clean HA image, native cext/backend C, no fallback
  warning, five reference vectors, incremental API and 700 concurrent checks.
- Three actual aiortc SCTP DataChannel + synthetic audio/video open-close cycles.

[Two-architecture CI and artifacts](https://github.com/Mins95/WelcomeEye/actions/runs/35784419251).

Before merging/deploying, please review the bundled-library notice and source
distribution requirements in [LICENSE-AUDIT.md](LICENSE-AUDIT.md). The repaired
wheel currently includes the wrapper license; auditwheel does not automatically
supply all copied libraries' license/source obligations. This draft does not
claim that research artifacts alone constitute a release-ready distribution.

Rollout must rebuild the wheel index and Core dependency image/cache. Merely
publishing a native wheel with version 1.8.0 does not replace an already satisfied
pure 1.8.0 install. Verify backend C and cext in the final shipped Core image and
in an existing-user dependency path, not only in the build job. See
[the rollout plan](UPSTREAM.md#rollout-and-packaging-decisions-for-maintainers).
