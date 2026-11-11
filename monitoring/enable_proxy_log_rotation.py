#!/usr/bin/env python3
"""Recreate the ten existing Nginx proxies, retaining stopped originals for recovery."""
import json
import subprocess
import time

SERVICES = {
    'config-server': ('knitty-config-router', 5678, '/bff/prod'),
    'bff-service': ('knitty-bff-router', 8877, '/bff/actuator/health'),
    'user-service': ('knitty-user-router', 8887, '/users/actuator/health'),
    'newsletter-service': ('knitty-newsletter-router', 8888, '/newsletter/actuator/health'),
    'pattern-service': ('knitty-pattern-router', 8889, '/patterns/actuator/health'),
}


def run(arguments):
    return subprocess.check_output(arguments, text=True, stderr=subprocess.PIPE).strip()


def recreate(name, port, path):
    original = json.loads(run(['docker', 'inspect', name]))[0]
    limits = {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}
    if original['HostConfig']['LogConfig'] == limits:
        print(name + ': rotation already enabled', flush=True)
        return
    if not original['State']['Running'] or not original['Config']['Image'].startswith('nginx@sha256:'):
        raise RuntimeError('Expected a running pinned Nginx proxy: ' + name)
    networks = original['NetworkSettings']['Networks']
    if list(networks) != ['supabase_default']:
        raise RuntimeError('Unexpected proxy networks')
    network = networks['supabase_default']
    backup = name + '-before-log-rotation'
    if run(['docker', 'ps', '-aq', '--filter', 'name=^/' + backup + '$']):
        raise RuntimeError('Backup already exists: ' + backup)
    host, config = original['HostConfig'], original['Config']
    arguments = ['docker', 'create', '--name', name, '--restart', host['RestartPolicy']['Name'],
                 '--network', 'supabase_default', '--ip', network['IPAddress'],
                 '--log-driver', 'json-file', '--log-opt', 'max-size=10m', '--log-opt', 'max-file=3',
                 '--memory', str(host['Memory']), '--user', config['User'],
                 '--entrypoint', config['Entrypoint'][0]]
    if host['ReadonlyRootfs']:
        arguments.append('--read-only')
    for alias in network.get('Aliases') or []:
        arguments += ['--network-alias', alias]
    for capability in host.get('CapDrop') or []:
        arguments += ['--cap-drop', capability]
    for mount in host.get('Binds') or []:
        arguments += ['--volume', mount]
    for location, options in (host.get('Tmpfs') or {}).items():
        arguments += ['--tmpfs', location + ':' + options]
    for container_port, bindings in (host.get('PortBindings') or {}).items():
        for binding in bindings:
            arguments += ['--publish', binding['HostIp'] + ':' + binding['HostPort'] + ':' + container_port]
    for key, value in (config.get('Labels') or {}).items():
        arguments += ['--label', key + '=' + value]
    for value in config.get('Env') or []:
        arguments += ['--env', value]
    health = config.get('Healthcheck')
    if health:
        if health['Test'][0] != 'CMD-SHELL':
            raise RuntimeError('Unexpected proxy healthcheck')
        arguments += ['--health-cmd', health['Test'][1]]
        for key, flag in [('Interval', '--health-interval'), ('Timeout', '--health-timeout'),
                          ('StartPeriod', '--health-start-period')]:
            if health.get(key):
                arguments += [flag, str(health[key]) + 'ns']
        if health.get('Retries'):
            arguments += ['--health-retries', str(health['Retries'])]
    arguments += [original['Image']] + config['Cmd']
    disconnected = False
    renamed = False
    try:
        run(['docker', 'stop', '--time', '320', name])
        run(['docker', 'network', 'disconnect', 'supabase_default', name])
        disconnected = True
        run(['docker', 'rename', name, backup])
        renamed = True
        run(arguments)
        run(['docker', 'start', name])
        for attempt in range(30):
            try:
                payload = json.loads(run(['docker', 'exec', name, 'wget', '-T', '5', '-q', '-O', '-',
                                          f'http://127.0.0.1:{port}{path}']))
                if payload.get('status') == 'UP' or payload.get('propertySources'):
                    break
            except (subprocess.CalledProcessError, ValueError):
                pass
            time.sleep(1)
        else:
            raise RuntimeError('Proxy did not become healthy: ' + name)
        updated = json.loads(run(['docker', 'inspect', name]))[0]
        if updated['HostConfig']['LogConfig'] != limits:
            raise RuntimeError('Proxy logging limits differ')
    except Exception:
        if renamed:
            subprocess.run(['docker', 'rm', '-f', name], stdout=subprocess.DEVNULL, stderr=subprocess.DEVNULL)
            run(['docker', 'rename', backup, name])
        if disconnected:
            reconnect = ['docker', 'network', 'connect', '--ip', network['IPAddress']]
            for alias in network.get('Aliases') or []:
                reconnect += ['--alias', alias]
            run(reconnect + ['supabase_default', name])
        run(['docker', 'start', name])
        raise
    print(name + ': healthy with log rotation; original retained stopped', flush=True)


if __name__ == '__main__':
    for service, (router, port, path) in SERVICES.items():
        recreate(router, port, path)
        recreate(service, port, path)
