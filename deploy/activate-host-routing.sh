#!/usr/bin/env bash
# One-time host routing activation. Future releases need no sudo.
set -Eeuo pipefail
[[ $EUID -eq 0 ]] || { echo 'Run with sudo.' >&2; exit 1; }
task_dir=$(cd -- "$(dirname -- "${BASH_SOURCE[0]}")" && pwd)
state_root=/home/tim/.local/share/knitty-blue-green
marker="$state_root/host-routes-ready"
[[ ! -e "$marker" ]] || { echo 'Routing is already activated.'; exit 0; }
sha256sum --check "$task_dir/host-routing-source.sha256"
nginx -t
for service in config-server bff-service user-service pattern-service newsletter-service; do
  runuser -u tim -- python3 "$task_dir/blue_green.py" status --service "$service" | python3 -c 'import json,sys; assert json.load(sys.stdin)["healthy"]'
done
backup_dir="/var/backups/knitty-blue-green-$(date -u +%Y%m%dT%H%M%SZ)"
install -d -m 0700 "$backup_dir"
cp -a /etc/nginx/sites-available/knittynetwork.com /etc/nginx/sites-available/gundmann.dk "$backup_dir/"
rollback() {
  task_status=$?
  trap - ERR
  rm -f -- "$marker"
  cp -a "$backup_dir/knittynetwork.com" /etc/nginx/sites-available/knittynetwork.com
  cp -a "$backup_dir/gundmann.dk" /etc/nginx/sites-available/gundmann.dk
  nginx -t && systemctl reload nginx
  echo "Activation failed; original routing restored. Backup: $backup_dir" >&2
  exit "$task_status"
}
trap rollback ERR
python3 - <<'PY'
from pathlib import Path
changes = {
    '/etc/nginx/sites-available/knittynetwork.com': {
        'proxy_pass http://localhost:8887;': 'proxy_pass http://127.0.0.1:19887;',
        'proxy_pass http://localhost:8877;': 'proxy_pass http://127.0.0.1:19877;',
    },
    '/etc/nginx/sites-available/gundmann.dk': {
        'proxy_pass https://localhost:8888;': 'proxy_pass http://127.0.0.1:19888;',
    },
}
for filename, replacements in changes.items():
    path = Path(filename)
    text = path.read_text()
    for before, after in replacements.items():
        assert text.count(before) == 1, 'Unexpected Nginx source; refusing activation'
        text = text.replace(before, after)
    path.write_text(text)
PY
nginx -t
systemctl reload nginx
python3 - <<'PY'
import json, time, urllib.request, subprocess
checks = [
    ('https://knittynetwork.com/bff/actuator/health', 'bff-service'),
    ('https://knittynetwork.com/users/actuator/health', 'user-service'),
    ('https://gundmann.dk/newsletter/actuator/health', 'newsletter-service'),
]
for url, identity in checks:
    ready = False
    for attempt in range(30):
        try:
            with urllib.request.urlopen(url, timeout=5) as response:
                ready = response.headers.get('X-Knitty-Backend') == identity and json.load(response).get('status') == 'UP'
        except Exception:
            ready = False
        if ready: break
        time.sleep(1)
    assert ready, 'Public routing did not become healthy: ' + identity
for attempt in range(60):
    workers = subprocess.check_output(['ps', '-eo', 'args'], text=True)
    if 'nginx: worker process is shutting down' not in workers: break
    time.sleep(1)
else:
    raise RuntimeError('Host Nginx still has in-flight requests; activation can be retried after they finish')
PY
# HTTP 410 is expected for the retired domain; verify it without raising.
[[ $(curl -s -o /dev/null -w '%{http_code}' http://knottynetwork.com/) == 410 ]]
install -o tim -g tim -m 0600 /dev/null "$marker"
trap - ERR
echo "Blue/green routing activated. All original applications are still running. Backup: $backup_dir"
