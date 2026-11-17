"""The judge entry executes extraction; it never reads reference annotations."""
import io
import json
import os
import tempfile
import unittest
import zipfile
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from decision_memory.cli import main
from decision_memory.client import Config
from decision_memory.judge_run import execute_run, load_run_samples
from decision_memory.storage import read_json
from http_fixture import completion, model_server
from test_grounded import context, proposed, reviewed


def dataset(path, count=2):
    with zipfile.ZipFile(path, 'w') as archive:
        for index in range(count):
            name = f'R{index + 1}'
            archive.writestr(name + '/上下文/技术设计确认记录.md',
                '**用户：**\n详情缓存仅处理展示数据，订单不改。\n**AGENT：**\n旧接口每批上限50条。\n'
                '**用户：**\n保持兼容；为何50条暂未确认。')
            archive.writestr(name + '/产物/技术方案.md',
                '# 方案\n详情缓存仅处理展示数据，订单不改。\n旧接口每批上限50条。')
        archive.writestr('评测标注/DO_NOT_READ.md', 'EVAL_LABEL_SENTINEL')


class JudgeRunTests(unittest.TestCase):
    def config(self, endpoint, **kwargs):
        return Config(endpoint=endpoint, model='fixture', api_key_env='', retries=2, **kwargs)

    def test_cli_zip_generates_json_markdown_html_receipts_without_labels_or_extra_calls(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); dataset(root / 'input.zip')
            with model_server([(200, completion(p)) for p in (proposed(), reviewed()) * 2]) as (url, requests):
                (root / 'model.toml').write_text('[model]\nendpoint=' + json.dumps(url) +
                    '\nmodel="fixture"\napi_key_env=""\nretries=2\n', encoding='utf-8')
                out = io.StringIO()
                with redirect_stdout(out):
                    code = main(['run', '--dataset', str(root / 'input.zip'), '--scope', 'stage',
                                 '--config', str(root / 'model.toml'), '--output', str(root / 'result')])
            self.assertEqual(code, 0, out.getvalue())
            self.assertEqual(len(requests), 4)
            self.assertNotIn('EVAL_LABEL_SENTINEL', json.dumps(requests))
            report = read_json(root / 'result/report.json')
            self.assertEqual((report['status'], report['succeeded'], report['failed']), ('completed', 2, 0))
            self.assertEqual(report['metrics']['http_attempts'], 4)
            self.assertEqual(report['metrics']['prompt_tokens'], 400)
            self.assertIsNone(report['metrics']['estimated_cost'])
            self.assertIsNone(report['quality']['official_score'])
            self.assertEqual(report['quality']['human_review'], 'pending')
            for name in ('report.md', 'report.html', 'samples/001/result.json', 'samples/001/result.md',
                         'samples/001/result.html', 'samples/001/calls/001.request.json',
                         'samples/001/calls/002.response.json'):
                self.assertTrue((root / 'result' / name).exists(), name)
            self.assertNotIn('EVAL_LABEL_SENTINEL', (root / 'result/report.html').read_text())
            prepared = read_json(root / 'result/samples/001/calls/001.request.json')
            self.assertEqual(prepared['body'], requests[0])

    def test_preflight_rejects_existing_directory_and_invalid_all_inputs_before_http(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); (root / 'out').mkdir()
            with model_server([]) as (url, requests):
                with self.assertRaises(ValueError):
                    execute_run([context()], self.config(url), root / 'out', scope='stage')
                with self.assertRaises(ValueError):
                    execute_run([], self.config(url), root / 'fresh', scope='stage')
            self.assertFalse((root / 'fresh').exists())
            self.assertEqual(requests, [])

    def test_custom_mytoken_headers_reach_wire_and_never_enter_receipts(self):
        headers = []
        secret = 'fixture-header-only-123456'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with patch.dict(os.environ, {'FIXTURE_TOKEN': secret, 'FIXTURE_USERNAME': 'fixture-user'}):
                with model_server([(200, completion(p)) for p in (proposed(), reviewed())], received_headers=headers) as (url, requests):
                    report = execute_run([context()], Config(endpoint=url, model='fixture', api_key_env='',
                        headers_env={'x-token': 'FIXTURE_TOKEN', 'x-username': 'FIXTURE_USERNAME'}), root / 'out', scope='stage')
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(headers[0]['X-Token'], secret)
            self.assertEqual(headers[1]['X-Username'], 'fixture-user')
            for path in (root / 'out').rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret, path.read_text(encoding='utf-8'))

    def test_known_sample_failure_preserves_other_success_and_draft_is_not_success(self):
        responses = [(200, completion('{')), (200, completion(proposed())), (200, completion(reviewed()))]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); dataset(root / 'input.zip')
            samples = load_run_samples(dataset=root / 'input.zip', scope='stage')
            with model_server(responses) as (url, requests):
                report = execute_run(samples, self.config(url), root / 'out', scope='stage')
            self.assertEqual(report['status'], 'partial')
            self.assertEqual([r['status'] for r in report['records']], ['failed', 'success'])
            self.assertEqual(report['metrics']['http_attempts'], 3)
            self.assertTrue((root / 'out/report.html').exists())

    def test_unknown_transport_stops_queue_and_uses_no_http_retry(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); dataset(root / 'input.zip')
            samples = load_run_samples(dataset=root / 'input.zip', scope='stage')
            with model_server([(200, 'interrupted-body'), (200, completion(proposed()))]) as (url, requests):
                report = execute_run(samples, self.config(url), root / 'out', scope='stage')
            self.assertEqual(len(requests), 1)
            self.assertEqual(report['status'], 'unknown')
            self.assertEqual([r['status'] for r in report['records']], ['unknown', 'not_started'])
            self.assertIsNone(report['metrics']['prompt_tokens'])
            receipt = read_json(root / 'out/samples/001/calls/001.receipt.json')
            self.assertEqual(receipt['provider_execution'], 'unknown')

    def test_auth_and_billing_stop_queue_and_redact_all_reports(self):
        secret = 'fixture-mytoken-private-987654'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary); dataset(root / 'input.zip')
            samples = load_run_samples(dataset=root / 'input.zip', scope='stage')
            for status in (401, 402):
                with patch.dict(os.environ, {'FIXTURE_MYTOKEN': secret}):
                    with model_server([(status, {'error': {'code': 'Denied', 'message': secret}})]) as (url, requests):
                        report = execute_run(samples, Config(endpoint=url, model='fixture', api_key_env='',
                            headers_env={'x-token': 'FIXTURE_MYTOKEN'}), root / str(status), scope='stage')
                self.assertEqual(len(requests), 1)
                self.assertEqual(report['records'][1]['status'], 'not_started')
                for path in (root / str(status)).rglob('*'):
                    if path.is_file():
                        self.assertNotIn(secret, path.read_text(encoding='utf-8'))

    def test_budget_bounds_actual_attempts_including_protocol_repairs(self):
        bad = proposed(); bad['items'][0]['conclusion']['evidence'] = ['BAD']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(bad)), (200, completion(proposed()))]) as (url, requests):
                report = execute_run([context()], self.config(url), root / 'out', scope='stage', max_http_attempts=1)
            self.assertEqual(len(requests), 1)
            self.assertEqual(report['status'], 'budget_exhausted')
            self.assertFalse((root / 'out/samples/001/result.json').exists())

    def test_html_escapes_source_and_model_content_and_usage_missing_stays_null(self):
        from decision_memory.sources import index_materials
        sample = index_materials('**用户：**\n<script>alert(1)</script>',
                                 '<img src=x onerror=alert(2)>', 'design', 'X', 'd.md', 'a.md')
        value = {'items': [{'conclusion': {'text': '<script>示例</script>', 'evidence': ['D00002']},
                           'reason': {'text': '未说明', 'evidence': []},
                           'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}
        first, second = completion(value), completion(reviewed(value))
        first.pop('usage'); second.pop('usage')
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, first), (200, second)]) as (url, requests):
                report = execute_run([sample], self.config(url), root / 'out', scope='stage')
            html = (root / 'out/samples/001/result.html').read_text(encoding='utf-8')
            self.assertNotIn('<script>', html)
            self.assertNotIn('<img src=x', html)
            self.assertIn('&lt;script&gt;', html)
            self.assertIsNone(report['metrics']['prompt_tokens'])
            self.assertFalse(report['metrics']['usage_complete'])

    def test_manifest_supports_both_explicit_scopes_and_rejects_scope_mismatch(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            (root / 'd.md').write_text('**用户：**\n明确约束。', encoding='utf-8')
            (root / 'a.md').write_text('明确约束。', encoding='utf-8')
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'requirement_id': 'R',
                'nodes': [{'id': 'N', 'stage': 'design', 'dialogue': 'd.md', 'artifact': 'a.md'}]}), encoding='utf-8')
            stage = load_run_samples(manifest=root / 'manifest.json', scope='stage')
            whole = load_run_samples(manifest=root / 'manifest.json', scope='requirement')
            self.assertEqual(stage[0].stage, 'design')
            self.assertEqual(whole[0].stage, 'requirement')
            with model_server([]) as (url, requests):
                with self.assertRaises(ValueError):
                    execute_run(stage, self.config(url), root / 'out', scope='requirement')
            self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
