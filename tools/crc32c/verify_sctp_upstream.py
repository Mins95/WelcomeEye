"""Run upstream's unmodified SCTP and optional codec suites against the derivative."""
import argparse
from hashlib import sha256
from io import BytesIO
from pathlib import Path
import subprocess
import sys
import tarfile
import tempfile
from urllib.request import urlopen

URL = 'https://files.pythonhosted.org/packages/04/f0/7ea7fd109ddb1fd347c071ebb1e626f92adf24999231b5d8019ea3bccea8/aiortc-1.17.0.tar.gz'
DIGEST = '69c14be1699839b36ab8d8910b155cd95882f31e94b52edfb293cd827164df54'

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
        subprocess.run([sys.executable, '-m', 'unittest', *suites, '-q'],
                       cwd=Path(temporary) / 'aiortc-1.17.0', check=True, timeout=300)
