import importlib.util
import json
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch


spec = importlib.util.spec_from_file_location('rotation_blue_green', Path(__file__).resolve().parents[1] / 'blue_green.py')
bg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bg)


class ContainerCreated(Exception):
    pass


class LogRotationTests(unittest.TestCase):
    def assert_rotated_run(self, arguments):
        self.assertIn('--log-driver', arguments)
        self.assertEqual(arguments[arguments.index('--log-driver') + 1], 'json-file')
        options = [arguments[index + 1] for index, value in enumerate(arguments) if value == '--log-opt']
        self.assertIn('max-size=10m', options)
        self.assertIn('max-file=3', options)
        raise ContainerCreated()

    def test_new_routers_have_bounded_logs(self):
        for service in bg.SERVICES:
            with self.subTest(service=service), tempfile.TemporaryDirectory() as directory:
                deployment = bg.Deployment(service, Path(directory))
                original = {'State': {'Running': True}, 'Image': 'sha256:test', 'Config': {'Env': []}, 'Mounts': []}

                def command(arguments):
                    if arguments[:2] == ['docker', 'run']:
                        return self.assert_rotated_run(arguments)
                    return json.dumps([{'Config': {'Env': []}}])

                with patch.object(bg, 'inspect', return_value=original), \
                     patch.object(bg, 'command', side_effect=command), \
                     patch.object(deployment, 'backend', return_value={'backend': service, 'container': service}):
                    with self.assertRaises(ContainerCreated):
                        deployment.bootstrap()

    def test_current_release_without_rotation_is_recreated_with_bounded_logs(self):
        for service in bg.SERVICES:
            with self.subTest(service=service), tempfile.TemporaryDirectory() as directory:
                root = Path(directory)
                (root / 'host-routes-ready').touch()
                deployment = bg.Deployment(service, root)
                deployment.directory.mkdir(parents=True)
                deployment.env_path.write_text('EXISTING=value\n')
                revision = 'a' * 40
                state = {'active': {'container': service + '-blue-' + revision[:12], 'color': 'blue', 'source': revision}, 'mounts': []}
                original_read = Path.read_text

                def read_text(path, *args, **kwargs):
                    if str(path) == '/proc/meminfo':
                        return 'MemAvailable: 8388608 kB\n'
                    return original_read(path, *args, **kwargs)

                def command(arguments):
                    if arguments[:2] == ['docker', 'run']:
                        return self.assert_rotated_run(arguments)
                    return ''

                with patch.object(deployment, 'state', return_value=state), \
                     patch.object(bg, 'inspect', return_value={'HostConfig': {'LogConfig': {'Type': 'json-file', 'Config': {}}}}), \
                     patch.object(bg, 'wait_healthy'), \
                     patch.object(bg.Path, 'read_text', read_text), \
                     patch.object(bg, 'command', side_effect=command), \
                     patch.object(bg.Path, 'home', return_value=root):
                    with self.assertRaises(ContainerCreated):
                        deployment.deploy(service + ':' + revision)

    def test_current_release_with_rotation_is_kept_running(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            (root / 'host-routes-ready').touch()
            deployment = bg.Deployment('config-server', root)
            revision = 'a' * 40
            state = {'active': {'container': 'config-server-blue-' + revision[:12], 'source': revision}}
            logging = {'Type': 'json-file', 'Config': {'max-size': '10m', 'max-file': '3'}}
            with patch.object(deployment, 'state', return_value=state), \
                 patch.object(bg, 'wait_healthy'), \
                 patch.object(bg, 'inspect', return_value={'HostConfig': {'LogConfig': logging}}), \
                 patch.object(bg, 'command') as command:
                deployment.deploy('config-server:' + revision)
            command.assert_not_called()


if __name__ == '__main__':
    unittest.main()
