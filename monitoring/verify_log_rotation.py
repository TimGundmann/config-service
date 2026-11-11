#!/usr/bin/env python3
"""Read-only production checks; never print configuration values or raw logs."""
import json
from pathlib import Path
import subprocess
import urllib.request


def main():
    ids = subprocess.check_output(['docker', 'ps', '-q'], text=True).split()
    containers = json.loads(subprocess.check_output(['docker', 'inspect', *ids], text=True))
    limits = {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}
    invalid = [container['Name'] for container in containers if container['HostConfig']['LogConfig'] != limits]
    if invalid:
        raise RuntimeError('Running containers without expected log limits: ' + ', '.join(invalid))
    unhealthy = [container['Name'] for container in containers
                 if container['State'].get('Health', {}).get('Status', 'healthy') != 'healthy']
    if unhealthy:
        raise RuntimeError('Containers not healthy: ' + ', '.join(unhealthy))
    root = Path.home() / '.local/share/knitty-blue-green'
    for service in ['config-server', 'bff-service', 'user-service', 'newsletter-service', 'pattern-service']:
        state = json.loads((root / service / 'state.json').read_text())
        name = state['active']['container']
        if name not in [container['Name'].lstrip('/') for container in containers]:
            raise RuntimeError('Active application is not running: ' + service)
    active = json.loads((root / 'config-server/state.json').read_text())['active']['container']
    with urllib.request.urlopen('http://127.0.0.1:15678/bff/prod', timeout=30) as response:
        config = json.load(response)
    with urllib.request.urlopen('http://127.0.0.1:15678/bff/prod/master', timeout=30) as response:
        master = json.load(response)
    # An implicit default label is represented as null in the environment response.
    if (master.get('label') != 'master' or not config.get('propertySources')
            or not config.get('version') or config['version'] != master.get('version')):
        raise RuntimeError('Config Server did not serve master configuration')
    started = next(container['State']['StartedAt'] for container in containers if container['Name'] == '/' + active)
    logs = subprocess.check_output(['docker', 'logs', '--since', started, active], stderr=subprocess.STDOUT, text=True)
    if 'No such label: main' in logs or 'Could not refresh default label main' in logs:
        raise RuntimeError('Active Config Server still reports the main label error')
    with urllib.request.urlopen('https://knittynetwork.com', timeout=30) as response:
        if response.status != 200:
            raise RuntimeError('Public HTTPS check failed')
    print(f'PASS: {len(containers)} running containers have 3 x 10 MB log rotation and healthy status.')
    print('PASS: all five active application releases are running.')
    print('PASS: Config Server serves master configuration and has no main-label errors since startup.')
    print('PASS: public HTTPS returns 200.')


if __name__ == '__main__':
    main()
