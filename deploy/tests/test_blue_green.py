import importlib.util
from pathlib import Path
import tempfile
import unittest
from unittest.mock import patch

module_path = Path(__file__).resolve().parents[1] / 'blue_green.py'
spec = importlib.util.spec_from_file_location('blue_green', module_path)
bg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bg)


class BlueGreenTests(unittest.TestCase):
    def test_unknown_service_is_rejected(self):
        with self.assertRaises(ValueError):
            bg.service_spec('../../nginx')

    def test_image_must_belong_to_selected_service_and_have_immutable_tag(self):
        for image in ['other-service:abc', 'bff-service:latest', 'bff-service:abc;rm -rf /']:
            with self.assertRaises(ValueError):
                bg.validate_image('bff-service', image)
        bg.validate_image('bff-service', 'bff-service:' + 'a' * 40)

    def test_proxy_preserves_streaming_and_forwarded_https(self):
        config = bg.proxy_config('bff-service', 'bff-service-blue', 'bff-service-blue')
        self.assertIn('listen 8877;', config)
        self.assertIn('proxy_read_timeout 5m;', config)
        self.assertIn('proxy_set_header X-Forwarded-Proto $forwarded_proto;', config)
        self.assertIn('proxy_request_buffering off;', config)
        self.assertIn('resolver 127.0.0.11', config)

    def test_backend_cannot_inject_nginx_directives(self):
        with self.assertRaises(ValueError):
            bg.proxy_config('bff-service', 'host; return 200;', 'bff-service-blue')

    def test_state_and_environment_are_private(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'state.json'
            bg.atomic_write(path, '{"active":"blue"}')
            self.assertEqual(path.stat().st_mode & 0o777, 0o600)
            self.assertEqual(path.read_text(), '{"active":"blue"}')

    def test_runtime_environment_keeps_credentials_without_copying_image_defaults(self):
        overrides = bg.environment_overrides(['PATH=/bin', 'TOKEN=secret'], ['PATH=/bin'])
        self.assertEqual(overrides, {'TOKEN': 'secret'})
        with self.assertRaises(ValueError):
            bg.environment_overrides(['TOKEN=secret\nINJECTED=value'], [])

    def test_snap_environment_copy_is_private_and_removed_on_failure(self):
        with tempfile.TemporaryDirectory() as directory, patch.object(bg.Path, 'home', return_value=Path(directory)):
            with self.assertRaises(RuntimeError):
                with bg.docker_environment('TOKEN=secret\n') as path:
                    self.assertEqual(path.read_text(), 'TOKEN=secret\n')
                    self.assertEqual(path.stat().st_mode & 0o777, 0o600)
                    self.assertEqual(path.parent.stat().st_mode & 0o777, 0o700)
                    raise RuntimeError('Docker startup rejected')
            self.assertFalse(path.exists())

    def test_failed_switch_restores_previous_configuration(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            deployment.router_dir.mkdir(parents=True)
            deployment.config_path.write_text('previous configuration')
            previous = {'container': 'bff-service', 'backend': '172.18.0.2'}
            candidate = {'container': 'bff-service-blue', 'backend': 'bff-service-blue'}
            with patch.object(bg, 'command', return_value=''), patch.object(bg, 'wait_healthy', side_effect=[RuntimeError('bad candidate route'), None]):
                with self.assertRaises(RuntimeError):
                    deployment.switch(previous, candidate)
            self.assertEqual(deployment.config_path.read_text(), 'previous configuration')

    def test_failed_nginx_validation_restores_config_before_reload(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            deployment.router_dir.mkdir(parents=True)
            deployment.config_path.write_text('original')
            with patch.object(bg, 'command', side_effect=[RuntimeError('invalid config'), '', '']), patch.object(bg, 'wait_healthy', return_value=None):
                with self.assertRaises(RuntimeError):
                    deployment.switch({'container':'bff-service', 'backend':'172.18.0.2'}, {'container':'bff-service-blue', 'backend':'bff-service-blue'})
            self.assertEqual(deployment.config_path.read_text(), 'original')

    def test_rollback_preserves_commit_and_color_for_next_release(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            previous = {'container': 'bff-service-blue-' + 'a' * 12, 'backend': 'old', 'color': 'blue', 'source': 'a' * 40}
            active = {'container': 'bff-service-green-' + 'b' * 12, 'backend': 'current'}
            with patch.object(deployment, 'state', return_value={'active': active, 'previous': previous}), \
                 patch.object(bg, 'inspect', return_value={'State': {'Running': True}}), \
                 patch.object(bg, 'wait_healthy'), patch.object(bg, 'verify_stable'), \
                 patch.object(deployment, 'wait_backend_healthy'), \
                 patch.object(deployment, 'backend', return_value={'container': previous['container'], 'backend': 'fresh'}), \
                 patch.object(deployment, 'switch'), patch.object(deployment, 'retire'), \
                 patch.object(deployment, 'save') as save:
                deployment.rollback()
            self.assertEqual(save.call_args.args[0]['active']['source'], 'a' * 40)
            self.assertEqual(save.call_args.args[0]['active']['color'], 'blue')

    def test_pattern_background_work_prevents_retirement(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('pattern-service', Path(directory))
            with patch.object(bg, 'inspect', return_value={'State': {'Running': True}}), \
                 patch.object(bg, 'command', side_effect=['nginx: worker process', '1']):
                self.assertFalse(deployment.drained('pattern-service'))

    def test_early_config_import_enables_the_config_client_resolver(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            deployment.env_path.parent.mkdir(parents=True)
            deployment.env_path.write_text('EXISTING=value\n')
            deployment.candidate_environment()
            environment=dict(line.split('=',1) for line in deployment.env_path.read_text().splitlines())
            self.assertEqual(environment['SPRING_CLOUD_CONFIG_ENABLED'], 'true')
            self.assertEqual(environment['SPRING_CONFIG_IMPORT'], 'configserver:http://knitty-config-router:5678')

    def test_unstable_new_route_is_rejected(self):
        with patch.object(bg, 'probe', return_value=False), patch.object(bg.time, 'sleep'):
            with self.assertRaises(RuntimeError):
                bg.verify_stable('http://test', 'bff-service-blue')

    def test_reused_healthy_ip_cannot_validate_a_restarting_candidate(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            state = {'State': {'Running':True, 'StartedAt':'first'}, 'RestartCount':0}
            with patch.object(bg, 'inspect', return_value=state), \
                 patch.object(bg, 'probe', return_value=True), \
                 patch.object(bg, 'command', side_effect=RuntimeError('Named container is restarting')) as command:
                self.assertFalse(deployment.backend_probe('bff-service-blue'))
            self.assertEqual(command.call_args.args[0][:3], ['docker','exec','bff-service-blue'])

    def test_restart_during_a_successful_health_response_is_rejected(self):
        with tempfile.TemporaryDirectory() as directory:
            deployment = bg.Deployment('bff-service', Path(directory))
            before = {'State': {'Running':True,'StartedAt':'first'},'RestartCount':0}
            after = {'State': {'Running':True,'StartedAt':'second'},'RestartCount':1}
            with patch.object(bg,'inspect',side_effect=[before,after]), \
                 patch.object(deployment,'backend_document',return_value={'status':'UP'}):
                self.assertFalse(deployment.backend_probe('bff-service-blue'))


if __name__ == '__main__':
    unittest.main()
