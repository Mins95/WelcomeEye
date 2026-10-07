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

URL = 'https://files.pythonhosted.org/packages/2d/42/af1e5755f4cdeb5926ef4aee4bd99d2fbc04b497dfe09f036d359219993e/aiortc-1.15.0.tar.gz'
DIGEST = 'ee6c0757ca070cf6d6bee441936d6ede24eef51211bbff6653409c540f72e625'

if __name__ == '__main__':
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--codecs', action='store_true', help='Also test every upstream media codec')
    args = parser.parse_args()
    suites = ['tests.test_rtcsctptransport']
    if args.codecs:
        suites += ['tests.test_codecs', 'tests.test_g711', 'tests.test_g722',
                   'tests.test_opus', 'tests.test_h264', 'tests.test_vpx', 'tests.test_mediastreams']
    data = urlopen(URL, timeout=60).read()
    assert sha256(data).hexdigest() == DIGEST
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(fileobj=BytesIO(data)) as source:
            source.extractall(temporary, filter='data')
        subprocess.run([sys.executable, '-m', 'unittest', *suites, '-q'],
                       cwd=Path(temporary) / 'aiortc-1.15.0', check=True, timeout=300)
