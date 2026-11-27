"""Reproducible, minimal aiortc derivative; no platform binary is redistributed."""
import argparse
import base64
import csv
from hashlib import sha256
from io import BytesIO, StringIO
from pathlib import Path
from urllib.request import urlopen
from zipfile import ZipFile, ZipInfo

UPSTREAM_VERSION = '1.17.0'
VERSION = f'{UPSTREAM_VERSION}+welcomeeye.crc1'
FILENAME = f'aiortc-{VERSION}-py3-none-any.whl'
UPSTREAM_URL = 'https://files.pythonhosted.org/packages/4e/e8/023c8d0258ab3e59150c356050bd570b36874433b129405136c04e512840/aiortc-1.17.0-py3-none-any.whl'
UPSTREAM_SHA256 = 'b9c24008d90313de290a9876e11940f5c44bb29ae9cd256b32afa88f149bdb5a'
OLD_INFO = f'aiortc-{UPSTREAM_VERSION}.dist-info'
NEW_INFO = f'aiortc-{VERSION}.dist-info'
PATCHES = {
    'aiortc/rtcsctptransport.py': [(b'from google_crc32c import value as crc32c', b'from crc32c import crc32c')],
    'aiortc/__init__.py': [(f'__version__ = "{UPSTREAM_VERSION}"'.encode(), f'__version__ = "{VERSION}"'.encode())],
    f'{OLD_INFO}/METADATA': [
        (f'\nVersion: {UPSTREAM_VERSION}\n'.encode(), f'\nVersion: {VERSION}\n'.encode()),
        (b'\nRequires-Dist: google-crc32c>=1.1\n', b'\nRequires-Dist: crc32c==2.9.post0\n'),
        (b'\nSummary: An implementation of WebRTC and ORTC\n', b'\nSummary: WelcomeEye CRC32C build of aiortc WebRTC and ORTC\n'),
    ],
}
NOTICE = f'''WelcomeEye derivative of aiortc {UPSTREAM_VERSION}

This is a downstream build, not an official aiortc release.
Source: https://github.com/aiortc/aiortc/tree/{UPSTREAM_VERSION}
Upstream wheel SHA256: {UPSTREAM_SHA256}
Reproducible patch/build: https://github.com/Mins95/WelcomeEye/tree/v0.4.9/tools/crc32c

Only the SCTP CRC32C import and dependency change (google-crc32c to crc32c).
The PyAV and aioice requirements are the upstream requirements.
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
    # Stored entries give identical release hashes across zlib implementations.
    with ZipFile(target, 'w') as wheel:
        for name, data in sorted(files.items()):
            entry = ZipInfo(name, (1980, 1, 1, 0, 0, 0))
            entry.create_system = 3
            entry.external_attr = 0o100644 << 16
            wheel.writestr(entry, data)
    digest = sha256(target.read_bytes()).hexdigest()
    target.with_suffix('.whl.sha256').write_text(f'{digest}  {FILENAME}\n', encoding='ascii', newline='\n')
    print(f'{digest}  {FILENAME}')
    return target


if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--upstream-wheel', type=Path)
    parser.add_argument('--output', type=Path, default=Path('out'))
    args = parser.parse_args()
    source = args.upstream_wheel.read_bytes() if args.upstream_wheel else urlopen(UPSTREAM_URL, timeout=60).read()
    build(source, args.output)
