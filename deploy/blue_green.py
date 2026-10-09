#!/usr/bin/env python3
"""Blue/green deployment for the five KnittyNetwork application services."""
import argparse
from contextlib import contextmanager
import fcntl
import ipaddress
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile
import time
import urllib.error
import urllib.request

NETWORK = 'supabase_default'
ROUTER_IMAGE = 'nginx@sha256:0985e772fb9f729e6fa0980da05fca5d9c468e870eed43071545afa9d2e27d94'
SERVICES = {
    'bff-service': (8877, 19877, '/bff/actuator/health', '768m', 'knitty-bff-router'),
    'user-service': (8887, 19887, '/users/actuator/health', '1024m', 'knitty-user-router'),
    'pattern-service': (8889, 19889, '/patterns/actuator/health', '1536m', 'knitty-pattern-router'),
    'newsletter-service': (8888, 19888, '/newsletter/actuator/health', '768m', 'knitty-newsletter-router'),
    'config-server': (5678, 15678, '/actuator/health', '768m', 'knitty-config-router'),
}


def service_spec(service):
    if service not in SERVICES:
        raise ValueError('Unknown application service')
    return SERVICES[service]


def validate_image(service, image):
    service_spec(service)
    if not re.fullmatch(re.escape(service) + r':[0-9a-f]{40}', image):
        raise ValueError('Use the service image tagged with its full Git commit SHA')


def valid_container(service, name):
    return name == service or re.fullmatch(re.escape(service) + r'-(blue|green)(-[0-9a-f]{12})?', name) is not None


def command(arguments):
    result = subprocess.run(arguments, capture_output=True, text=True)
    if result.returncode:
        # Do not dump Docker inspect/environment data or subprocess output.
        raise RuntimeError('Command failed: ' + ' '.join(arguments[:4]))
    return result.stdout.strip()


def inspect(container):
    return json.loads(command(['docker', 'inspect', container]))[0]


def container_ip(data):
    address = data['NetworkSettings']['Networks'][NETWORK]['IPAddress']
    ipaddress.ip_address(address)
    return address


def atomic_write(path, content, mode=0o600):
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, temporary = tempfile.mkstemp(dir=path.parent, prefix='.pending-')
    try:
        os.fchmod(fd, mode)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
            stream.flush()
            os.fsync(stream.fileno())
        os.replace(temporary, path)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)


