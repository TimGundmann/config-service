#!/usr/bin/env python3
"""One-time migration of the existing server Compose project to bounded logs."""
import json
from pathlib import Path
import shutil
import subprocess

import yaml


def main():
    directory = Path('/home/tim/git/supabase-project')
    files = ['docker-compose.yml', 'docker-compose.pg15.yml', 'docker-compose.kong.yml',
             'docker-compose.logs.yml', 'docker-compose.local.yml']
    compose = ['docker', 'compose']
    for file in files:
        compose += ['-f', file]

    def configuration():
        result = subprocess.check_output(compose + ['config', '--format', 'json'], cwd=directory, text=True)
        return json.loads(result)

    before = configuration()
    target = directory / files[-1]
    backup = target.with_name(target.name + '.before-log-rotation')
    if backup.exists():
        raise RuntimeError('Backup already exists; review the previous migration first')
    shutil.copy2(target, backup)
    override = yaml.safe_load(target.read_text())
    for name in before['services']:
        override.setdefault('services', {}).setdefault(name, {})['logging'] = {
            'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}
    try:
        target.write_text(yaml.safe_dump(override, sort_keys=False))
        after = configuration()
        for name, service in after['services'].items():
            if service.pop('logging') != {'driver': 'json-file', 'options': {'max-size': '10m', 'max-file': '3'}}:
                raise RuntimeError('Unexpected logging configuration')
            before['services'][name].pop('logging', None)
        if before != after:
            raise RuntimeError('Migration changed configuration other than logging')
    except Exception:
        shutil.copy2(backup, target)
        raise
    print('Validated: only logging changed. Recreating existing services without pulling or building images.', flush=True)
    subprocess.run(compose + ['up', '-d', '--wait', '--wait-timeout', '240', '--pull', 'never', '--no-build'],
                   cwd=directory, check=True)
    ids = subprocess.check_output(compose + ['ps', '-q'], cwd=directory, text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True))
    for container in containers:
        logging = container['HostConfig']['LogConfig']
        if logging != {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}:
            raise RuntimeError('Container did not receive log rotation: ' + container['Name'])
        print(container['Name'] + ': log rotation verified')


if __name__ == '__main__':
    main()
