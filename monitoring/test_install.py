import base64
import json
from pathlib import Path
import runpy
import tempfile
import unittest
from unittest.mock import patch


class InstallerTests(unittest.TestCase):
    def test_installs_incident_dependencies_with_the_collector(self):
        bundle = {name: '# ' + name for name in (
            'monitor.py', 'test_monitor.py', 'incidents.py', 'test_incidents.py')}
        with tempfile.TemporaryDirectory() as directory:
            home = Path(directory)
            target = home / '.local/share/knitty-monitor'
            target.mkdir(parents=True)
            (target / 'config.json').write_text('{}')
            encoded = base64.b64encode(json.dumps(bundle).encode()).decode()
            with patch('pathlib.Path.home', return_value=home), \
                    patch('sys.argv', ['install.py', encoded]), \
                    patch('subprocess.run') as run:
                runpy.run_path(str(Path(__file__).with_name('install.py')), run_name='__main__')
            for name, content in bundle.items():
                self.assertTrue((target / name).exists(), name + ' was not installed')
                self.assertEqual((target / name).read_text(), content)
            run.assert_called_once()


if __name__ == '__main__':
    unittest.main()