@contextmanager
def docker_environment(content):
    # Snap Docker cannot read hidden home paths. Keep a private, short-lived
    # copy in an accessible directory, rather than putting secrets in argv.
    directory = Path.home() / 'knitty-blue-green-runtime'
    if directory.is_symlink():
        raise RuntimeError('Runtime environment directory must not be a symlink')
    directory.mkdir(mode=0o700, exist_ok=True)
    os.chmod(directory, 0o700)
    fd, temporary = tempfile.mkstemp(dir=directory, prefix='environment-')
    try:
        os.fchmod(fd, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
        yield Path(temporary)
    finally:
        os.unlink(temporary)


def environment_overrides(container_env, image_env):
    defaults = dict(item.split('=', 1) for item in image_env)
    overrides = {}
    for item in container_env:
        name, value = item.split('=', 1)
        if not re.fullmatch(r'[A-Za-z_][A-Za-z0-9_]*', name) or '\n' in value or '\r' in value:
            raise ValueError('Invalid container environment entry')
        if defaults.get(name) != value:
            overrides[name] = value
    return overrides


def proxy_config(service, backend, identity):
    port = service_spec(service)[0]
    if not valid_container(service, backend):
        ipaddress.ip_address(backend)
    if not valid_container(service, identity):
        raise ValueError('Invalid backend identity')
    return f'''worker_processes auto;
error_log /dev/stderr warn;
events {{ worker_connections 1024; }}
http {{
    access_log off;
    resolver 127.0.0.11 valid=5s ipv6=off;
    map $http_upgrade $connection_upgrade {{ default upgrade; '' ''; }}
    map $http_x_forwarded_proto $forwarded_proto {{ default $http_x_forwarded_proto; '' $scheme; }}
    map $http_x_forwarded_host $forwarded_host {{ default $http_x_forwarded_host; '' $host; }}
    server {{
        listen {port};
        client_max_body_size 60m;
        location / {{
            set $backend "{backend}:{port}";
            proxy_pass http://$backend;
            proxy_http_version 1.1;
            proxy_set_header Host $host;
            proxy_set_header X-Forwarded-Host $forwarded_host;
            proxy_set_header X-Forwarded-Proto $forwarded_proto;
            proxy_set_header X-Forwarded-For $proxy_add_x_forwarded_for;
            proxy_set_header Upgrade $http_upgrade;
            proxy_set_header Connection $connection_upgrade;
            proxy_connect_timeout 5s;
            proxy_read_timeout 5m;
            proxy_send_timeout 120s;
            proxy_request_buffering off;
            proxy_buffering off;
            proxy_hide_header X-Knitty-Backend;
            add_header X-Knitty-Backend "{identity}" always;
        }}
    }}
}}
'''


def probe(url, expected_backend=None):
    try:
        with urllib.request.urlopen(url, timeout=5) as response:
            if expected_backend and response.headers.get('X-Knitty-Backend') != expected_backend:
                return False
            payload = json.load(response)
            if url.endswith('/bff/prod'):
                return response.status == 200 and payload.get('name') == 'bff' and bool(payload.get('propertySources'))
            return response.status == 200 and payload.get('status') == 'UP'
    except (OSError, ValueError, urllib.error.URLError):
        return False


def wait_healthy(url, expected_backend=None, timeout=300):
    deadline = time.monotonic() + timeout
    consecutive = 0
    while time.monotonic() < deadline:
        consecutive = consecutive + 1 if probe(url, expected_backend) else 0
        if consecutive >= 3:
            return
        time.sleep(1)
    raise RuntimeError('Backend did not become healthy; traffic stays on the previous release')


def verify_stable(url, backend, seconds=15):
    deadline = time.monotonic() + seconds
    failures = 0
    while time.monotonic() < deadline:
        failures = 0 if probe(url, backend) else failures + 1
        if failures >= 3:
            raise RuntimeError('New route became unhealthy; restoring the previous release')
        time.sleep(1)


class Deployment:
    def __init__(self, service, root):
        self.service = service
        self.port, self.front_port, self.health, self.memory, self.router = service_spec(service)
        self.root = root
        self.directory = root / service
        self.router_dir = self.directory / 'router'
        self.config_path = self.router_dir / 'nginx.conf'
        self.state_path = self.directory / 'state.json'
        self.env_path = self.directory / 'runtime.env'
        self.router_health = f'http://127.0.0.1:{self.front_port}{self.health}'
        self.keep_candidate = False

    def save(self, state):
        atomic_write(self.state_path, json.dumps(state, indent=2) + '\n')

    def state(self):
        state = json.loads(self.state_path.read_text())
        if not valid_container(self.service, state['active']['container']):
            raise ValueError('Invalid saved active container')
        if state.get('previous') and not valid_container(self.service, state['previous']['container']):
            raise ValueError('Invalid saved rollback container')
        return state

    def backend(self, container):
        data = inspect(container)
        return {'container': container, 'backend': container_ip(data) if container == self.service else container,
                'image': data['Image']}

    def backend_health(self, container):
        return f'http://{container_ip(inspect(container))}:{self.port}{self.health_path(container)}'

    def health_path(self, container):
        return '/bff/prod' if self.service == 'config-server' and container == self.service else self.health

    def router_health_for(self, container):
        return f'http://127.0.0.1:{self.front_port}{self.health_path(container)}'

    def bootstrap(self):
        if self.state_path.exists():
            active = self.state()['active']['container']
            wait_healthy(self.router_health_for(active), active)
            print(self.service + ': existing router is healthy')
            return
        original = inspect(self.service)
        if not original['State']['Running']:
            raise RuntimeError('Bootstrap requires the existing service to be running')
        image = json.loads(command(['docker', 'image', 'inspect', original['Image']]))[0]
        environment = environment_overrides(original['Config'].get('Env', []), image['Config'].get('Env', []))
        self.directory.mkdir(parents=True, exist_ok=True)
        os.chmod(self.directory, 0o700)
        self.router_dir.mkdir(exist_ok=True)
        atomic_write(self.env_path, ''.join(key + '=' + value + '\n' for key, value in environment.items()))
        active = self.backend(self.service)
        atomic_write(self.config_path, proxy_config(self.service, active['backend'], active['container']), 0o644)
        mounts = [mount for mount in original['Mounts'] if mount['Type'] == 'bind']
        if any(mount['Destination'] != '/security' for mount in mounts):
            raise RuntimeError('Unexpected mount; review it before bootstrap')
        command(['docker', 'run', '-d', '--name', self.router, '--restart', 'unless-stopped',
                 '--network', NETWORK, '--entrypoint', '/usr/sbin/nginx',
                 '--user', '101:101', '--cap-drop', 'ALL',
                 '--memory', '128m', '--read-only',
                 '--tmpfs', '/var/cache/nginx:uid=101,gid=101,mode=0700',
                 '--tmpfs', '/var/run:uid=101,gid=101,mode=0700',
                 '-p', f'127.0.0.1:{self.front_port}:{self.port}',
                 '-v', str(self.router_dir) + ':/etc/nginx:ro', ROUTER_IMAGE, '-g', 'daemon off;'])
        try:
            wait_healthy(self.router_health_for(self.service), self.service)
            # Register the legacy DNS name only after the proxy is ready.
            # Public host routing is not switched during this bootstrap step.
            router_ip = container_ip(inspect(self.router))
            command(['docker', 'network', 'disconnect', NETWORK, self.router])
            command(['docker', 'network', 'connect', '--ip', router_ip, '--alias', self.service, NETWORK, self.router])
            wait_healthy(self.router_health_for(self.service), self.service)
            self.save({'active': active, 'previous': None, 'mounts': mounts})
        except Exception:
            command(['docker', 'rm', '-f', self.router])
            raise
        print(self.service + ': router added; existing application is unchanged')

    def reload(self):
        command(['docker', 'exec', self.router, 'nginx', '-t'])
        command(['docker', 'exec', self.router, 'nginx', '-s', 'reload'])

    def switch(self, previous, candidate):
        before = self.config_path.read_text()
        try:
            atomic_write(self.config_path, proxy_config(self.service, candidate['backend'], candidate['container']), 0o644)
            self.reload()
            wait_healthy(self.router_health_for(candidate['container']), candidate['container'], timeout=30)
        except Exception:
            atomic_write(self.config_path, before, 0o644)
            try:
                self.reload()
                wait_healthy(self.router_health_for(previous['container']), previous['container'], timeout=30)
            except Exception:
                self.keep_candidate = True
                raise RuntimeError('Router recovery needs attention; both backends were kept running')
            raise

    def drained(self, container):
        if not inspect(container)['State']['Running']:
            return True
        if 'worker process is shutting down' in command(['docker', 'exec', self.router, 'ps', '-o', 'args']):
            return False
        if self.service != 'pattern-service':
            return True
        if container == self.service:
            count = command(['docker', 'exec', 'supabase-db', 'psql', '-U', 'postgres', '-d', 'pattern', '-Atc',
                             "SELECT count(*) FROM upload_job WHERE status NOT IN ('COMPLETED', 'FAILED')"])
            return count == '0'
        url = f'http://{container_ip(inspect(container))}:{self.port}/patterns/actuator/deployment'
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                return json.load(response)['runningUploads'] == 0
        except (OSError, ValueError, KeyError, urllib.error.URLError):
            return False

    def retire(self, container, timeout=30):
        deadline = time.monotonic() + timeout
        while time.monotonic() < deadline:
            if self.drained(container):
                if inspect(container)['State']['Running']:
                    command(['docker', 'stop', '--time', '320', container])
                print(container + ': stopped after requests and background jobs drained; retained for rollback')
                return True
            time.sleep(1)
        print(container + ': still draining; left running to preserve existing work')
        return False

    def candidate_environment(self):
        environment = dict(line.split('=', 1) for line in self.env_path.read_text().splitlines())
        environment['SERVER_SHUTDOWN'] = 'graceful'
        environment['SPRING_LIFECYCLE_TIMEOUT_PER_SHUTDOWN_PHASE'] = '300s'
        environment['SPRING_TASK_EXECUTION_SHUTDOWN_AWAIT_TERMINATION'] = 'true'
        environment['SPRING_TASK_EXECUTION_SHUTDOWN_AWAIT_TERMINATION_PERIOD'] = '300s'
        environment['MANAGEMENT_ENDPOINT_HEALTH_PROBES_ENABLED'] = 'true'
        if self.service == 'pattern-service':
            environment['MANAGEMENT_ENDPOINTS_WEB_EXPOSURE_INCLUDE'] = 'health,deployment'
        options = environment.get('JAVA_TOOL_OPTIONS', '')
        if '-Xmx' not in options and 'MaxRAMPercentage' not in options:
            environment['JAVA_TOOL_OPTIONS'] = (options + ' -XX:MaxRAMPercentage=70.0').strip()
        if self.service != 'config-server':
            # Environment imports resolve before profile-specific YAML. Enable
            # the resolver at the same early stage as its configserver import.
            environment['SPRING_CLOUD_CONFIG_ENABLED'] = 'true'
            environment['SPRING_CONFIG_IMPORT'] = 'configserver:http://knitty-config-router:5678'
        else:
            for name in ['GITHUB_USERNAME', 'GITHUB_TOKEN']:
                if os.environ.get(name):
                    environment[name] = os.environ[name]
        environment_overrides([key + '=' + value for key, value in environment.items()], [])
        atomic_write(self.env_path, ''.join(key + '=' + value + '\n' for key, value in environment.items()))

    def deploy(self, image):
        self.keep_candidate = False
        validate_image(self.service, image)
        if not (self.root / 'host-routes-ready').exists():
            raise RuntimeError('Activate the reviewed host Nginx routing before enabling these workflows')
        state = self.state()
        previous = state['active']
        wait_healthy(self.router_health_for(previous['container']), previous['container'], timeout=30)
        revision = image.split(':', 1)[1]
        if previous.get('source') == revision:
            print(self.service + ': this commit is already healthy and active')
            return
        color = 'green' if previous.get('color') == 'blue' else 'blue'
        candidate_name = self.service + '-' + color + '-' + revision[:12]
        listed = command(['docker', 'ps', '-a', '--filter', 'name=^/' + candidate_name + '$', '--format', '{{.Names}}'])
        if listed:
            existing = inspect(candidate_name)
            if existing['Config'].get('Labels', {}).get('org.knitty.bluegreen.service') != self.service:
                raise RuntimeError('Candidate name belongs to an unmanaged container')
            if candidate_name == (state.get('previous') or {}).get('container'):
                raise RuntimeError('This candidate is still the rollback release; use rollback instead')
            if existing['State']['Running']:
                raise RuntimeError('A candidate with this revision is still running; review it before retrying')
            command(['docker', 'rm', candidate_name])
        available = next(int(line.split()[1]) // 1024 for line in Path('/proc/meminfo').read_text().splitlines()
                         if line.startswith('MemAvailable:'))
        if available < int(self.memory[:-1]) + 256:
            raise RuntimeError('Not enough spare memory to warm the candidate safely; active release was kept')
        self.candidate_environment()
        arguments = ['docker', 'run', '-d', '--name', candidate_name, '--restart', 'unless-stopped',
                     '--network', NETWORK, '--label', 'org.knitty.bluegreen.service=' + self.service,
                     '--memory', self.memory, '--stop-timeout', '320']
        for mount in state['mounts']:
            arguments += ['-v', mount['Source'] + ':' + mount['Destination'] + (':ro' if not mount['RW'] else '')]
        with docker_environment(self.env_path.read_text()) as environment_file:
            command(arguments + ['--env-file', str(environment_file), image])
        try:
            candidate = self.backend(candidate_name)
            candidate.update({'color': color, 'source': revision})
            wait_healthy(self.backend_health(candidate_name))
            if self.service == 'config-server':
                with urllib.request.urlopen(f'http://{container_ip(inspect(candidate_name))}:5678/bff/prod', timeout=30) as response:
                    if not json.load(response).get('propertySources'):
                        raise RuntimeError('Config candidate cannot fetch application configuration')
            self.switch(previous, candidate)
            self.keep_candidate = True
            try:
                verify_stable(self.router_health_for(candidate_name), candidate_name)
                retained = state.get('retained', [])
                if state.get('previous'):
                    retained = retained + [state['previous']['container']]
                self.save({**state, 'active': candidate, 'previous': previous, 'retained': retained})
            except Exception:
                self.switch(candidate, previous)
                self.keep_candidate = False
                raise
        except Exception:
            if not self.keep_candidate:
                command(['docker', 'rm', '-f', candidate_name])
            raise
        self.retire(previous['container'])
        current = self.state()
        retained = []
        for container in current.get('retained', []):
            if not valid_container(self.service, container):
                raise ValueError('Invalid retained container')
            if self.retire(container, timeout=1):
                command(['docker', 'rm', container])
            else:
                retained.append(container)
        self.save({**current, 'retained': retained})
        print(self.service + ': new release is healthy and receives traffic')

    def rollback(self):
        state = self.state()
        previous = state.get('previous')
        if not previous:
            raise RuntimeError('No previous release is available')
        if not inspect(previous['container'])['State']['Running']:
            command(['docker', 'start', previous['container']])
        wait_healthy(self.backend_health(previous['container']))
        previous = {**previous, **self.backend(previous['container'])}
        self.switch(state['active'], previous)
        try:
            verify_stable(self.router_health_for(previous['container']), previous['container'])
            self.save({**state, 'active': previous, 'previous': state['active']})
        except Exception:
            self.switch(previous, state['active'])
            raise
        self.retire(state['active']['container'])
        print(self.service + ': previous healthy release restored')


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument('action', choices=['bootstrap', 'deploy', 'rollback', 'status'])
    parser.add_argument('--service', required=True, choices=SERVICES)
    parser.add_argument('--image')
    parser.add_argument('--root', type=Path, default=Path.home() / '.local/share/knitty-blue-green')
    args = parser.parse_args()
    args.root.mkdir(parents=True, exist_ok=True)
    os.chmod(args.root, 0o700)
    with (args.root / 'deployment.lock').open('a') as lock:
        deadline = time.monotonic() + 1800
        while True:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
                break
            except BlockingIOError:
                if time.monotonic() > deadline:
                    raise RuntimeError('Another service deployment still holds the host lock')
                time.sleep(2)
        deployment = Deployment(args.service, args.root)
        if args.action == 'bootstrap':
            deployment.bootstrap()
        elif args.action == 'deploy':
            deployment.deploy(args.image or '')
        elif args.action == 'rollback':
            deployment.rollback()
        else:
            state = deployment.state()
            print(json.dumps({'service': args.service, 'active': state['active']['container'],
                              'previous': (state.get('previous') or {}).get('container'),
                              'healthy': probe(deployment.router_health_for(state['active']['container']), state['active']['container'])}))


if __name__ == '__main__':
    main()
