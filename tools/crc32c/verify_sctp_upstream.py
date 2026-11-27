"""Run upstream's unmodified SCTP and optional codec suites against the derivative."""
import argparse
from hashlib import sha256
from io import BytesIO
import os
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

URL = 'https://files.pythonhosted.org/packages/04/f0/7ea7fd109ddb1fd347c071ebb1e626f92adf24999231b5d8019ea3bccea8/aiortc-1.17.0.tar.gz'
DIGEST = '69c14be1699839b36ab8d8910b155cd95882f31e94b52edfb293cd827164df54'
REFERENCE_URL = 'https://files.pythonhosted.org/packages/fa/25/9cb0c1c31c45b893eb8f11ae70b3f4309432d59b5acaebca5dbe791729a4/google_crc32c-1.9.0.tar.gz'
REFERENCE_SHA256 = '7b8c84c3d159ab6817fe3f74e6e6cef099c3f95dcec3abc0d8afb1404642efbe'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codecs', action='store_true', help='Also test every upstream media codec')
    parser.add_argument('--source', type=Path, help='Use a local copy of the official source archive')
    args = parser.parse_args()
    suites = ['tests.test_rtcsctptransport']
    if args.codecs:
        suites += ['tests.test_codecs', 'tests.test_g711', 'tests.test_g722',
                   'tests.test_opus', 'tests.test_h264', 'tests.test_vpx', 'tests.test_mediastreams']
    data = args.source.read_bytes() if args.source else urlopen(URL, timeout=60).read()
    assert sha256(data).hexdigest() == DIGEST
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(fileobj=BytesIO(data)) as source:
            source.extractall(temporary, filter='data')
        # The upstream tests construct reference packets with Google's CRC.
        # Expose its independent Python implementation only to the test process.
        reference = urlopen(REFERENCE_URL, timeout=60).read()
        if sha256(reference).hexdigest() != REFERENCE_SHA256:
            raise ValueError('Reference checksum package SHA256 mismatch')
        with tarfile.open(fileobj=BytesIO(reference)) as source:
            source.extractall(temporary, filter='data')
        env = os.environ.copy()
        reference_path = str(Path(temporary) / 'google_crc32c-1.9.0' / 'src')
        env['PYTHONPATH'] = os.pathsep.join(filter(None, (reference_path, env.get('PYTHONPATH'))))
        subprocess.run([sys.executable, '-m', 'unittest', *suites, '-q'],
                       cwd=Path(temporary) / 'aiortc-1.17.0', env=env, check=True, timeout=300)
