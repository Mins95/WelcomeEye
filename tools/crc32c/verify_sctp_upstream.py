"""Run upstream's unmodified SCTP test suite against the installed derivative."""
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
    data = urlopen(URL, timeout=60).read()
    assert sha256(data).hexdigest() == DIGEST
    with tempfile.TemporaryDirectory() as temporary:
        with tarfile.open(fileobj=BytesIO(data)) as source:
            source.extractall(temporary, filter='data')
        subprocess.run([sys.executable, '-m', 'unittest', 'tests.test_rtcsctptransport', '-q'],
                       cwd=Path(temporary) / 'aiortc-1.15.0', check=True, timeout=300)
