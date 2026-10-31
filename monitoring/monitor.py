#!/usr/bin/env python3
"""Read-only hourly Knittynetwork checks; alerts only on incident changes."""
import argparse
import datetime as dt
import email.message
import json
import os
from pathlib import Path
import re
import smtplib
import ssl
import subprocess
import sys
import tempfile
import time
import urllib.request
from incidents import capture, deliver_reports

BASE = Path(__file__).resolve().parent
ERROR = re.compile(r'\b(?:ERROR|FATAL|PANIC|OutOfMemoryError|OOM|[\w.$]+Exception)\b', re.I)


def disk_issues(mount, used, inodes):
    issues = {}
    for kind, value in [('disk', used), ('inodes', inodes)]:
        if value >= 90:
            issues[f'{kind}:{mount}'] = {'count': 1, 'percent': round(value, 1),
                                        'message': f'{kind} {mount}: {value:.1f}% brugt (grænse 90%)'}
    return issues


def log_summary(lines):
    # Deliberately keep only counts and Docker timestamps, never raw application data.
    count, first, last = 0, None, None
    for line in lines:
        if ERROR.search(line):
            count += 1
            stamp = line.split(' ', 1)[0]
            if re.fullmatch(r'\d{4}-\d\d-\d\dT[\d:.]+Z', stamp):
                first = first or stamp
                last = stamp
    return {'count': count, 'first': first, 'last': last}


def new_log_lines(lines, since):
    for line in lines:
        stamp = line.split(' ', 1)[0]
        try:
            if dt.datetime.fromisoformat(stamp.replace('Z', '+00:00')).timestamp() < since:
                continue
        except ValueError:
            pass
        yield line


def notification_needed(previous, current):
    if set(previous) != set(current):
        return True
    return any(v.get('count', 1) >= max(2, previous[k].get('count', 1) * 2)
               or v.get('percent', 0) >= previous[k].get('percent', 0) + 5
               for k, v in current.items())


def run(args, timeout=30):
    p = subprocess.run(args, capture_output=True, text=True, timeout=timeout)
    if p.returncode:
        raise RuntimeError(f'{args[0]} failed (exit {p.returncode})')
    return p.stdout


def save(path, data):
    temporary = path.with_suffix('.tmp')
    temporary.write_text(json.dumps(data, indent=2) + '\n')
    temporary.chmod(0o600)
    temporary.replace(path)


def send_mail(subject, body):
    data = json.loads(run(['docker', 'inspect', 'supabase-auth']))[0]
    env = dict(v.split('=', 1) for v in data['Config']['Env'])
    prefix = 'GOTRUE_SMTP_'
    host, port = env[prefix + 'HOST'], int(env[prefix + 'PORT'])
    message = email.message.EmailMessage()
    message['From'] = env[prefix + 'ADMIN_EMAIL']
    message['To'] = 'tim@gundmann.dk'
    message['Subject'] = subject
    message.set_content(body)
    context = ssl.create_default_context()
    client = smtplib.SMTP_SSL if port == 465 else smtplib.SMTP
    options = {'context': context} if port == 465 else {}
    with client(host, port, timeout=25, **options) as smtp:
        if port != 465:
            smtp.starttls(context=context)
        smtp.login(env[prefix + 'USER'], env[prefix + 'PASS'])
        if smtp.send_message(message):
            raise RuntimeError('SMTP rejected recipient')


