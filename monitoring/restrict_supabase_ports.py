#!/usr/bin/env python3
"""Restrict exposed Supabase ports; clarify log-only monitoring notifications."""
import copy
import datetime as dt
import fcntl
import json
import os
from pathlib import Path
import shutil
import subprocess
import sys
import yaml

os.umask(0o077)
base = Path('/home/tim/.local/share/knitty-monitor')
project = Path('/home/tim/git/supabase-project')
stage = base / ('port-hardening-' + dt.datetime.now(dt.timezone.utc).strftime('%Y%m%dT%H%M%SZ'))
stage.mkdir(mode=0o700)
files = ['docker-compose.yml', 'docker-compose.pg15.yml', 'docker-compose.kong.yml',
         'docker-compose.logs.yml', 'docker-compose.local.yml']
compose = ['docker', 'compose', '-p', 'supabase']
for name in files:
    compose += ['-f', str(project / name)]

def config(command):
    return json.loads(subprocess.check_output(command + ['config', '--format', 'json'], cwd=project, text=True))

def up():
    subprocess.run(compose + ['up', '-d', '--no-deps', '--wait', '--wait-timeout', '180',
                              '--pull', 'never', '--no-build', 'supavisor', 'analytics'], cwd=project, check=True)

lock = (base / 'monitor.lock').open('a')
fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
for name in ('monitor.py', 'test_monitor.py', 'incidents.py', 'test_incidents.py'):
    shutil.copy2(base / name, stage / name)
target = project / files[-1]
shutil.copy2(target, stage / 'compose.before.yml')
before = config(compose)

tests = """
class LogNotificationTests(unittest.TestCase):
    def test_log_only_subject_does_not_claim_service_outage(self):
        self.assertEqual(monitor.alarm_subject({'logs:supabase-pooler': {}}),
                         '[Knittynetwork] Logadvarsel')
        self.assertEqual(monitor.alarm_subject({'service:supabase-pooler': {}, 'logs:x': {}}),
                         '[Knittynetwork] Alarm: problemer fundet')
        self.assertEqual(monitor.alarm_subject({}), '[Knittynetwork] Kontrol uden fejl')

    def test_recovery_describes_log_window(self):
        result = monitor.report({}, {'logs:supabase-pooler': {}})
        self.assertIn('Ingen nye fejlmarkeringer i logs: supabase-pooler', result)
        self.assertNotIn('Løst siden sidste kontrol: logs:', result)

    def test_rejected_connections_and_real_database_errors_remain_visible(self):
        lines = ['2026-11-01T09:33:07Z [error] ClientHandler: (ENOIDENTIFIER) no tenant identifier provided',
                 '2026-11-01T09:34:00Z FATAL database unavailable']
        self.assertEqual(monitor.log_summary(lines)['count'], 2)
"""
with (stage / 'test_monitor.py').open('a') as output:
    output.write(tests)
red = subprocess.run([sys.executable, '-m', 'unittest', 'test_monitor.LogNotificationTests'], cwd=stage)
if red.returncode == 0:
    raise RuntimeError('Regression tests unexpectedly passed before change')

source = (stage / 'monitor.py').read_text()
needle = 'def notification_needed(previous, current):'
assert source.count(needle) == 1
source = source.replace(needle, """def alarm_subject(issues):
    if not issues:
        return '[Knittynetwork] Kontrol uden fejl'
    if all(key.startswith('logs:') for key in issues):
        return '[Knittynetwork] Logadvarsel'
    return '[Knittynetwork] Alarm: problemer fundet'


""" + needle)
old = """    if recovered:
        lines.append('\\nLøst siden sidste kontrol: ' + ', '.join(recovered))"""
new = """    recovered_logs = [key[5:] for key in recovered if key.startswith('logs:')]
    recovered_services = [key for key in recovered if not key.startswith('logs:')]
    if recovered_logs:
        lines.append('\\nIngen nye fejlmarkeringer i logs: ' + ', '.join(recovered_logs))
    if recovered_services:
        lines.append('\\nLøst siden sidste kontrol: ' + ', '.join(recovered_services))"""
assert source.count(old) == 1
source = source.replace(old, new)
old_subject = "'[Knittynetwork] ' + ('Alarm: problemer fundet' if issues else 'Drift genoprettet')"
assert source.count(old_subject) == 1
source = source.replace(old_subject, 'alarm_subject(issues)')
(stage / 'monitor.py').write_text(source)
subprocess.run([sys.executable, '-m', 'unittest', 'discover', '-s', str(stage)], cwd=stage, check=True)

class Override(list):
    pass
yaml.SafeDumper.add_representer(Override, lambda dumper, data: dumper.represent_sequence('!override', data))
override = yaml.safe_load(target.read_text())
expected = copy.deepcopy(before)
for service in ('supavisor', 'analytics'):
    ports = copy.deepcopy(before['services'][service]['ports'])
    for port in ports:
        port['host_ip'] = '127.0.0.1'
    override['services'][service]['ports'] = Override(ports)
    expected['services'][service]['ports'] = ports
candidate = project / 'docker-compose.port-hardening-candidate.yml'
candidate.write_text(yaml.safe_dump(override, sort_keys=False))
candidate_compose = compose[:-1] + [str(candidate)]
assert config(candidate_compose) == expected, 'Unexpected Compose changes'
print('VALIDATED: only supavisor/analytics host port bindings change', flush=True)
try:
    shutil.copy2(candidate, target)
    up()
    for name in ('supabase-pooler', 'supabase-analytics'):
        data = json.loads(subprocess.check_output(['docker', 'inspect', name], text=True))[0]
        assert data['State']['Running']
        assert data['State']['Health']['Status'] == 'healthy'
        for ports in data['NetworkSettings']['Ports'].values():
            for port in ports or []:
                assert port['HostIp'] == '127.0.0.1', 'Public port still bound'
        print(name + ': healthy; ports restricted to localhost', flush=True)
except Exception:
    shutil.copy2(stage / 'compose.before.yml', target)
    up()
    raise

for name in ('monitor.py', 'test_monitor.py'):
    temporary = base / (name + '.port-hardening.tmp')
    shutil.copy2(stage / name, temporary)
    temporary.replace(base / name)
note = """\n\n## Portadgang og logadvarsler (1. november 2026)
Poolerportene 5432/6543 og analytics-port 4000 er bundet til 127.0.0.1.
Interne Docker-forbindelser fortsætter uændret. Fjernadministration bruger SSH-tunnel.
Compose-overriden bruger !override, så offentlige portbindinger erstattes fremfor at blive tilføjet.
Logfejl registreres fortsat; mails med udelukkende logfejl hedder Logadvarsel.
En kontrol uden nye logfejl beskriver kontrolperioden og hævder ikke, at en tjeneste er repareret.
Backuppen før ændringen og regressionstests ligger i port-hardening-mappen.
"""
with (base / 'README.md').open('a') as output:
    output.write(note)
lock.close()
print('BACKUP=' + str(stage), flush=True)
subprocess.run([sys.executable, str(base / 'monitor.py'), '--dry-run'], check=True)
