"""Reproducible, minimal aiortc derivative; no platform binary is redistributed."""
import argparse
import base64
import csv
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile, ZipInfo, ZIP_DEFLATED

UPSTREAM_VERSION = '1.15.0'
VERSION = '1.15.0+welcomeeye.crc1'
FILENAME = f'aiortc-{VERSION}-py3-none-any.whl'
UPSTREAM_URL = 'https://files.pythonhosted.org/packages/0c/5f/8435ba02c9278b6cec6f168db92e1d3280dd3af8f2225e20dc7c3be5ab22/aiortc-1.15.0-py3-none-any.whl'
UPSTREAM_SHA256 = '4e1e54bff31a9c2cb654c7b7edc068085a7df53365e5df24a5cb24168e3f95f7'
OLD_INFO = f'aiortc-{UPSTREAM_VERSION}.dist-info'
NEW_INFO = f'aiortc-{VERSION}.dist-info'
PATCHES = {
    'aiortc/rtcsctptransport.py': [(b'from google_crc32c import value as crc32c', b'from crc32c import crc32c')],
    'aiortc/__init__.py': [(b'__version__ = "1.15.0"', b'__version__ = "1.15.0+welcomeeye.crc1"')],
    f'{OLD_INFO}/METADATA': [
        (b'\nVersion: 1.15.0\n', b'\nVersion: 1.15.0+welcomeeye.crc1\n'),
        (b'\nRequires-Dist: google-crc32c>=1.1\n', b'\nRequires-Dist: crc32c==2.9.post0\n'),
        (b'\nSummary: An implementation of WebRTC and ORTC\n', b'\nSummary: WelcomeEye CRC32C build of aiortc WebRTC and ORTC\n'),
    ],
}
NOTICE = f'''WelcomeEye derivative of aiortc {UPSTREAM_VERSION}

This is a downstream build, not an official aiortc release.
Source: https://github.com/aiortc/aiortc/tree/1.15.0
Upstream wheel SHA256: {UPSTREAM_SHA256}
Reproducible patch/build: https://github.com/Mins95/WelcomeEye/tree/main/tools/crc32c

Only the SCTP CRC32C import and dependency change (google-crc32c to crc32c).
Version and distribution metadata identify this derivative. The aiortc BSD-3-Clause
license and upstream copyright notices are preserved in licenses/LICENSE.
crc32c 2.9.post0 is a separate, unmodified LGPL-2.1-or-later dependency installed
from its official distribution; no crc32c binary is embedded in this wheel.
'''.encode()


def build(source, output):
    if sha256(source).hexdigest() != UPSTREAM_SHA256:
        raise ValueError('Upstream wheel SHA256 mismatch')
    files = {}
    with ZipFile(BytesIO(source)) as wheel:
        for name in wheel.namelist():
            if name.endswith('/RECORD'):
                continue
            data = wheel.read(name)
            for before, after in PATCHES.get(name, []):
                if data.count(before) != 1:
                    raise ValueError(f'Expected source differs: {name}')
                data = data.replace(before, after)
            files[name.replace(OLD_INFO + '/', NEW_INFO + '/')] = data
    files[f'{NEW_INFO}/WELCOMEEYE.txt'] = NOTICE
    record = StringIO(newline='')
    writer = csv.writer(record, lineterminator='\n')
    for name, data in sorted(files.items()):
        digest = base64.urlsafe_b64encode(sha256(data).digest()).rstrip(b'=').decode()
        writer.writerow((name, 'sha256=' + digest, len(data)))
    writer.writerow((f'{NEW_INFO}/RECORD', '', ''))
    files[f'{NEW_INFO}/RECORD'] = record.getvalue().encode()
    output.mkdir(parents=True, exist_ok=True)
    target = output / FILENAME
    with ZipFile(target, 'w', ZIP_DEFLATED, compresslevel=9) as wheel:
        for name, data in sorted(files.items()):
            entry = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            entry.compress_type = ZIP_DEFLATED
            wheel.writestr(entry, data, compresslevel=9)
    digest = sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.whl.sha256').write_text(f'{digest}  {FILENAME}\n', encoding='ascii')
    print(f'{digest}  {FILENAME}')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-wheel', type=Path)
    parser.add_argument('--output', type=Path, default=Path('out'))
    args = parser.parse_args()
    source = args.upstream_wheel.read_bytes() if args.upstream_wheel else urlopen(UPSTREAM_URL, timeout=60).read()
    build(source, args.output)
