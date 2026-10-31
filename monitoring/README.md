# Docker error evidence and Codex investigations

The pre-existing hourly `monitor.py` checks HTTPS, nginx, disk/inodes, containers,
restarts and new Docker errors on gundmann.dk. Its cron and SMTP channel remain
the collection/reporting mechanism. Set `codex_incidents: true` in the existing
private `config.json` to also enqueue evidence. `--dry-run` never enqueues or
sends reports. No third-party Python dependency is required.

`incidents.py` keeps private atomic JSON cases under the monitor's `incidents/`
directory, locked together with the existing monitor. Each case includes:

- An explicit service-to-GitHub mapping, Docker image ID and full source SHA
  from the immutable image tag. A mutable tag produces `blocked-identity`.
- Each error, all available continuation/stacktrace lines, and eight surrounding
  lines on either side. No independent stacktrace length limit is applied.
- A structural projection: timestamps, severity, exception and frame symbols,
  caused-by/suppressed relations, known infrastructure peers and fixed technical
  diagnostics. Every free-text message and value is removed on the server.
  Runtime environment values are also removed before symbol extraction.
  This is deliberately lossy: arbitrary exception messages, SQL, headers,
  payloads and URLs cannot safely be exported. An absent trace cannot be rebuilt.
- A fingerprint of repository, running commit, deepest exception, its first
  three frames and fixed diagnostics. Context/timestamps do not affect it.
  A separate family fingerprint excludes the commit for PR deduplication across
  releases. Completed cases are updated, never automatically reopened.
- Original evidence, first/last observation, distinct collection windows and
  result/delivery state. `occurrences` counts distinct windows, not error lines.

The collector includes a 60-second overlap for context, excludes that overlap
from the existing error counters, and leaves five seconds for traces to settle.
It retains the original 100,000-line per-container safety limit.
Capped intervals are explicitly marked incomplete and must never justify a fix;
the original cursor does not advance. Queue failure likewise leaves the cursor
unchanged. SMTP failure does not lose queued evidence. There is no retention
policy or automatic deletion. Monitor private disk usage as the queue grows.

Codex consumes this queue through a local hourly heartbeat following
[INVESTIGATE.md](INVESTIGATE.md). It uses the desktop's existing authentication,
isolated source checkouts and draft PRs. There is no Codex process or GitHub
credential on the production host. Investigations wait if the desktop is offline.

Commands on the host:

```sh
python3 /home/tim/.local/share/knitty-monitor/incidents.py list
python3 /home/tim/.local/share/knitty-monitor/incidents.py show CASE_ID
python3 /home/tim/.local/share/knitty-monitor/incidents.py backfill \
  --since 2026-10-24T09:00:00Z --until 2026-10-24T10:00:00Z CONTAINER
python3 /home/tim/.local/share/knitty-monitor/incidents.py resolve CASE_ID \
  --status configuration --report-file /private/sanitized-report.txt
```

`resolve` queues the report for the next hourly email run. Unchanged reports
must not be resolved again. Case commands lock against collection. Do not print
raw Docker logs or container environments to debug missing evidence.

Validation: `python3 -m unittest discover -s monitoring` (standard library only).
The original four monitor tests are retained; incident tests exercise privacy,
long traces/context, identity, idempotency, result delivery and failures.

Changing the collector is a manual maintenance operation with a timestamped
backup and atomic file replacement. No application is deployed or restarted.
The extension never merges PRs, invokes deployment, restarts or cleans up.
