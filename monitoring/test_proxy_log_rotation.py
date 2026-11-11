import json
import subprocess
import unittest
from unittest.mock import patch

import enable_proxy_log_rotation as rotation


class ProxyRotationTests(unittest.TestCase):
    def original(self):
        return {
            'State': {'Running': True}, 'Image': 'sha256:pinned',
            'Config': {'Image': 'nginx@sha256:pinned', 'User': '101:101',
                       'Entrypoint': ['/usr/sbin/nginx'], 'Cmd': ['-g', 'daemon off;'],
                       'Env': ['KEEP=value'], 'Labels': {'keep': 'value'}},
            'HostConfig': {'LogConfig': {'Type': 'json-file', 'Config': {}},
                           'RestartPolicy': {'Name': 'unless-stopped'}, 'Memory': 134217728,
                           'ReadonlyRootfs': True, 'Binds': ['/home/tim/router:/etc/nginx:ro'],
                           'Tmpfs': {'/var/run': 'uid=101,gid=101,mode=0700'}, 'CapDrop': ['ALL'],
                           'PortBindings': {'5678/tcp': [{'HostIp': '127.0.0.1', 'HostPort': '15678'}]}},
            'NetworkSettings': {'Networks': {'supabase_default': {'IPAddress': '172.18.0.20', 'Aliases': ['config-server']}}},
        }

    def test_recreation_preserves_address_configuration_and_rotation(self):
        original = self.original()
        updated = self.original()
        updated['HostConfig']['LogConfig']['Config'] = {'max-size': '10m', 'max-file': '3'}
        calls = []

        def run(arguments):
            calls.append(arguments)
            if arguments[:2] == ['docker', 'inspect']:
                return json.dumps([updated if any(call[:2] == ['docker', 'start'] for call in calls) else original])
            if arguments[:2] == ['docker', 'exec']:
                return '{"status":"UP"}'
            return ''

        with patch.object(rotation, 'run', side_effect=run):
            rotation.recreate('knitty-config-router', 5678, '/bff/prod')
        create = next(call for call in calls if call[:2] == ['docker', 'create'])
        for value in ['172.18.0.20', 'config-server', '127.0.0.1:15678:5678/tcp',
                      '/home/tim/router:/etc/nginx:ro', 'KEEP=value', 'max-size=10m', 'max-file=3']:
            self.assertIn(value, create)
        self.assertIn(['docker', 'rename', 'knitty-config-router', 'knitty-config-router-before-log-rotation'], calls)
        self.assertFalse(any(call[:2] == ['docker', 'rm'] for call in calls))

    def test_create_failure_restores_original_name_address_and_process(self):
        calls = []

        def run(arguments):
            calls.append(arguments)
            if arguments[:2] == ['docker', 'inspect']:
                return json.dumps([self.original()])
            if arguments[:2] == ['docker', 'create']:
                raise subprocess.CalledProcessError(1, arguments)
            return ''

        with patch.object(rotation, 'run', side_effect=run), patch.object(rotation.subprocess, 'run'):
            with self.assertRaises(subprocess.CalledProcessError):
                rotation.recreate('knitty-config-router', 5678, '/bff/prod')
        self.assertIn(['docker', 'rename', 'knitty-config-router-before-log-rotation', 'knitty-config-router'], calls)
        self.assertIn(['docker', 'network', 'connect', '--ip', '172.18.0.20', '--alias', 'config-server',
                       'supabase_default', 'knitty-config-router'], calls)
        self.assertEqual(calls[-1], ['docker', 'start', 'knitty-config-router'])


if __name__ == '__main__':
    unittest.main()