def collect(config, state, now):
    issues, cursors, restarts = {}, dict(state.get('cursors', {})), {}

    def issue(key, message, **extra):
        issues[key] = {'count': 1, 'message': message, **extra}

    for attempt in range(2):
        try:
            with urllib.request.urlopen('https://knittynetwork.com', timeout=20) as response:
                if response.status != 200:
                    raise RuntimeError('unexpected HTTP status')
            break
        except Exception:
            if attempt:
                issue('https', 'Knittynetwork HTTPS fejlede ved to forsøg. Kontroller nginx, TLS og DNS.')
            else:
                time.sleep(3)
    try:
        if run(['systemctl', 'is-active', 'nginx']).strip() != 'active':
            raise RuntimeError('inactive')
    except Exception:
        issue('service:nginx', 'nginx er ikke aktiv, eller status kan ikke læses. Kontroller systemctl status nginx.')
    try:
        exclude = ['-x', 'tmpfs', '-x', 'devtmpfs', '-x', 'overlay', '-x', 'squashfs']
        inode_rows = run(['df', '-Pi', *exclude]).splitlines()[1:]
        inode_usage = {r.split(maxsplit=5)[5]: float(r.split()[4].strip('%')) for r in inode_rows}
        for row in run(['df', '-Pk', *exclude]).splitlines()[1:]:
            columns = row.split(maxsplit=5)
            mount, used = columns[5], float(columns[4].strip('%'))
            found = disk_issues(mount, used, inode_usage[mount])
            for value in found.values():
                value['available_gib'] = round(int(columns[3]) / 1024**2, 2)
            issues.update(found)
    except Exception:
        issue('check:disk', 'Disk- eller inodekontrol kunne ikke gennemføres. Kontroller df -h og df -i.')
    try:
        ids = run(['docker', 'ps', '-aq']).split()
        containers = json.loads(run(['docker', 'inspect', *ids])) if ids else []
        by_name = {v['Name'].lstrip('/'): v for v in containers}
        expected = set(config['containers'])
        for service in config['releases']:
            try:
                release = Path.home() / '.local/share/knitty-blue-green' / service / 'state.json'
                expected.add(json.loads(release.read_text())['active']['container'])
            except Exception:
                issue('check:release:' + service, f'Aktiv release for {service} kunne ikke bestemmes.')
        for name in sorted(expected):
            container = by_name.get(name)
            if container is None:
                issue('service:' + name, f'{name} mangler. Kontroller docker ps -a og release-status.')
                continue
            status = container['State']
            identity = container['Id']
            restarts[identity] = container['RestartCount']
            if not status['Running'] or status.get('Health', {}).get('Status', 'healthy') != 'healthy':
                issue('service:' + name, f'{name} er stoppet eller usund. Kontroller docker inspect og docker logs.')
            if status.get('OOMKilled'):
                issue('oom:' + name, f'{name} blev dræbt pga. hukommelsesmangel. Kontroller hukommelse og containergrænser.')
            previous_restarts = state.get('restarts', {}).get(identity, container['RestartCount'])
            if container['RestartCount'] > previous_restarts:
                issue('restart:' + name, f'{name} er genstartet siden sidste kontrol. Kontroller docker logs.',
                      count=container['RestartCount'] - previous_restarts)
            try:
                since = cursors.get(identity, now - 3600)
                # Stream to a temporary file rather than retaining arbitrary logs in memory.
                with tempfile.TemporaryFile(mode='w+t') as output:
                    p = subprocess.run(['docker', 'logs', '--tail', '100000', '--timestamps', '--since', str(max(0, since - 60)),
                                        '--until', str(now), name], stdout=output, stderr=output, timeout=60)
                    if p.returncode:
                        raise RuntimeError('logs unavailable')
                    output.seek(0)
                    summary = log_summary(new_log_lines(output, since))
                    output.seek(0)
                    capped = sum(1 for _ in output) >= 100000
                    # Queue before advancing the cursor. Retry is idempotent after SMTP failure.
                    if config.get('codex_incidents') and not config.get('_dry_run'):
                        output.seek(0)
                        capture(container, output.readlines(), BASE, f'{since}/{now}', complete=not capped)
                if summary['count']:
                    issues['logs:' + name] = {
                        **summary,
                        'message': f'{name}: {summary["count"]} fejlmarkeringer i nye logs. Kontroller docker logs --since 1h {name}.',
                    }
                if capped:
                    issue('check:logs:' + name, f'Logmængden for {name} ramte sikkerhedsgrænsen på 100000 linjer; perioden er ufuldstændigt kontrolleret.')
                else:
                    cursors[identity] = now
            except Exception:
                issue('check:logs:' + name, f'Logs for {name} kunne ikke læses. Logtidspunkt er ikke rykket frem.')
    except Exception:
        issue('check:docker', 'Docker-kontrol kunne ikke gennemføres. Kontroller Docker og brugerens adgang.')
    return issues, cursors, restarts


