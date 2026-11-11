"""Enable exactly one hourly cron line, preserving other jobs."""
import json
from pathlib import Path
import shutil
import subprocess

base = Path.home() / '.local/share/knitty-monitor'
status = json.loads((base / 'status.json').read_text())
if status.get('mail_ok') is not True:
    raise RuntimeError('A successful live check and mail path are required before scheduling')
if not shutil.which('timeout'):
    raise RuntimeError('timeout unavailable')
subprocess.run(['systemctl', 'is-active', '--quiet', 'cron'], check=True)
existing = subprocess.run(['crontab', '-l'], capture_output=True, text=True)
if existing.returncode not in (0, 1):
    raise RuntimeError('Cannot read crontab')
backup = base / 'crontab.before-monitor'
if not backup.exists():
    backup.write_text(existing.stdout)
    backup.chmod(0o600)
line = ('0 * * * * /usr/bin/timeout 15m /usr/bin/python3 '
        '/home/tim/.local/share/knitty-monitor/monitor.py '
        '>> /home/tim/.local/share/knitty-monitor/monitor.log 2>&1 # knitty-monitor')
lines = [v for v in existing.stdout.splitlines() if not v.endswith('# knitty-monitor')]
subprocess.run(['crontab', '-'], input='\n'.join(lines + [line]) + '\n', text=True, check=True)
verified = subprocess.check_output(['crontab', '-l'], text=True)
assert verified.splitlines().count(line) == 1
print('HOURLY_CRON_ENABLED')
print(line)
print('Latest live check:', json.dumps(status))
