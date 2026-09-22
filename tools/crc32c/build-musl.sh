#!/bin/sh
# Isolated Alpine/HA image only. Never run the build in production HA.
set -eu
apk add --no-cache build-base crc32c-dev patchelf
python -m pip install --no-cache-dir auditwheel
mkdir -p /tmp/crc-wheel/raw /tmp/crc-wheel/repaired
CRC32C_PURE_PYTHON=0 python -m pip wheel --no-cache-dir --no-deps \
  --no-binary google-crc32c --wheel-dir /tmp/crc-wheel/raw google-crc32c==1.8.0
auditwheel repair --plat "musllinux_1_2_$(uname -m)" \
  --wheel-dir /tmp/crc-wheel/repaired /tmp/crc-wheel/raw/*.whl
python -m pip install --no-deps --force-reinstall /tmp/crc-wheel/repaired/*.whl
python -W error -c 'import google_crc32c, google_crc32c.cext; assert google_crc32c.implementation == "c"; assert google_crc32c.value(b"123456789") == 0xe3069283; print("Native CRC32C verified")'
readelf -d /usr/local/lib/python3.14/site-packages/google_crc32c/_crc32c*.so
if [ -d /out ]; then
  cp /tmp/crc-wheel/repaired/*.whl /out/
fi
