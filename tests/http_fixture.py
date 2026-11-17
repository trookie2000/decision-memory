import json
import threading
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer


def completion(content):
    if not isinstance(content, str):
        content = json.dumps(content, ensure_ascii=False)
    return {'choices': [{'message': {'content': content}, 'finish_reason': 'stop'}],
            'usage': {'prompt_tokens': 100, 'completion_tokens': 40}}


@contextmanager
def model_server(responses):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            status, payload = responses.pop(0) if responses else (500, {'error': 'no fixture response'})
            self.send_response(status)
            if payload == 'interrupted-body':
                self.send_header('Transfer-Encoding', 'chunked')
                self.end_headers()
                self.wfile.write(b'10\r\n{}\r\n')
                self.wfile.flush()
                self.close_connection = True
                return
            if isinstance(payload, tuple) and payload[0] == 'short-content-length':
                body = json.dumps(payload[1]).encode()
                self.send_header('Content-Type', 'application/json')
                self.send_header('Content-Length', str(len(body) + 100))
                self.end_headers()
                self.wfile.write(body)
                self.wfile.flush()
                self.close_connection = True
                return
            self.send_header('Content-Type', 'application/json')
            self.send_header('Retry-After', '0')
            self.end_headers()
            self.wfile.write(json.dumps(payload).encode())

        def log_message(self, *args):
            pass

    server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
    thread = threading.Thread(target=server.serve_forever, daemon=True)
    thread.start()
    try:
        yield f'http://127.0.0.1:{server.server_port}/chat/completions', requests
    finally:
        server.shutdown()
        server.server_close()
        thread.join()
