"""Exercise process isolation, restart and PID-1 shutdown using UDP children."""
import json
import os
from pathlib import Path
import signal
import socket
import subprocess
import sys
import tempfile
import time
import unittest

SUPERVISOR = os.environ['AWG_SUPERVISOR']
COMMON = '''AWG_JC=0
AWG_JMIN=0
AWG_JMAX=0
AWG_S1=0
AWG_S2=0
AWG_H1=1
AWG_H2=2
AWG_H3=3
AWG_H4=4
AWG_SERVER_PUB=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
AWG_CLIENT_PUB=AAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAAA=
'''


def port():
    with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
        s.bind(('127.0.0.1', 0))
        return s.getsockname()[1]


def until(fn, timeout=5):
    deadline = time.monotonic() + timeout
    while time.monotonic() < deadline:
        result = fn()
        if result:
            return result
        time.sleep(.02)
    raise AssertionError('condition timed out')


class SupervisorTest(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.root = Path(self.temp.name)
        self.conf = self.root / 'config'
        self.conf.mkdir()
        (self.conf / 'common.env').write_text(COMMON)
        self.fake = self.root / 'fake-proxy'
        self.fake.write_text(f'#!{sys.executable}\n' + '''import json, os, signal, socket
from pathlib import Path
name = os.environ.get('AWG_ROUTE_NAME', 'legacy')
s = socket.socket(socket.AF_INET, socket.SOCK_DGRAM)
host, port = os.environ['AWG_LISTEN'].rsplit(':', 1)
s.bind((host, int(port)))
if os.environ.get('TEST_IGNORE_TERM'):
    signal.signal(signal.SIGTERM, signal.SIG_IGN)
p = Path(os.environ['TEST_OUTPUT']) / (name + '.json')
tmp = p.with_suffix('.tmp')
tmp.write_text(json.dumps({'pid': os.getpid(), 'env': dict(os.environ)}))
tmp.replace(p)
while True:
    data, addr = s.recvfrom(65535)
    if data == b'crash': os._exit(42)
    s.sendto(name.encode() + b':' + data, addr)
''')
        self.fake.chmod(0o700)
        self.env = dict(os.environ, AWG_CONFIG_DIR=str(self.conf),
                        AWG_PROXY_BIN=str(self.fake), TEST_OUTPUT=str(self.root),
                        AWG_LISTEN='bad inherited listen', AWG_H1='999',
                        AWG_FB_H1='888')
        self.proc = None

    def tearDown(self):
        if self.proc:
            if self.proc.poll() is None:
                self.proc.terminate()
            self.proc.communicate(timeout=9)
        self.temp.cleanup()

    def route(self, name, listen=None, extra=''):
        listen = listen or port()
        (self.conf / (name + '.env')).write_text(
            f'AWG_LISTEN=127.0.0.1:{listen}\nAWG_REMOTE=127.0.0.1:40000\n' + extra)
        return listen

    def run_check(self):
        return subprocess.run([SUPERVISOR, '--check'], env=self.env,
                              capture_output=True, timeout=5)

    def start(self):
        self.proc = subprocess.Popen([SUPERVISOR], env=self.env,
                                     stdout=subprocess.PIPE, stderr=subprocess.PIPE)

    def snapshot(self, name):
        p = self.root / (name + '.json')
        return json.loads(p.read_text()) if p.exists() else None

    def udp(self, listen, msg=b'test'):
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.settimeout(1)
            s.sendto(msg, ('127.0.0.1', listen))
            return s.recv(65535)

    def test_config_dir_argument_overrides_environment(self):
        self.route('a')
        env = dict(self.env, AWG_CONFIG_DIR='/nonexistent')
        result = subprocess.run([SUPERVISOR, '--config-dir', str(self.conf), '--check'],
                                env=env, capture_output=True, timeout=5)
        self.assertEqual(result.returncode, 0, result.stderr)

    def test_normal_requires_single_client_key(self):
        self.route('a', extra='AWG_MODE=normal\nAWG_CLIENT_PUB=\nAWG_CLIENT_PUBS=key1,key2\n')
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_isolated_routes_and_restart(self):
        a, b = self.route('a'), self.route('b', extra='AWG_H1=55\n')
        self.start()
        first = until(lambda: self.snapshot('a'))
        second = until(lambda: self.snapshot('b'))
        self.assertEqual(first['env']['AWG_H1'], '1')
        self.assertEqual(second['env']['AWG_H1'], '55')
        self.assertNotIn('AWG_FB_H1', first['env'])
        self.assertNotIn('AWG_CONFIG_DIR', first['env'])
        self.assertEqual(self.udp(a), b'a:test')
        self.assertEqual(self.udp(b), b'b:test')
        with socket.socket(socket.AF_INET, socket.SOCK_DGRAM) as s:
            s.sendto(b'crash', ('127.0.0.1', a))
        self.assertEqual(self.udp(b), b'b:test')
        until(lambda: self.snapshot('a')['pid'] != first['pid'])
        self.assertEqual(self.snapshot('b')['pid'], second['pid'])
        self.assertEqual(self.udp(a), b'a:test')
        children = [self.snapshot(x)['pid'] for x in ('a', 'b')]
        self.proc.terminate()
        self.proc.communicate(timeout=8)
        self.assertEqual(self.proc.returncode, 0)
        for pid in children:
            with self.assertRaises(ProcessLookupError): os.kill(pid, 0)

    def test_force_stop_unresponsive_child(self):
        self.route('stubborn')
        self.env['TEST_IGNORE_TERM'] = '1'
        self.start()
        child = until(lambda: self.snapshot('stubborn'))['pid']
        self.proc.terminate()
        self.proc.communicate(timeout=8)
        self.assertEqual(self.proc.returncode, 0)
        with self.assertRaises(ProcessLookupError): os.kill(child, 0)

    def test_legacy_env_entrypoint(self):
        self.env.pop('AWG_CONFIG_DIR')
        self.env['AWG_LISTEN'] = f'127.0.0.1:{port()}'
        self.start()
        child = until(lambda: self.snapshot('legacy'))
        self.assertEqual(child['pid'], self.proc.pid)  # exec, no extra process

    def test_duplicate_listen_fails_before_start(self):
        a = self.route('a')
        self.route('b', listen=a)
        self.assertNotEqual(self.run_check().returncode, 0)
        self.assertFalse(list(self.root.glob('*.json')))

    def test_duplicate_setting_rejected(self):
        self.route('a', extra='AWG_H1=5\nAWG_H1=6\n')
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_no_routes_rejected(self):
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_missing_required_not_inherited(self):
        self.route('a')
        (self.conf / 'common.env').unlink()
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_values_are_literal(self):
        self.route('a', extra="AWG_I1='$(touch /tmp/never-execute); `whoami` # literal'\n")
        self.start()
        env = until(lambda: self.snapshot('a'))['env']
        self.assertEqual(env['AWG_I1'], '$(touch /tmp/never-execute); `whoami` # literal')

    def test_reserved_setting_rejected(self):
        self.route('a', extra='AWG_PROXY_BIN=/other\n')
        self.assertNotEqual(self.run_check().returncode, 0)

    def test_server_multiple_peers(self):
        self.route('server', extra='AWG_MODE=server\nAWG_CLIENT_PUB=\nAWG_CLIENT_PUBS=key1,key2\n')
        self.assertEqual(self.run_check().returncode, 0)


if __name__ == '__main__':
    unittest.main(verbosity=2)
