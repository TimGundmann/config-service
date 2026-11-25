import copy
import datetime as dt
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch, MagicMock

import incidents
import monitor


class StartupAlertTests(unittest.TestCase):
    def setUp(self):
        self.container = {
            'Name': '/bff-service-blue-release', 'RestartCount': 0,
            'Config': {'Image': 'bff:' + 'a' * 40},
            'State': {'Running': True, 'Health': {'Status': 'healthy'},
                      'StartedAt': '2026-11-25T09:00:00.000000000Z'},
        }
        self.lines = [
            '2026-11-25T09:00:03Z 2026-11-25T09:00:03 INFO o.s.c.c.c.ConfigServerConfigDataLoader : '
            'org.springframework.web.client.ResourceAccessException: I/O error on GET request for '
            'http://localhost:8888/application/default Connection refused\n',
            '2026-11-25T09:00:04Z 2026-11-25T09:00:04 INFO o.s.v.b.OptionalValidatorFactoryBean : '
            'jakarta.validation.NoProviderFoundException: '
            'no Jakarta Bean Validation provider could be found\n',
        ]

    def test_healthy_release_startup_does_not_alert_or_queue(self):
        filtered = incidents.actionable_log_lines(self.container, self.lines, active_release=True)
        self.assertEqual(monitor.log_summary(filtered)['count'], 0)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(incidents.capture(self.container, filtered, Path(folder), 'window'), [])

    def test_companion_property_source_warning_is_filtered(self):
        lines = [self.lines[0].replace('INFO', 'WARN').replace(
            'org.springframework.web.client.ResourceAccessException:', 'Could not locate PropertySource:')]
        self.assertEqual(incidents.actionable_log_lines(self.container, lines, active_release=True), [])

    def test_uncertain_or_failed_release_preserves_warnings(self):
        for change in ({'Running': False}, {'Health': {'Status': 'unhealthy'}},
                       {'OOMKilled': True}, {'StartedAt': 'invalid'}):
            container = copy.deepcopy(self.container)
            container['State'].update(change)
            with self.subTest(change=change):
                self.assertEqual(incidents.actionable_log_lines(container, self.lines, active_release=True), self.lines)
        container = copy.deepcopy(self.container)
        container['RestartCount'] = 1
        self.assertEqual(incidents.actionable_log_lines(container, self.lines, active_release=True), self.lines)
        self.assertEqual(incidents.actionable_log_lines(self.container, self.lines), self.lines)
        self.assertEqual(incidents.actionable_log_lines(self.container, self.lines, active_release=True, complete=False), self.lines)

    def test_real_errors_and_runtime_warnings_remain(self):
        for line in (
                self.lines[0].replace('INFO', 'ERROR'),
                self.lines[0].replace('localhost:8888', 'knitty-config-router:5678'),
                self.lines[0].replace('09:00:03', '09:05:01'),
                self.lines[0].replace('09:00:03', '08:59:59'),
                self.lines[1].replace('OptionalValidatorFactoryBean', 'OtherLogger'),
                '2026-11-25T09:00:05Z ERROR java.lang.IllegalStateException: failed\n'):
            with self.subTest(line=line):
                self.assertEqual(incidents.actionable_log_lines(self.container, [line], active_release=True), [line])

    def test_full_benign_stack_removed_but_adjacent_failure_retained(self):
        benign = [self.lines[0], '2026-11-25T09:00:03Z Caused by: java.net.ConnectException: Connection refused\n']
        real = '2026-11-25T09:00:05Z 2026-11-25T09:00:05 ERROR java.lang.IllegalStateException: failed\n'
        filtered = incidents.actionable_log_lines(self.container, benign + [real], active_release=True)
        self.assertEqual(filtered, [real])
        self.assertEqual(monitor.log_summary(filtered)['count'], 1)
        with tempfile.TemporaryDirectory() as folder:
            self.assertEqual(len(incidents.capture(self.container, filtered, Path(folder), 'window')), 1)

    def test_unknown_nested_cause_is_not_hidden(self):
        lines = [self.lines[0], '2026-11-25T09:00:03Z Caused by: java.lang.IllegalStateException: failed\n']
        self.assertEqual(incidents.actionable_log_lines(self.container, lines, active_release=True), lines)

    def test_collector_filters_both_channels_even_when_polling_later(self):
        container = dict(self.container, Id='container-id')
        name = container['Name'].lstrip('/')
        now = dt.datetime.fromisoformat('2026-11-25T09:50:00+00:00').timestamp()
        config = {'containers': [], 'releases': ['bff-service'], 'codex_incidents': True}

        def run(command):
            if command[0] == 'systemctl':
                return 'active'
            if command[:2] == ['docker', 'ps']:
                return 'container-id'
            if command[:2] == ['docker', 'inspect']:
                return json.dumps([container])
            return 'header\n'

        def docker_logs(command, stdout, **kwargs):
            stdout.writelines(self.lines)
            return MagicMock(returncode=0)

        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            release = base / '.local/share/knitty-blue-green/bff-service/state.json'
            release.parent.mkdir(parents=True)
            release.write_text(json.dumps({'active': {'container': name}}))
            response = MagicMock()
            response.__enter__.return_value.status = 200
            with patch.object(Path, 'home', return_value=base), patch.object(monitor, 'BASE', base), \
                    patch.object(monitor, 'run', side_effect=run), \
                    patch.object(monitor.urllib.request, 'urlopen', return_value=response), \
                    patch.object(monitor.subprocess, 'run', side_effect=docker_logs):
                issues, cursors, _ = monitor.collect(config, {}, now)
            self.assertEqual(issues, {})
            self.assertEqual(cursors['container-id'], now)
            self.assertFalse((base / 'incidents').exists())
