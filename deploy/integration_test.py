"""Exercise actual Docker/Nginx cutovers on an isolated test network."""
import importlib.util
import json
from pathlib import Path
import subprocess
import tempfile
import threading
import time
import urllib.request

module_path = Path(__file__).with_name('blue_green.py')
spec = importlib.util.spec_from_file_location('blue_green', module_path)
bg = importlib.util.module_from_spec(spec)
spec.loader.exec_module(bg)
SERVICE = 'knitty-bg-demo'
NETWORK = 'knitty-bg-test-network'
ROUTER = 'knitty-bg-test-router'
bg.NETWORK = NETWORK
bg.SERVICES[SERVICE] = (18000, 19800, '/health', '128m', ROUTER)

real_wait = bg.wait_healthy
bg.wait_healthy = lambda url, expected_backend=None, timeout=300: real_wait(url, expected_backend, min(timeout, 15))
real_backend_wait = bg.Deployment.wait_backend_healthy
bg.Deployment.wait_backend_healthy = lambda self, container, timeout=300: real_backend_wait(self, container, min(timeout,15))
failures = []
requests = []
stop = threading.Event()


def response(path='/health'):
    with urllib.request.urlopen('http://127.0.0.1:19800' + path, timeout=15) as result:
        return json.load(result)


def poll():
    while not stop.is_set():
        try:
            requests.append(response()['version'])
        except Exception as error:
            failures.append(type(error).__name__)
        time.sleep(0.05)


def build(directory, revision, healthy):
    (directory / 'Dockerfile').write_text(f'''FROM python:3.12-alpine
COPY app.py /app.py
ENV VERSION={revision} HEALTHY={int(healthy)}
CMD ["python", "/app.py"]
''')
    result = subprocess.run(['docker', 'build', '-q', '-t', SERVICE + ':' + revision, str(directory)], capture_output=True, text=True)
    if result.returncode:
        raise RuntimeError('Isolated demo image build failed: ' + result.stderr)


def main():
    bg.command(['docker', 'network', 'create', NETWORK])
    thread = None
    try:
        with tempfile.TemporaryDirectory(prefix='knitty-bg-demo-', dir=Path.home() / 'knitty-blue-green-tests') as temporary:
            directory = Path(temporary)
            (directory / 'app.py').write_text('''from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
import json, os, time, signal, sys
signal.signal(signal.SIGTERM, lambda *args: sys.exit(0))
class Handler(BaseHTTPRequestHandler):
    def do_GET(self):
        if self.path == '/slow': time.sleep(10)
        healthy = os.environ.get('HEALTHY') == '1'
        self.send_response(200 if healthy else 503)
        self.send_header('Content-Type', 'application/json')
        self.end_headers()
        self.wfile.write(json.dumps({'status':'UP' if healthy else 'DOWN','version':os.environ['VERSION']}).encode())
    def log_message(self, *args): pass
ThreadingHTTPServer(('0.0.0.0',18000), Handler).serve_forever()
''')
            for revision, healthy in [('1' * 40, True), ('2' * 40, True), ('3' * 40, False), ('4' * 40, True)]:
                build(directory, revision, healthy)
            bg.command(['docker', 'run', '-d', '--name', SERVICE, '--network', NETWORK, SERVICE + ':' + '1' * 40])
            root = directory / 'state'
            root.mkdir()
            deployment = bg.Deployment(SERVICE, root)
            deployment.bootstrap()
            (root / 'host-routes-ready').touch()
            thread = threading.Thread(target=poll)
            thread.start()
            slow_result = []
            slow = threading.Thread(target=lambda: slow_result.append(response('/slow')['version']))
            slow.start()
            time.sleep(0.2)
            deployment.deploy(SERVICE + ':' + '2' * 40)
            slow.join()
            assert slow_result == ['1' * 40], 'In-flight request did not finish on the old release'
            assert response()['version'] == '2' * 40
            before = deployment.state()
            try:
                deployment.deploy(SERVICE + ':' + '3' * 40)
                raise AssertionError('Unhealthy release was accepted')
            except RuntimeError:
                pass
            assert deployment.state() == before, 'Failed release changed state'
            failed = bg.command(['docker', 'ps', '-a', '--filter', 'name=^/' + SERVICE + '-green-' + '3' * 12 + '$', '--format', '{{.Names}}'])
            assert not failed, 'Failed candidate was not removed'
            assert response()['version'] == '2' * 40
            deployment.deploy(SERVICE + ':' + '4' * 40)
            assert response()['version'] == '4' * 40
            deployment.rollback()
            assert response()['version'] == '2' * 40
            stop.set()
            thread.join()
            assert not failures, 'Requests failed during cutover: ' + str(failures)
            print(json.dumps({'requests':len(requests), 'failedRequests':len(failures),
                              'inFlightRequestPreserved':True, 'unhealthyCandidateRejected':True,
                              'secondColorDeployPassed':True, 'rollbackPassed':True}))
    finally:
        stop.set()
        if thread:
            thread.join(timeout=20)
        names = bg.command(['docker', 'ps', '-a', '--filter', 'label=org.knitty.bluegreen.service=' + SERVICE, '--format', '{{.Names}}']).splitlines()
        for name in names + [SERVICE, ROUTER]:
            if name == ROUTER or bg.valid_container(SERVICE, name):
                subprocess.run(['docker', 'rm', '-f', name], capture_output=True)
        subprocess.run(['docker', 'network', 'rm', NETWORK], capture_output=True)


if __name__ == '__main__':
    main()
