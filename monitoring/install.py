"""Narrow installer, invoked remotely with a base64 JSON bundle as its only argument."""
import base64
import json
from pathlib import Path
import re
import subprocess
import sys

base = Path.home() / '.local/share/knitty-monitor'
base.mkdir(mode=0o700, parents=True, exist_ok=True)
base.chmod(0o700)
bundle = json.loads(base64.b64decode(sys.argv[1]))
for name in ('incidents.py', 'test_incidents.py', 'monitor.py', 'test_monitor.py'):
    path = base / name
    if path.exists():
        (base / (name + '.previous')).write_bytes(path.read_bytes())
    path.write_text(bundle[name], encoding='utf-8')
    path.chmod(0o700)
config_path = base / 'config.json'
if not config_path.exists():
    names = subprocess.check_output(['docker', 'ps', '--format', '{{.Names}}'], text=True).splitlines()
    stable = [n for n in names if not re.search(r'-(blue|green)(-|$)', n)]
    releases = sorted(p.parent.name for p in (Path.home() / '.local/share/knitty-blue-green').glob('*/state.json'))
    if not stable or not releases:
        raise RuntimeError('Expected running containers and release state')
    config_path.write_text(json.dumps({'containers': stable, 'releases': releases}, indent=2))
    config_path.chmod(0o600)
subprocess.run(['/usr/bin/python3', '-m', 'unittest', 'discover', '-s', str(base)], check=True)
print('INSTALLED', base, flush=True)
