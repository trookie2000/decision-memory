import json
import threading
import time
import unittest
from contextlib import contextmanager
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer

from decision_memory.client import Config, ModelClient, ModelError, ModelOutputError
from decision_memory.pipeline import extract, run_signature
from decision_memory.sources import make_sample
from test_validation import valid_payload


def sse_event(value):
    return b'data: ' + json.dumps(value, ensure_ascii=False).encode() + b'\r\n\r\n'


def response_bytes(content, finish='stop', done=True):
    text = json.dumps(content, ensure_ascii=False)
    chunks = [text[:7], text[7:19], text[19:]]
    data = b': heartbeat\r\n\r\n' + sse_event({'choices': [
        {'index': 0, 'delta': {'role': 'assistant', 'reasoning_content': 'private reasoning'}}]})
    for chunk in chunks:
        data += sse_event({'choices': [{'index': 0, 'delta': {'content': chunk}, 'finish_reason': None}]})
    if finish:
        data += sse_event({'choices': [{'index': 0, 'delta': {}, 'finish_reason': finish}]})
    data += sse_event({'choices': [], 'usage': {'prompt_tokens': 7, 'completion_tokens': 13}})
    return data + (b'data: [DONE]\r\n\r\n' if done else b'')


@contextmanager
def stream_server(bodies, drip_seconds=0):
    requests = []

    class Handler(BaseHTTPRequestHandler):
        def do_POST(self):
            requests.append(json.loads(self.rfile.read(int(self.headers['Content-Length']))))
            body = bodies.pop(0)
            self.send_response(200)
            self.send_header('Content-Type', 'text/event-stream; charset=utf-8')
            self.end_headers()
            # Split UTF-8 characters and SSE records across real socket writes.
            for offset in range(0, len(body), 5):
                try:
                    self.wfile.write(body[offset:offset + 5])
                    self.wfile.flush()
                except (BrokenPipeError, ConnectionResetError):
                    break
                if drip_seconds:
                    time.sleep(drip_seconds)

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


class StreamingTests(unittest.TestCase):
    def setUp(self):
        self.sample = make_sample('门槛改成 120 元', '本次活动门槛为 120 元', 'analysis')

    def client(self, endpoint):
        config = Config(endpoint=endpoint, model='fixture', api_key_env='', retries=0)
        # An old client still runs this test and fails on observed HTTP behavior.
        config.stream = True
        return ModelClient(config)

    def test_streaming_http_produces_a_validated_prediction_and_trailing_usage(self):
        body = response_bytes(valid_payload())
        with stream_server([body, body]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'baseline')
        self.assertTrue(requests[0]['stream'])
        self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['line'], 1)
        self.assertEqual(result['metrics']['prompt_tokens'], 7)
        self.assertEqual(result['metrics']['completion_tokens'], 13)
        self.assertEqual(result['metrics']['http_attempts'], 1)
        self.assertNotIn('private reasoning', json.dumps(result))

    def test_complete_json_without_finish_state_is_not_a_success(self):
        with stream_server([response_bytes({'items': [], 'open_questions': []}, finish=None, done=False)]) as (endpoint, _):
            client = self.client(endpoint)
            with self.assertRaises(ModelError):
                client.complete('system', 'user')
        self.assertEqual(client.calls[0]['status'], 'failed')

    def test_filtered_or_tool_output_is_not_a_successful_empty_result(self):
        for finish in ('content_filter', 'tool_calls'):
            with self.subTest(finish=finish):
                with stream_server([response_bytes({'items': [], 'open_questions': []}, finish=finish)]) as (endpoint, _):
                    with self.assertRaises(ModelError):
                        self.client(endpoint).complete('system', 'user')

    def test_stream_token_limit_keeps_usage_and_reports_truncation(self):
        with stream_server([response_bytes({'ok': True}, finish='length')]) as (endpoint, _):
            client = self.client(endpoint)
            with self.assertRaisesRegex(ModelOutputError, '截断'):
                client.complete('system', 'user')
        self.assertEqual(client.metrics()['completion_tokens'], 13)

    def test_non_object_stream_event_is_a_controlled_error(self):
        with stream_server([sse_event(['invalid']) + b'data: [DONE]\n\n']) as (endpoint, _):
            with self.assertRaises(ModelOutputError):
                self.client(endpoint).complete('system', 'user')

    def test_run_signature_is_stable_and_stream_configuration_changes_it(self):
        config = Config(endpoint='https://example.test/chat/completions', model='fixture', api_key_env='')
        legacy = run_signature(self.sample, ModelClient(config), 'baseline')
        self.assertEqual(legacy, run_signature(self.sample, ModelClient(config), 'baseline'))
        self.assertEqual(len(legacy), 64)
        config.stream = True
        self.assertNotEqual(run_signature(self.sample, ModelClient(config), 'baseline'), legacy)

    def test_stream_flag_must_be_boolean(self):
        with self.assertRaises(ValueError):
            Config(endpoint='https://example.test/chat/completions', model='fixture', stream='false')

    def test_explicit_reasoning_effort_reaches_the_gateway(self):
        with stream_server([response_bytes({'ok': True})]) as (endpoint, requests):
            client = self.client(endpoint)
            client.config.reasoning_effort = 'low'
            self.assertEqual(client.complete('system', 'user'), {'ok': True})
        self.assertEqual(requests[0].get('reasoning_effort'), 'low')

    def test_reasoning_effort_rejects_unsupported_configuration(self):
        config = Config(endpoint='https://example.test/chat/completions', model='fixture')
        config.reasoning_effort = 'invalid'
        with self.assertRaises(ValueError):
            config.__post_init__()

    def test_dripping_unterminated_sse_line_cannot_evade_total_deadline(self):
        body = b'data: ' + b'x' * 160 + b'\n\n'
        with stream_server([body], drip_seconds=0.075) as (endpoint, _):
            client = self.client(endpoint)
            client.config.timeout = 1
            started = time.monotonic()
            with self.assertRaises(ModelError):
                client.complete('system', 'user')
            elapsed = time.monotonic() - started
        self.assertLess(elapsed, 1.7)

    def test_premature_eof_retries_and_preserves_only_the_complete_response(self):
        partial = sse_event({'choices': [{'delta': {'content': '{"partial":'}}]})
        complete = response_bytes({'ok': True})
        with stream_server([partial, complete]) as (endpoint, requests):
            client = self.client(endpoint)
            client.config.retries = 1
            self.assertEqual(client.complete('system', 'user'), {'ok': True})
        self.assertEqual(len(requests), 2)
        self.assertEqual(client.metrics()['http_attempts'], 2)


if __name__ == '__main__':
    unittest.main()
