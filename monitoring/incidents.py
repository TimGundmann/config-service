#!/usr/bin/env python3
"""Private, local incident spool. Only a structural log projection leaves the host."""
import argparse
import datetime as dt
import hashlib
import json
import os
from pathlib import Path
import re
import subprocess
import tempfile

SERVICES = {
    'bff-service': 'TimGundmann/bff',
    'config-server': 'TimGundmann/config-service',
    'newsletter-service': 'TimGundmann/newsletter-service',
    'pattern-service': 'TimGundmann/pattern-service',
    'user-service': 'TimGundmann/user-service',
}
STAMP = re.compile(r'^\d{4}-\d\d-\d\dT[\d:.]+Z[ \t]')
ERROR = re.compile(r'\b(?:ERROR|FATAL|PANIC|OutOfMemoryError|OOM|[\w.$]+Exception)\b', re.I)
SYMBOL = r'(?:java|javax|jakarta|sun|jdk|org|com|dk|reactor|io|net)\.[\w.$]+'
EXCEPTION = re.compile(r'\b' + SYMBOL + r'(?:Exception|Error)\b')
FRAME = re.compile(r'^\s*(?:at|Suppressed:|Caused by:)\s|^\s*\.\.\. \d+ (?:common )?frames omitted')
METHOD = re.compile(r'^\s*at ((?:[\w.@+\-]+/)?' + SYMBOL + r'\((?:[\w.$]+(?::\d+)?|Native Method|Unknown Source)\))')
LOGGER = re.compile(r'\b(' + SYMBOL + r')\s+:')
# Literal diagnostic vocabulary, never arbitrary values, URLs, SQL or user messages.
DIAGNOSTICS = (
    'Connection refused', 'Connection timed out', 'Read timed out', 'UnknownHostException',
    'Could not resolve placeholder', 'Failed to bind properties', 'Permission denied',
    'No space left on device', 'Too many open files', 'OutOfMemoryError',
    'SSLHandshakeException', 'PKIX path building failed', 'authentication failed',
    'password authentication failed', 'does not exist', 'duplicate key', 'deadlock detected',
    'connection reset', 'Connection reset', 'Broken pipe', 'CannotGetJdbcConnectionException',
    'Unable to acquire JDBC Connection', 'Failed to obtain JDBC Connection',
    'Could not fetch user name', 'not authorized', 'invalid token', 'JWT expired',
    'APPLICATION FAILED TO START', 'Service Unavailable', 'Bad Gateway', 'Not Found',
    'Cannot clone or checkout repository', 'Cannot pull from remote', 'TransportException',
    'No such file or directory', 'could not connect to server', 'timeout',
    'No such label: main', 'No such label: master', 'Ref main can not be resolved',
    'Ref master can not be resolved', 'no Jakarta Bean Validation provider could be found',
    'hibernate-validator', 'NoSuchLabelException', 'RefNotFoundException',
)


def atomic_save(path, value):
    path.parent.mkdir(parents=True, exist_ok=True, mode=0o700)
    descriptor, name = tempfile.mkstemp(dir=path.parent)
    with os.fdopen(descriptor, 'w', encoding='utf-8') as stream:
        json.dump(value, stream, indent=2)
        stream.write('\n')
        stream.flush()
        os.fsync(stream.fileno())
    os.chmod(name, 0o600)
    os.replace(name, path)


def identity(container):
    name = container['Name'].lstrip('/')
    service = next((s for s in SERVICES if name == s or name.startswith(s + '-blue-') or name.startswith(s + '-green-')), None)
    if not service:
        return None
    image = container['Config']['Image']
    tag = image.rsplit(':', 1)[-1]
    # Deployment images are tagged with the FULL source SHA. Never infer it from names.
    commit = tag if re.fullmatch('[a-f0-9]{40}', tag) else None
    return {'service': service, 'repository': SERVICES[service], 'container': name,
            'commit': commit, 'image_id': container.get('Image'), 'identity_source': 'docker-image-tag'}