def report(issues, previous):
    lines = ['Server: gundmann.dk / Knittynetwork',
             'Kontroltid (UTC): ' + dt.datetime.now(dt.timezone.utc).isoformat(), '']
    if issues:
        lines.append('Aktuelle problemer:')
        for key, value in sorted(issues.items()):
            lines.append('- ' + value['message'])
            if value.get('first'):
                lines.append(f'  Første/seneste logtid: {value["first"]} / {value["last"]}')
            if 'available_gib' in value:
                lines.append(f'  Ledig diskplads: {value["available_gib"]} GiB')
    else:
        lines.append('De tidligere registrerede problemer ses ikke længere.')
    recovered = sorted(set(previous) - set(issues))
    if recovered:
        lines.append('\nLøst siden sidste kontrol: ' + ', '.join(recovered))
    lines.append('\nKontrollen foretager ingen genstart eller oprydning. '
                 'Den kører på serveren og kan derfor ikke selv sende mail ved totalt server- eller netværksnedbrud.')
    return '\n'.join(lines)


def main():
    import fcntl
    parser = argparse.ArgumentParser()
    parser.add_argument('--dry-run', action='store_true')
    parser.add_argument('--test-mail', action='store_true')
    args = parser.parse_args()
    os.umask(0o077)
    with (BASE / 'monitor.lock').open('a') as lock:
        try:
            fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
        except BlockingIOError:
            return 0
        if args.test_mail:
            send_mail('[Knittynetwork] Test af serveralarm',
                      'Dette er en testmail fra din serverovervågning til tim@gundmann.dk.\n'
                      'Plan: hver time. Kontroller: services, nye logfejl, disk og inoder (90%).\n'
                      'Mailserverens accept bekræfter afsendelse; kontroller også indbakke/spam.\n')
            print('TEST_MAIL_SMTP_ACCEPTED', flush=True)
            return 0
        config = json.loads((BASE / 'config.json').read_text())
        config['_dry_run'] = args.dry_run
        path = BASE / 'state.json'
        try:
            state = json.loads(path.read_text()) if path.exists() else {}
        except Exception:
            # Never silently reset a damaged cursor and lose an unknown log interval.
            raise RuntimeError('Monitor state unreadable; manual recovery required')
        # Leave a short settling interval so an in-flight multiline trace can finish.
        now = int(time.time()) - 5
        issues, cursors, restarts = collect(config, state, now)
        previous = state.get('notified', {})
        notify = notification_needed(previous, issues)
        print(json.dumps({'time': now, 'issues': issues, 'notification_needed': notify}), flush=True)
        if args.dry_run:
            return 0
        save(BASE / 'status.json', {'last_check': now, 'issues': issues, 'mail_ok': None})
        if notify:
            try:
                send_mail('[Knittynetwork] ' + ('Alarm: problemer fundet' if issues else 'Drift genoprettet'),
                          report(issues, previous))
            except Exception:
                save(BASE / 'status.json', {'last_check': now, 'issues': issues, 'mail_ok': False})
                # Do not advance cursors or notification state before SMTP accepts the alert.
                raise RuntimeError('SMTP alert failed; pending incident will be retried')
        save(path, {'last_check': now, 'cursors': cursors, 'restarts': restarts,
                    'notified': issues if notify else previous})
        if config.get('codex_incidents'):
            try:
                deliver_reports(BASE, send_mail)
            except Exception:
                save(BASE / 'status.json', {'last_check': now, 'issues': issues, 'mail_ok': False})
                raise RuntimeError('Codex report mail failed; queued result will be retried')
        save(BASE / 'status.json', {'last_check': now, 'issues': issues, 'mail_ok': True})
        return 0


if __name__ == '__main__':
    try:
        sys.exit(main())
    except Exception as exc:
        # Exception messages from SMTP/Docker can contain secrets; only expose the type.
        print('MONITOR_FAILED: ' + type(exc).__name__, file=sys.stderr, flush=True)
        sys.exit(1)
