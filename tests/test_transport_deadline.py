"""Deadline cancellation must itself stay bounded, including TLS IO locks."""
import json
import shutil
import ssl
import subprocess
import sys
import tempfile
import threading
import time
import unittest
import urllib.request
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from pathlib import Path

from decision_memory.client import deadline_exchange

ROOT = Path(__file__).resolve().parents[1]


class DeadlineCancellationTests(unittest.TestCase):
    def test_locked_buffer_introspection_cannot_block_timeout_caller(self):
        release = threading.Event()

        class LockedBuffer:
            @property
            def raw(self):
                release.wait(4)
                return None

        class Response:
            fp = LockedBuffer()
            headers = {}

            def read(self, *args):
                release.wait(4)
                return b'{}'

            def __enter__(self):
                return self

            def __exit__(self, *args):
                return False

        class Opener:
            def open(self, *args, **kwargs):
                return Response()

        started = time.monotonic()
        try:
            with self.assertRaises(TimeoutError):
                deadline_exchange(Opener(), urllib.request.Request('http://localhost/'), .1,
                                  stream=False, strict_json=False)
            self.assertLess(time.monotonic() - started, .8)
        finally:
            release.set()

    @unittest.skipUnless(shutil.which('openssl'), 'TLS fixture needs the openssl executable')
    def test_real_https_unfinished_sse_exits_with_unknown_before_outer_process_limit(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'openssl.cnf').write_text('[req]\ndistinguished_name=dn\nx509_extensions=v3\nprompt=no\n'
                '[dn]\nCN=localhost\n[v3]\nsubjectAltName=DNS:localhost,IP:127.0.0.1\n')
            generated = subprocess.run([shutil.which('openssl'), 'req', '-x509', '-newkey', 'rsa:2048', '-nodes',
                '-keyout', str(root / 'key.pem'), '-out', str(root / 'cert.pem'), '-days', '1',
                '-config', str(root / 'openssl.cnf')], capture_output=True, text=True, timeout=15)
            self.assertEqual(generated.returncode, 0, generated.stderr)
            received = []

            class Handler(BaseHTTPRequestHandler):
                def do_POST(self):
                    received.append(self.rfile.read(int(self.headers['Content-Length'])))
                    self.send_response(200); self.send_header('Content-Type', 'text/event-stream'); self.end_headers()
                    try:
                        for _ in range(30):
                            self.wfile.write(b': waiting\n\n'); self.wfile.flush(); time.sleep(.1)
                    except (OSError, ssl.SSLError):
                        pass

                def log_message(self, *args):
                    pass

            server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
            context = ssl.SSLContext(ssl.PROTOCOL_TLS_SERVER)
            context.load_cert_chain(root / 'cert.pem', root / 'key.pem')
            server.socket = context.wrap_socket(server.socket, server_side=True)
            thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
            script = '''import json,ssl,sys,time,urllib.request
from decision_memory.client import Config,ModelClient,ModelError,NoRedirect,DeadlineHTTPHandler,DeadlineHTTPSHandler
c=ModelClient(Config(endpoint=sys.argv[1],model='fixture',api_key_env='',retries=0,timeout=1,stream=True))
c.opener=urllib.request.build_opener(NoRedirect,DeadlineHTTPHandler,DeadlineHTTPSHandler(context=ssl.create_default_context(cafile=sys.argv[2])))
start=time.monotonic()
try:
 c.complete('fixture','fixture',strict_json=True)
except ModelError:
 print(json.dumps({'elapsed':time.monotonic()-start,'transport':c.calls[-1]['transport_status']}))
else:
 raise RuntimeError('unfinished SSE accepted')
'''
            try:
                child = subprocess.run([sys.executable, '-B', '-c', script,
                    f'https://127.0.0.1:{server.server_port}/chat', str(root / 'cert.pem')],
                    cwd=ROOT, capture_output=True, text=True, timeout=5)
            finally:
                server.shutdown(); server.server_close(); thread.join()
            self.assertEqual(child.returncode, 0, child.stderr)
            result = json.loads(child.stdout)
            self.assertLess(result['elapsed'], 2)
            self.assertEqual(result['transport'], 'unknown')
            self.assertEqual(len(received), 1)


if __name__ == '__main__':
    unittest.main()