def project(lines, secrets=()):
    result = []
    for raw in lines:
        peers = re.findall(r'https?://(localhost|127\.0\.0\.1|knitty-config-router|config-server|knitty-user-router|knitty-pattern-router|knitty-newsletter-router)(?::(5678|8888|8887|8889))?(?=[/\s"\']|$)', raw)
        for value in secrets:
            if value:
                raw = raw.replace(value, '[REMOVED]')
        stamp = STAMP.match(raw)
        body = raw[stamp.end():] if stamp else raw
        item = {'timestamp': stamp.group().strip() if stamp else None,
                'level': next((v for v in ('ERROR', 'FATAL', 'WARN', 'INFO', 'DEBUG', 'TRACE') if re.search(r'\b' + v + r'\b', body)), None),
                'exceptions': EXCEPTION.findall(body),
                'diagnostics': [value for value in DIAGNOSTICS if value in body],
                'message': '[REMOVED: free text and values]'}
        method = METHOD.match(body)
        if method:
            item['frame'] = method.group(1)
        omitted = re.match(r'^\s*(\.\.\. \d+ (?:common )?frames omitted)', body)
        if omitted:
            item['frame'] = omitted.group(1)
        logger = LOGGER.search(body)
        if logger:
            item['logger'] = logger.group(1)
        # Only predeclared infrastructure peers and ports, never arbitrary URL values.
        if peers:
            item['peers'] = sorted(set(host + (':' + port if port else '') for host, port in peers))
        if body.lstrip().startswith(('Caused by:', 'Suppressed:')):
            item['relation'] = body.strip().split(':', 1)[0]
        result.append(item)
    return result


def event_ranges(lines):
    """Each error and its full continuation; context is excluded from its identity."""
    windows, consumed = [], 0
    for index, raw in enumerate(lines):
        body = STAMP.sub('', raw)
        if index < consumed or not ERROR.search(body) or FRAME.match(body):
            continue
        end = index + 1
        while end < len(lines):
            following = STAMP.sub('', lines[end])
            if re.match(r'^\d{4}-\d\d-\d\d[T ]', following):
                break
            if not (FRAME.match(following) or EXCEPTION.search(following) or following[:1].isspace()):
                break
            end += 1
        consumed = end
        windows.append((max(0, index - 8), index, end, min(len(lines), end + 8)))
    return windows


def enqueue(directory, metadata, evidence, window):
    core = [item for item in evidence if item.get('role', 'error') == 'error']
    exceptions = [(i, name) for i, line in enumerate(core) for name in line['exceptions']]
    root_index, root = exceptions[-1] if exceptions else (0, None)
    # Identify by the deepest cause and its first three frames, not servlet wrappers.
    canonical = {'root': root,
                 'frames': [line['frame'] for line in core[root_index:] if 'frame' in line][:3],
                 'diagnostics': sorted({v for line in core for v in line['diagnostics']})}
    family = hashlib.sha256(json.dumps([metadata['repository'], canonical], sort_keys=True).encode()).hexdigest()
    digest = hashlib.sha256(json.dumps([metadata['repository'], metadata['commit'], canonical], sort_keys=True).encode()).hexdigest()
    path = directory / (digest + '.json')
    now = dt.datetime.now(dt.timezone.utc).isoformat()
    if path.exists():
        value = json.loads(path.read_text())
        if window in value['windows']:
            return digest
        value['windows'].append(window)
        value['occurrences'] += 1
        value['last_seen'] = now
    else:
        value = {'id': digest, 'family': family, **metadata, 'status': 'pending' if metadata['commit'] else 'blocked-identity',
                 'first_seen': now, 'last_seen': now, 'occurrences': 1, 'windows': [window],
                 'projection': 'structural-v1: all free text/values removed; stack symbols and fixed diagnostics only',
                 'evidence': evidence}
    atomic_save(path, value)
    return digest


