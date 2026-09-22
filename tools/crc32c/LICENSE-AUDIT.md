# Repaired wheel dependency/license audit

Research artifacts are not a supported integration binary distribution.
No binary is included in `custom_components`.

The previously built x86_64 wheel contains:

| Component | Observed binary/file | License evidence |
| --- | --- | --- |
| google-crc32c 1.8.0 | `_crc32c.cpython-314-x86_64-linux-musl.so` | Package LICENSE: Apache-2.0 |
| Google crc32c 1.1.2 (Alpine 1.1.2-r3) | `libcrc32c-*.so.1.1.0` | BSD-3-Clause |
| GCC libgcc runtime | `libgcc_s-*.so.1` | GPL-3.0-or-later WITH GCC-exception-3.1 |
| GCC libstdc++ runtime | `libstdc++-*.so.6.0.34` | GPL-3.0-or-later WITH GCC-exception-3.1 |

Primary evidence:
- https://github.com/google/crc32c/blob/1.1.2/LICENSE
- https://pkgs.alpinelinux.org/package/edge/community/x86/crc32c
- https://gcc.gnu.org/onlinedocs/libstdc++/faq.html#faq.license
- https://www.gnu.org/licenses/gcc-exception-3.1-faq.html
- https://github.com/gcc-mirror/gcc/blob/master/COPYING.RUNTIME

The earlier repaired wheel includes only the wrapper's LICENSE under
`.dist-info/licenses`, plus the auditwheel CycloneDX SBOM. Auditwheel repair
does **not** establish that all bundled-library notices or source-distribution
obligations have been met. The BSD notice must accompany libcrc32c; GCC runtime
distribution must retain its license/exception notices and the applicable
corresponding-source arrangements. The runtime exception permits eligible
combinations; it does not mean the copied runtime libraries have no obligations.

This is an explicit packaging review item for the upstream maintainers before
release, not a claim that the existing research wheel is release-ready.
The draft's technical build fix does not silently overwrite the package license
to label every bundled library Apache-2.0.

`inspect-wheel.py` records the actual wheel tag, SHA256, included license files,
and DT_NEEDED/RPATH for **every** included shared object. It verifies RECORD
hashes and rejects residual non-libc system dependencies. Musl libc itself is
the expected platform dependency; it is not bundled. The clean-image test
then verifies actual dynamic loading and SCTP/media, independently of the
build container's available libraries.
