"""Exercise the patched HA builder functions, with no upload or device access."""
from pathlib import Path
import subprocess

from builder.pip import build_wheels_package
from builder.wheel import fix_wheels_unmatch_requirements, run_auditwheel

out = Path('/out')
# Model a portable wheel selected from the existing index. This placeholder is
# never installed: the real rejection function must remove it before rebuilding.
portable = out / 'google_crc32c-1.8.0-py3-none-any.whl'
portable.touch()
rebuild = fix_wheels_unmatch_requirements(out)
assert rebuild == {'google_crc32c': '1.8.0'}
assert not portable.exists()
build_wheels_package('google_crc32c==1.8.0', 'https://wheels.home-assistant.io/musllinux-index/',
                     out, 'google_crc32c', 600)
assert run_auditwheel(out)
assert not fix_wheels_unmatch_requirements(out)
wheels = list(out.glob('*.whl'))
assert len(wheels) == 1 and 'musllinux_1_2' in wheels[0].name
subprocess.run(['auditwheel', 'show', str(wheels[0])], check=True)
subprocess.run(['python', '/audit/inspect-wheel.py', str(wheels[0])], check=True)
