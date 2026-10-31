import unittest
from unittest.mock import patch
import json
import sys
import tempfile
import types
from pathlib import Path
import monitor
from monitor import disk_issues, log_summary, notification_needed, new_log_lines


class MonitorTests(unittest.TestCase):
    def test_overlap_is_available_as_context_but_not_counted_as_new_errors(self):
        lines = ['2026-10-24T08:59:59.999999999Z ERROR old\n',
                 '2026-10-24T09:00:00.000000000Z ERROR new\n']
        since = 1792832400
        self.assertEqual(log_summary(new_log_lines(lines, since))['count'], 1)

    def test_disk_threshold_and_inodes(self):
        self.assertEqual(disk_issues('/data', 89.9, 89.9), {})
        self.assertIn('disk:/data', disk_issues('/data', 90, 20))
        self.assertIn('inodes:/data', disk_issues('/data', 20, 90))

    def test_log_detection_does_not_expose_secrets_or_personal_data(self):
        text = 'INFO normal\nERROR user=user@example.com password=secret\nFATAL failure\njava.net.ConnectException: refused\n'
        result = log_summary(text.splitlines())
        self.assertEqual(result['count'], 3)
        self.assertNotIn('secret', str(result))
        self.assertNotIn('example.com', str(result))
        self.assertEqual(log_summary(['INFO healthy'])['count'], 0)

    def test_notify_new_recovered_and_worse_but_not_unchanged(self):
        self.assertFalse(notification_needed({}, {}))
        self.assertTrue(notification_needed({}, {'http': {'count': 1}}))
        self.assertFalse(notification_needed({'http': {'count': 1}}, {'http': {'count': 1}}))
        self.assertTrue(notification_needed({'http': {'count': 1}}, {}))
        self.assertTrue(notification_needed({'logs:a': {'count': 2}}, {'logs:a': {'count': 4}}))
        self.assertTrue(notification_needed({'disk:/': {'count': 1, 'percent': 90}},
                                            {'disk:/': {'count': 1, 'percent': 95}}))

    def test_failed_mail_does_not_advance_log_cursor_or_clear_incident(self):
        with tempfile.TemporaryDirectory(dir=Path(__file__).parent) as folder:
            base = Path(folder)
            (base / 'config.json').write_text('{}')
            original = {'cursors': {'container': 123}, 'notified': {}}
            (base / 'state.json').write_text(json.dumps(original))
            fake_lock = types.SimpleNamespace(LOCK_EX=1, LOCK_NB=2, flock=lambda *a: None)
            with patch.object(monitor, 'BASE', base), patch.object(sys, 'argv', ['monitor']), \
                    patch.dict(sys.modules, {'fcntl': fake_lock}), \
                    patch.object(monitor, 'collect', return_value=({'http': {'count': 1, 'message': 'offline'}}, {'container': 456}, {})), \
                    patch.object(monitor, 'send_mail', side_effect=RuntimeError('SMTP unavailable')):
                with self.assertRaises(RuntimeError):
                    monitor.main()
            self.assertEqual(json.loads((base / 'state.json').read_text()), original)
            self.assertFalse(json.loads((base / 'status.json').read_text())['mail_ok'])


if __name__ == '__main__':
    unittest.main()
