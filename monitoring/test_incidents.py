import json
from pathlib import Path
import tempfile
import unittest

import incidents


class IncidentTests(unittest.TestCase):
    def test_projection_removes_free_text_but_preserves_stack_and_diagnostics(self):
        source = [
            '2026-10-24T09:12:00.000Z ERROR Alice Nielsen alice@example.com password=secret',
            '2026-10-24T09:12:00.001Z java.net.ConnectException: Connection refused https://user:pass@host?token=abc',
            '2026-10-24T09:12:00.002Z\tat com.knittynetwork.pattern.PatternService.load(PatternService.java:42)',
            '2026-10-24T09:12:00.003Z Caused by: java.io.IOException: address=My Street 12 phone=12345678',
            '2026-10-24T09:12:00.004Z\t... 12 common frames omitted',
            '2026-10-24T09:12:00.005Z INFO SSN=241090-1234 bearer abc privatekey',
        ]
        result = incidents.project(source, ['secret', 'privatekey'])
        text = json.dumps(result)
        for value in ['Alice', 'Nielsen', 'alice@', 'secret', 'pass@', 'My Street', '12345678', '241090', 'privatekey']:
            self.assertNotIn(value, text)
        self.assertIn('java.net.ConnectException', text)
        self.assertIn('Connection refused', text)
        self.assertIn('PatternService.java:42', text)
        self.assertIn('12 common frames omitted', text)
        self.assertEqual(len(result), len(source))

    def test_entire_long_trace_and_context_are_captured(self):
        lines = ['2026-10-24T09:00:00Z INFO context'] * 10
        lines += ['2026-10-24T09:00:01Z ERROR failed', '2026-10-24T09:00:01Z java.lang.IllegalStateException: detail']
        lines += ['2026-10-24T09:00:01Z\tat com.knittynetwork.Test.run(Test.java:1)'] * 300
        lines += ['2026-10-24T09:00:02Z INFO after'] * 10
        events = [lines[start:stop] for start, _, _, stop in incidents.event_ranges(lines)]
        self.assertEqual(len(events), 1)
        self.assertEqual(len(events[0]), 318)

    def test_repeated_windows_increment_without_reopening_and_new_commit_is_new(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            evidence = incidents.project(['2026-10-24T09:00:00Z java.net.ConnectException: Connection refused'])
            meta = {'repository': 'TimGundmann/bff', 'commit': 'a' * 40, 'service': 'bff-service'}
            first = incidents.enqueue(base, meta, evidence, 'window-one')
            path = base / (first + '.json')
            saved = json.loads(path.read_text())
            saved['status'] = 'operations'
            incidents.atomic_save(path, saved)
            incidents.enqueue(base, meta, evidence, 'window-one')
            self.assertEqual(json.loads(path.read_text())['occurrences'], 1)
            incidents.enqueue(base, meta, evidence, 'window-two')
            saved = json.loads(path.read_text())
            self.assertEqual(saved['occurrences'], 2)
            self.assertEqual(saved['status'], 'operations')
            meta['commit'] = 'b' * 40
            self.assertNotEqual(incidents.enqueue(base, meta, evidence, 'window-two'), first)

    def test_identity_never_uses_container_name_as_commit(self):
        container = {'Name': '/bff-service-blue-aaaaaaaaaaaa', 'Config': {'Image': 'bff-service:' + 'a' * 40}, 'Image': 'sha256:abc'}
        self.assertEqual(incidents.identity(container)['commit'], 'a' * 40)
        container['Config']['Image'] = 'bff-service:latest'
        self.assertIsNone(incidents.identity(container)['commit'])
        container['Name'] = '/unknown-service'
        self.assertIsNone(incidents.identity(container))

    def test_context_changes_do_not_change_identity_and_all_stack_formats_survive(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            container = {'Name': '/bff-service-blue-aaaaaaaaaaaa',
                         'Config': {'Image': 'bff-service:' + 'a' * 40, 'Env': ['TOKEN=secret']}}
            error = ['2026-10-24T09:00:00Z ERROR java.lang.IllegalStateException: secret',
                     '2026-10-24T09:00:00Z\tat java.base@24/java.lang.Thread.run(Unknown Source)',
                     '2026-10-24T09:00:00Z\tat java.base/java.lang.Thread.sleep(Native Method)']
            first = incidents.capture(container, ['INFO before'] + error + ['INFO after'], base, 'one')
            second = incidents.capture(container, ['INFO changed'] + error + ['WARN changed'], base, 'two')
            self.assertEqual(first, second)
            value = json.loads(next((base / 'incidents').glob('*.json')).read_text())
            self.assertEqual(len([v for v in value['evidence'] if 'frame' in v]), 2)
            self.assertNotIn('secret', json.dumps(value))

    def test_result_mail_retries_failure_and_suppresses_successful_duplicates(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            metadata = {'service': 'bff-service', 'repository': 'TimGundmann/bff', 'commit': 'a' * 40}
            ident = incidents.enqueue(base / 'incidents', metadata, incidents.project(['ERROR']), 'one')
            path = base / 'incidents' / (ident + '.json')
            value = json.loads(path.read_text())
            value.update(report='No source fix justified.', status='insufficient-evidence')
            incidents.atomic_save(path, value)
            from unittest.mock import Mock
            sender = Mock(side_effect=RuntimeError('mail unavailable'))
            with self.assertRaises(RuntimeError):
                incidents.deliver_reports(base, sender)
            self.assertFalse(json.loads(path.read_text()).get('mail_sent'))
            sender = Mock()
            incidents.deliver_reports(base, sender)
            incidents.deliver_reports(base, sender)
            self.assertEqual(sender.call_count, 1)

    def test_wrapper_variations_share_family_across_commits(self):
        with tempfile.TemporaryDirectory() as folder:
            base = Path(folder)
            metadata = {'service': 'bff-service', 'repository': 'TimGundmann/bff', 'commit': 'a' * 40}
            root = ['Caused by: java.net.ConnectException: Connection refused',
                    '\tat java.net.Socket.connect(Socket.java:42)']
            first = incidents.enqueue(base, metadata, incidents.project(['java.lang.IllegalStateException: wrapper'] + root), 'one')
            metadata['commit'] = 'b' * 40
            second = incidents.enqueue(base, metadata, incidents.project(root), 'two')
            self.assertNotEqual(first, second)
            self.assertEqual(json.loads((base / (first + '.json')).read_text())['family'],
                             json.loads((base / (second + '.json')).read_text())['family'])


if __name__ == '__main__':
    unittest.main()
