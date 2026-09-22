# Native google-crc32c on Home Assistant musl — submission draft

Prepared on 2026-09-22; **not submitted**. Review before submission.
Target repository: [home-assistant/wheels](https://github.com/home-assistant/wheels),
base `0593342304ed90da140c7cb613acf9aa8251b8da`.
Re-fetched on 2026-09-22: origin/master still points to that exact commit.
No upstream drift was found. The source tree contains CRLF lines; the patch
artifact preserves those bytes via `.gitattributes` (do not normalize it).
Patch: `home-assistant-wheels.patch` alongside this file.
Standalone submission body: [HA-PR-DRAFT.md](HA-PR-DRAFT.md).
It uses zero context; apply at that base with
`git apply --unidiff-zero /path/to/home-assistant-wheels.patch`.

## Proposed title

Build native google-crc32c wheels and reject cached pure-Python fallbacks

## Problem and demonstrated correction

An actual HA Core 2026.9.3 / CPython 3.14.6 / Alpine 3.24.1 amd64 installation
contains google-crc32c 1.8.0 with `Tag: py3-none-any`. The compiled `_crc32c`
extension is absent; `google_crc32c.cext` raises ModuleNotFoundError.
The configured HA index also lists the portable fallback. PyPI 1.8.0 has
CPython 3.14 manylinux wheels but no musllinux wheels. A historical build log
is unavailable, so the exact origin of this particular installed wheel is unknown.

Building the unmodified Google 1.8.0 sdist with Alpine `crc32c-dev` and
`CRC32C_PURE_PYTHON=0`, then running auditwheel repair, produces a usable
`cp314-cp314-musllinux_1_2` wheel on both x86_64 and aarch64. The extension
and its repaired native dependencies load in a clean HA image with no compiler,
no crc32c-dev package and no shared directory containing build dependencies.

The patch supplies the missing development library, refuses silent Python
fallbacks, and routes existing portable CRC wheels through the builder's
existing forced-source rebuild path. That rebuild bypasses pip's cache so
an old locally built fallback cannot be reused. Other packages keep their
existing policy. The unchanged auditwheel step bundles native dependencies.

## Reproduction / validation

Run on each architecture (Docker host may be glibc; the container is musl):

```sh
mkdir -p out
docker run --rm --entrypoint /bin/sh \
  -v "$PWD/tools/crc32c:/audit:ro" -v "$PWD/out:/out" \
  ghcr.io/home-assistant/home-assistant:2026.9.3 /audit/build-musl.sh
docker run --rm --entrypoint /bin/sh \
  -v "$PWD/tools/crc32c:/audit:ro" -v "$PWD/out:/out:ro" \
  ghcr.io/home-assistant/home-assistant:2026.9.3 -ec \
  'python -m pip install --no-deps --force-reinstall /out/*.whl; python -m pip install aiortc==1.15.0; python /audit/verify-runtime.py'
```

[Successful two-architecture CI, with wheel artifacts and reports](https://github.com/Mins95/WelcomeEye/actions/runs/35778484771).
The patch's builder tests also pass: 42 tests, including portable CRC wheel
rejection, bypassing the old wheel cache and preserving unrelated package policy.

For each architecture the runtime test verifies:

- implementation `c`, explicit cext import and no Python-fallback warning;
- five CRC32C vectors, incremental `extend`/Checksum API and 700 concurrent checks;
- actual aiortc 1.15.0 SCTP DataChannel payload transfer;
- synthetic video and audio in both directions, three complete open/close cycles;
- native versus Python timing on 1,200-byte payloads.

These are synthetic media tests, not microphone tests on physical hardware.
Same-architecture HA containers were validated, not a full HAOS supervisor VM.
The follow-up CI builds the actual patched upstream Dockerfile for both
architectures, runs its tests and actual wheel-selection/build/repair functions,
then installs the result in a separate clean HA image. No upload pipeline or
upstream publication is exercised.
[Successful actual-builder CI](https://github.com/Mins95/WelcomeEye/actions/runs/35784419251)
passes all of these checks on both architectures. auditwheel reports no external
shared libraries outside its musllinux policy; the independent ELF check allows
only platform musl libc outside the wheel. Native timings were 0.529 microseconds
(x86_64) and 0.186 microseconds (aarch64) per 1,200-byte input, versus 210.913 and
208.338 microseconds respectively for the Python reference in those containers.
See [the dedicated license/dependency audit](LICENSE-AUDIT.md); release notice
and source-distribution obligations remain explicit maintainer review items.

## Rollout and packaging decisions for maintainers

1. Rebuild google-crc32c 1.8.0 for both CPython 3.14 architectures with the patched
   builder. Retire or supersede the portable fallback in the wheel index.
2. Verify bundled library license notices according to the wheel index's
   redistribution policy before releasing artifacts; research CI artifacts
   are not a supported WelcomeEye runtime distribution.
3. A wheel appearing in the index does not replace an already installed package
   with the same version. Validate a clean Core image rebuild or supported
   dependency reinstall/cache migration as part of the rollout.
4. Test imports and actual SCTP in the resulting Core image before publication.

HACS distributes integration code; it does not install Alpine build packages.
No supported, transparent custom-integration workaround has been established
for replacing an already satisfied pure-Python dependency. Do not bundle the
binary in `custom_components` or recommend an ephemeral manual site-packages
replacement as a durable fix. The official wheel index/Core image route is
the proposed supported solution; it needs maintainer acceptance and deployment.

## Google upstream alternative

Current source: [googleapis/google-cloud-python, packages/google-crc32c](https://github.com/googleapis/google-cloud-python/tree/main/packages/google-crc32c).
The former python-crc32c repository points to this location. An issue draft:

> Please publish musllinux_1_2 wheels for google-crc32c on CPython 3.14,
> x86_64 and aarch64. On HA Alpine, available manylinux wheels are incompatible
> and the installed pure wheel falls back to Python. Compiling the existing
> sdist with libcrc32c headers and CRC32C_PURE_PYTHON=0, followed by auditwheel
> repair, passed native import, CRC32C vectors, threaded checks and aiortc SCTP
> loopback on both architectures. Reproduction and CI evidence are linked above.
> Can this build matrix be included in the official wheel release process?

Targeted GitHub issue searches on the old Google repository, current Google
repository and HA wheels returned no matches during this audit. This is not
proof that no related issue or roadmap exists.

A standalone [Google issue title/body draft](GOOGLE-ISSUE-DRAFT.md) is ready
for human review. Nothing has been submitted.

No CRC backend substitution or aiortc patch is necessary: the existing native
Google implementation works. aiortc's `google-crc32c>=1.1` requirement accepts
this package. The observed Core constraints do not conflict with aiortc 1.15.0.

OHF's [AI contribution policy](https://developers.home-assistant.io/docs/ai_policy)
requires human review and does not accept autonomous submissions. These are
reviewable drafts; no issue, PR or comment has been posted to either upstream.