def capture(container, lines, base, window, complete=True):
    metadata = identity(container)
    if metadata is None:
        return []
    # Remove actual runtime values before any symbol extraction, even if they resemble code.
    secrets = [v.split('=', 1)[1] for v in container['Config'].get('Env', [])
               if '=' in v and re.search(r'password|pass$|secret|token|key|credential|user|email|name|address', v.split('=', 1)[0], re.I)]
    found = []
    for start, error_start, error_end, stop in event_ranges(lines):
        projected = project(lines[start:stop], secrets)
        for index, line in enumerate(projected, start=start):
            line['role'] = 'error' if error_start <= index < error_end else 'context'
        found.append(enqueue(base / 'incidents', {**metadata, 'logs_complete': complete,
                                                'capture_window': window}, projected, window))
    return sorted(set(found))


def deliver_reports(base, sender):
    pending = []
    for path in sorted((base / 'incidents').glob('*.json')):
        value = json.loads(path.read_text())
        if value.get('report') and not value.get('mail_sent'):
            pending.append((path, value))
    if pending:
        sender('[Knittynetwork] Codex: undersøgte fejlsager', '\n\n---\n\n'.join(
            'Incident: ' + value['id'] + '\nService: ' + value['service'] +
            '\nStatus: ' + value['status'] + '\nRepository: ' + value['repository'] +
            '\nKørende commit: ' + str(value['commit']) + '\n\n' + value['report']
            for _, value in pending))
        for path, value in pending:
            value['mail_sent'] = True
            atomic_save(path, value)


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument('--base', type=Path, default=Path(__file__).resolve().parent)
    sub = parser.add_subparsers(dest='command', required=True)
    sub.add_parser('list')
    show = sub.add_parser('show')
    show.add_argument('id')
    finish = sub.add_parser('resolve')
    finish.add_argument('id')
    finish.add_argument('--status', choices=['operations', 'configuration', 'insufficient-evidence', 'draft-pr', 'blocked'], required=True)
    finish.add_argument('--report-file', type=Path, required=True)
    backfill = sub.add_parser('backfill')
    backfill.add_argument('--since', required=True)
    backfill.add_argument('--until', required=True)
    backfill.add_argument('containers', nargs='+', choices=None)
    args = parser.parse_args()
    os.umask(0o077)
    with (args.base / 'monitor.lock').open('a') as lock:
        fcntl.flock(lock, fcntl.LOCK_EX)
        if args.command == 'list':
            for path in sorted((args.base / 'incidents').glob('*.json')):
                value = json.loads(path.read_text())
                print(json.dumps({k: v for k, v in value.items() if k != 'evidence'}))
        elif args.command in ('show', 'resolve'):
            if not re.fullmatch('[a-f0-9]{64}', args.id):
                raise ValueError('Invalid incident id')
            path = args.base / 'incidents' / (args.id + '.json')
            value = json.loads(path.read_text())
            if args.command == 'show':
                print(json.dumps(value))
            else:
                value.update(status=args.status, report=args.report_file.read_text(), mail_sent=False,
                             resolved_at=dt.datetime.now(dt.timezone.utc).isoformat())
                atomic_save(path, value)
        else:
            from monitor import run
            for name in args.containers:
                container = json.loads(run(['docker', 'inspect', name]))[0]
                with tempfile.TemporaryFile(mode='w+t') as output:
                    proc = subprocess.run(['docker', 'logs', '--timestamps', '--since', args.since, '--until', args.until, name],
                                          stdout=output, stderr=output, timeout=120)
                    if proc.returncode:
                        raise RuntimeError('Historical logs unavailable')
                    output.seek(0)
                    ids = capture(container, output.readlines(), args.base, args.since + '/' + args.until)
                print(json.dumps({'container': name, 'incidents': ids}))


if __name__ == '__main__':
    try:
        main()
    except Exception as exc:
        print('INCIDENT_FAILED: ' + type(exc).__name__)
        raise SystemExit(1)
