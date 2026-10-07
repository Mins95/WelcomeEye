# Native CRC32C and Home Assistant 2026.10 compatibility

## 0.4.3 compatibility correction

Home Assistant 2026.10.0 requires `av==19.0.0` in its
[official package constraints](https://github.com/home-assistant/core/blob/2026.10.0/homeassistant/package_constraints.txt).
The previous `aiortc 1.15.0+welcomeeye.crc1` wheel requires `av>=14,<18`.
Those constraints cannot be satisfied together, causing WelcomeEye requirement
installation to fail after the Core upgrade. The URL/hash itself is unchanged
and the installer still applies Core's normal constraints.

The correction is `aiortc 1.15.0+welcomeeye.crc2`: the same SCTP
native CRC32C change, with a declared PyAV range of `>=14,<20`. Its media and
transport Python code is unchanged from the previous derivative. The bound is
accepted only after actual PyAV 19 installation, upstream codec tests and
bidirectional WebRTC checks in clean Home Assistant containers; constraints
are not bypassed and Core's installed PyAV is not downgraded.

[Native validation run 37670791459](https://github.com/Mins95/WelcomeEye/actions/runs/37670791459)
passed on x86_64/aarch64 musl with HA 2026.9.3 and 2026.10.0, plus glibc PyAV 19.
It reproduced the old resolver failure, installed the new wheel through HA's
full requirements API, retained Core's PyAV version, and passed unmodified
upstream SCTP/codec tests and three bidirectional WebRTC cycles per runtime.
The project validation also checks HA 2026.7.3 and Python 3.12/3.14 with PyAV
17.0.1/19.0.0.

crc2 wheel SHA256:
`49ea45110ca3012f4fb33ddce1c9f4fc46642b8bff32f8130b576740f1046d35`.
It is an additional immutable asset hosted alongside crc1 under v0.4.2;
existing assets are not overwritten. The 0.4.3 manifest selects only crc2.

The original crc1 wheel remains unchanged for existing 0.4.2 installations.
Users who already installed the initial 0.4.3 must **Redownload 0.4.3 in HACS**
and restart Home Assistant after the corrected assets are published; the version
number is unchanged. No device firmware or intercom setting is modified.

## Historical 0.4.2 correction — 2026-09-24

The maintainer explicitly approved a tested downstream aiortc build and requested
republication in the existing 0.4.2 release. This supersedes the earlier
upstream-only distribution decision. The Google/HA wheel-builder drafts remain
available; they have not been submitted or deployed upstream.

## Shipped correction

`aiortc 1.15.0+welcomeeye.crc1` changes one runtime import in
`aiortc/rtcsctptransport.py` from `google_crc32c.value` to `crc32c.crc32c`.
The dependency becomes `crc32c==2.9.post0`, which publishes native musllinux and
manylinux wheels for the tested x86_64/aarch64 CPython 3.14 environments.
The remaining aiortc protocol/media code is byte-identical to upstream 1.15.0.
Version and distribution metadata identify this derivative explicitly.

The official aiortc pure-Python wheel is SHA256-pinned in `build_aiortc.py`.
That script preserves the BSD-3-Clause license, applies the two-line functional
change, updates the derivative version/metadata, and recomputes wheel RECORD.
The result is reproducible on both tested architectures:

`75f7d14e598dfd2b3e97bd4b9342e9b675185b6a3d83d5a5083c43250b6eee9d`

Home Assistant installs it as a normal URL requirement with this hash. Its
package manager installs the separate, unmodified `crc32c` package from the
package index. That dependency is LGPL-2.1-or-later and includes its own license;
its source and license are available in the exact
[2.9.post0 source distribution on PyPI](https://pypi.org/project/crc32c/2.9.post0/)
and the [upstream repository](https://github.com/ICRAR/crc32c).
No native binary is bundled in `custom_components` or the aiortc wheel. No
`sys.modules` injection, runtime monkey patch, warning filter, CRC32 replacement,
or disabled checksum is used.

## Validation

[Two-architecture CI](https://github.com/Mins95/WelcomeEye/actions/runs/36036885406)
passed in actual HA Core 2026.9.3 / CPython 3.14.6 / musl images on x86_64 and
aarch64, and in glibc Python 3.14 containers on both architectures:

- HA's `install_package` installs the derivative and resolves the native CRC wheel.
- SCTP's actual checksum callable is the native C function, not the old Google fallback.
- Five CRC32C reference vectors, incremental checks and 700 concurrent checks pass.
- All 72 unmodified upstream SCTP tests pass, including serialized packet fixtures.
- Three PeerConnection open/close cycles transfer a fragmented DataChannel message,
  synthetic video, downstream audio and upstream microphone audio.
- No CRC32C warning occurs on aiortc import.

The same tests also passed in an isolated container using the owner's exact
QNAP HA image. This is synthetic audio/video validation, not a new physical
microphone/intercom test. No physical output command is involved.

Diagnostics now examine the callable actually loaded by aiortc: expected values
are `crc32c_package=crc32c`, `crc32c_version=2.9.post0`, `crc32c_backend=c` and
`crc32c_native_available=true`. CPU acceleration is reported separately from
native C availability. An unrelated integration explicitly importing Google CRC
can still emit its own warning; this derivative does not replace that package.

## Rollout and maintenance

Existing 0.4.2 installations must **Redownload** 0.4.2 in HACS and **restart HA**.
The version number is intentionally unchanged at the maintainer's request, so
an automatic HACS update notification is not guaranteed. The derivative wheel
is an immutable additional release asset; the integration ZIP/checksum and tag
are replaced once with recorded old/new digests.

HA's normal dependency installation repeats after a Core container update when
needed; no manual modification of site-packages or custom Core image is required.
The local aiortc version remains compatible with the public `==1.15.0` constraint.
Future aiortc upgrades must rebase and rerun these checks before adopting another
upstream release. When an official compatible fix becomes available, remove the
derivative requirement and verify migration through the same HA installer path.

Rollback: restore the previous integration package and original `aiortc==1.15.0`
distribution together, then restart HA. Reverting only the WelcomeEye ZIP does
not necessarily downgrade a compatible local-version dependency automatically.
