"""Verify metadata, RECORD hashes and complete non-libc ELF dependency closure."""
import base64
import csv
import hashlib
import io
import json
from pathlib import Path
import re
import subprocess
import sys
from tempfile import TemporaryDirectory
from zipfile import ZipFile

path = Path(sys.argv[1])
report = {'wheel': path.name, 'sha256': hashlib.sha256(path.read_bytes()).hexdigest()}
with ZipFile(path) as archive, TemporaryDirectory() as folder:
    names = archive.namelist()
    metadata = next(n for n in names if n.endswith('.dist-info/METADATA'))
    wheel = next(n for n in names if n.endswith('.dist-info/WHEEL'))
    record = next(n for n in names if n.endswith('.dist-info/RECORD'))
    report['wheel_metadata'] = archive.read(wheel).decode()
    assert 'Root-Is-Purelib: false' in report['wheel_metadata']
    assert 'Tag: cp314-cp314-musllinux_1_2_' in report['wheel_metadata']
    for name, digest, size in csv.reader(io.StringIO(archive.read(record).decode())):
        if digest:
            algorithm, expected = digest.split('=', 1)
            data = archive.read(name)
            actual = base64.urlsafe_b64encode(hashlib.new(algorithm, data).digest()).rstrip(b'=').decode()
            assert actual == expected and len(data) == int(size), name
    report['record_verified'] = True
    report['license_files'] = [n for n in names if '/licenses/' in n and not n.endswith('/')]
    report['declared_licenses'] = [s for s in archive.read(metadata).decode().splitlines()
                                   if s.startswith(('License', 'Classifier: License'))]
    binaries = [n for n in names if '.so' in Path(n).name and not n.endswith('/')]
    basenames = {Path(n).name for n in binaries}
    report['elf'] = []
    for name in binaries:
        target = Path(folder) / Path(name).name
        target.write_bytes(archive.read(name))
        dynamic = subprocess.check_output(['readelf', '-d', str(target)], text=True)
        needed = re.findall(r'\(NEEDED\).*?\[(.*?)\]', dynamic)
        paths = re.findall(r'\((?:RPATH|RUNPATH)\).*?\[(.*?)\]', dynamic)
        missing = [n for n in needed if n not in basenames and not n.startswith('libc.musl-')]
        assert not missing, (name, missing)
        assert all(p.startswith('$ORIGIN') for value in paths for p in value.split(':')), paths
        report['elf'].append({'name': name, 'needed': needed, 'search_paths': paths})
    report['external_dependency_policy'] = 'musl libc only; all other DT_NEEDED bundled'
    assert any('_crc32c.cpython-314' in n for n in binaries)
Path('/out/packaging.json').write_text(json.dumps(report, indent=2))
print(json.dumps(report, indent=2))
