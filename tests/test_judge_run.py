"""The judge entry executes extraction; it never reads reference annotations."""
import io
import json
import os
import tempfile
import unittest
import zipfile
import threading
import time
from http.server import BaseHTTPRequestHandler, ThreadingHTTPServer
from contextlib import redirect_stdout
from pathlib import Path
from unittest.mock import patch

from decision_memory.cli import main
from decision_memory.client import Config, ModelClient, ModelOutputError
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

    def test_witness_review_sends_candidates_only_to_second_request(self):
        sample = context()
        draft = proposed()
        draft['items'][0]['boundary'] = {'text': '未说明', 'evidence': []}
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            with model_server([(200, completion(draft)), (200, completion(reviewed()))]) as (url, requests):
                report = execute_run([sample], self.config(url, grounded_strategy='witness-review'),
                                     output, max_http_attempts=2)
            self.assertEqual(report['status'], 'completed')
            saved = read_json(output / 'samples/001/result.json')
            from decision_memory.storage import validate_saved
            self.assertEqual(validate_saved(saved, sample), saved)
        self.assertEqual(len(requests), 2)
        self.assertNotIn('field_witnesses', requests[0]['messages'][0]['content'])
        self.assertIn('field_witnesses', requests[1]['messages'][0]['content'])
        hints = json.loads(requests[1]['messages'][1]['content'].rsplit('\nreview_hints ', 1)[1])
        self.assertTrue(any(row['field'] == 'boundary' and 'D00002' in row['candidate_source_ids']
                            for row in hints['field_witnesses']))

    def test_witness_preflight_rejects_unfit_review_without_calling_extract(self):
        from decision_memory.grounded import initial_request

        sample = context()
        minimum = sum(map(len, initial_request(sample, 'witness-review')))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            with model_server([(200, completion(proposed()))]) as (url, requests):
                config = self.config(url, grounded_strategy='witness-review', max_input_chars=minimum)
                with self.assertRaisesRegex(ValueError, '最小复核'):
                    execute_run([sample], config, output)
            self.assertEqual(requests, [])
            self.assertFalse(output.exists())

    def test_complete_reply_with_unescaped_layout_characters_preserves_text(self):
        reply = reviewed()
        note = '复核路径 C:\\tmp，字段 "边界"\t备注\r\n下一行'
        reply['review_notes'] = [note]
        content = json.dumps(reply, ensure_ascii=False)
        content = content.replace('边界\\"\\t备注\\r\\n下一行', '边界\\"\t备注\r\n下一行')
        self.assertIn('\t备注\r\n', content)
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            with model_server([(200, completion(proposed())), (200, completion(content))]) as (url, requests):
                report = execute_run([context()], self.config(url), output, max_http_attempts=2)
            self.assertEqual(report['status'], 'completed')
            sample = output / 'samples/001'
            self.assertEqual(read_json(sample / 'result.json')['review_notes'], [note])
            receipt = read_json(sample / 'calls/002.receipt.json')
            self.assertEqual(receipt['call']['json_normalization']['escaped_characters'], 3)
            self.assertEqual(len(requests), 2)

    def test_layout_normalization_does_not_accept_other_json_errors(self):
        invalid = ('{"text":"a\n\x00b"}', '{"text":"a\nb",}', '{"text":"a\nb","number":NaN}',
                   '{"text":"a\\\nb"}', '{"text":"a\nb"', '```json\n{"text":"a\nb"}\n```')
        with model_server([(200, completion(content)) for content in invalid]) as (url, requests):
            client = ModelClient(self.config(url))
            for content in invalid:
                with self.subTest(content=content), self.assertRaises(ModelOutputError) as error:
                    client.complete('system', 'user', strict_json=True)
                self.assertEqual(error.exception.response_text, content)
                self.assertNotIn('json_normalization', client.calls[-1])
        self.assertEqual(len(requests), len(invalid))

    def test_evidence_first_cli_sends_original_sources_and_no_draft(self):
        topics = [{'topic': '详情缓存范围', 'rule': ['D00002', 'A00002'],
                   'reason': [], 'boundary': ['D00002'], 'history': []}]
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'sources.zip'
            dataset(package, count=1)
            with model_server([(200, completion({'topics': topics})),
                               (200, completion(reviewed()))]) as (url, requests):
                config = root / 'model.toml'
                config.write_text('[model]\nendpoint=' + json.dumps(url) +
                                  '\nmodel="fixture"\napi_key_env=""\ngrounded_strategy="evidence-first"\n')
                output = root / 'output'
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['run', '--dataset', str(package), '--scope', 'stage',
                                           '--config', str(config), '--output', str(output),
                                           '--max-http-attempts', '2']), 0)
                    result = output / 'samples/001/result.json'
                    self.assertEqual(main(['validate', '--dataset', str(package), '--result', str(result)]), 0)
                inventory = read_json(output / 'samples/001/audit/source-inventory.json')
                self.assertEqual(inventory['topics'], topics)
                self.assertFalse(inventory['semantic_certification'])
                self.assertEqual(read_json(result)['requested_config']['grounded_strategy'], 'evidence-first')
        original, synthesis = (json.loads(request['messages'][1]['content']) for request in requests)
        self.assertEqual(synthesis, {**original, 'source_topics': topics})
        self.assertEqual(original['input_scope'], 'stage')
        self.assertNotIn('EVAL_LABEL_SENTINEL', json.dumps(requests))

    def test_evidence_first_rejects_unknown_sources_before_synthesis(self):
        bad = {'topics': [{'topic': '合成主题', 'rule': ['D99999'], 'reason': [],
                           'boundary': [], 'history': []}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(bad))] * 2) as (url, requests):
                report = execute_run([context()], self.config(url, grounded_strategy='evidence-first'), root / 'out')
            self.assertEqual(report['status'], 'failed')
            audit = root / 'out/samples/001/audit'
            self.assertTrue((audit / 'draft.json').exists())
            self.assertFalse((audit / 'source-inventory.json').exists())
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(step['step'] == 'inventory' for step in read_json(audit / 'state.json')['steps']))

    def test_evidence_first_inventory_retry_drops_unrelated_draft_fields(self):
        bad = {'field_checks': [{'item_index': 1, 'field': 'reason', 'status': 'unsupported',
                                 'notes': 'OFF_PROTOCOL_DRAFT'}]}
        good = {'topics': [{'topic': '详情缓存', 'rule': ['D00002'], 'reason': [],
                            'boundary': [], 'history': []}]}
        with tempfile.TemporaryDirectory() as temporary:
            replies = [(200, completion(row)) for row in (bad, good, reviewed())]
            with model_server(replies) as (url, requests):
                report = execute_run([context()], self.config(url, grounded_strategy='evidence-first'),
                                     Path(temporary) / 'out', max_http_attempts=3)
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(requests), 3)
        self.assertNotIn('OFF_PROTOCOL_DRAFT', json.dumps(requests))

    def test_evidence_first_preflight_rejects_unfit_minimum_synthesis_before_http(self):
        from decision_memory.grounded import initial_request

        sample = context()
        minimum = sum(map(len, initial_request(sample, 'evidence-first')))
        with tempfile.TemporaryDirectory() as temporary:
            output = Path(temporary) / 'out'
            with model_server([(200, completion({'topics': []}))]) as (url, requests):
                config = self.config(url, grounded_strategy='evidence-first', max_input_chars=minimum)
                with self.assertRaisesRegex(ValueError, '最小合成'):
                    execute_run([sample], config, output)
            self.assertEqual(requests, [])
            self.assertFalse(output.exists())

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

    def test_plain_transcript_user_attribution_passes_run_and_validate(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dialogue = 'User: Use the loading bay after closing.'
            with zipfile.ZipFile(root / 'input.zip', 'w') as archive:
                archive.writestr('R1/上下文/技术设计确认记录.md',
                                 dialogue + '\nAssistant: Use the main entrance instead?')
                archive.writestr('R1/产物/技术方案.md', 'Use the loading bay after closing.')
            draft = {'items': [{'kind': 'scope',
                     'conclusion': {'text': '用户要求闭馆后走装卸区。', 'evidence': ['D00001']},
                     'reason': {'text': '未说明', 'evidence': []},
                     'boundary': {'text': '闭馆后转运。', 'evidence': ['A00001']}}], 'open_questions': []}
            with model_server([(200, completion(draft)), (200, completion(reviewed(draft)))]) as (url, requests):
                (root / 'model.toml').write_text('[model]\nendpoint=' + json.dumps(url) +
                    '\nmodel="fixture"\napi_key_env=""\n', encoding='utf-8')
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['run', '--dataset', str(root / 'input.zip'), '--scope', 'stage',
                        '--config', str(root / 'model.toml'), '--output', str(root / 'result'),
                        '--max-http-attempts', '2']), 0)
                    saved = root / 'result/samples/001/result.json'
                    self.assertEqual(main(['validate', '--dataset', str(root / 'input.zip'), '--result', str(saved)]), 0)
            self.assertEqual(len(requests), 2)
            packet = json.loads(requests[0]['messages'][1]['content'])
            first = packet['sources'][0]
            self.assertEqual(first[5:8], ['user', 1, dialogue])
            self.assertEqual(read_json(saved)['items'][0]['conclusion']['evidence'][0]['quote'], dialogue)

    def test_run_accepts_a_working_hour_limit_with_a_relative_business_day_anchor(self):
        dialogue = ('User: For depot-access forms received after closing, start at opening on the next business day. '
                    'The staff must process the form within two business hours of that opening.')
        draft = {'items': [{'kind': 'constraint',
                            'conclusion': {'text': '闭门后收到的入场表从下一个工作日开门时起算，两个工作小时内办结。',
                                           'evidence': ['D00001']},
                            'reason': {'text': '未说明', 'evidence': []},
                            'boundary': {'text': '仅适用于闭门后提交的入场表。', 'evidence': ['D00001']}}],
                 'open_questions': []}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package, config, output = root / 'input.zip', root / 'model.toml', root / 'result'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('R1/上下文/技术设计确认记录.md', dialogue)
                archive.writestr('R1/产物/技术方案.md', 'Depot access form rules for after-hours submissions.')
            with model_server([(200, completion(draft)), (200, completion(reviewed(draft)))]) as (url, requests):
                config.write_text('[model]\nendpoint=' + json.dumps(url) + '\nmodel="fixture"\napi_key_env=""\n',
                                  encoding='utf-8')
                with redirect_stdout(io.StringIO()):
                    self.assertEqual(main(['run', '--dataset', str(package), '--scope', 'stage', '--config', str(config),
                                           '--output', str(output), '--max-http-attempts', '2']), 0)
                    saved = output / 'samples/001/result.json'
                    self.assertEqual(main(['validate', '--dataset', str(package), '--result', str(saved)]), 0)
            self.assertEqual(len(requests), 2)
            atoms = read_json(saved)['grounding_proof']['literal_bindings']['atoms']
            bindings = atoms['fields'][0]['bindings']
            self.assertEqual({key for atom in bindings for key in atom['keys']},
                             {'number:2', 'business-hours:2', 'relative-business-day:next'})

    def test_literal_review_repair_points_to_the_old_rule_in_the_right_field(self):
        dialogue = ('Assistant: The contractor access card proposal locks after 36 hours of inactivity.\n'
                    'User: Contractor access cards now lock after 18 hours of inactivity. Replace the draft threshold.\n'
                    'User: Camera archives lock after 36 hours of inactivity.')
        draft = {'items': [{'kind': 'correction',
                            'conclusion': {'text': '外包门禁卡闲置 18 小时后锁定。', 'evidence': ['D00002']},
                            'reason': {'text': '未说明', 'evidence': []},
                            'boundary': {'text': '旧草案中的 36 小时只说明此前的门槛。',
                                         'evidence': ['D00001']}}], 'open_questions': []}
        incomplete = json.loads(json.dumps(draft))
        incomplete['items'][0]['conclusion']['text'] = '外包门禁卡闲置锁定的时限从 36 小时调整到 18 小时。'
        raw_review = reviewed(incomplete)
        correction = {'additions': [{'item_id': 'K001', 'field': 'conclusion', 'source_ids': ['D00001']}]}
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            package = root / 'input.zip'
            with zipfile.ZipFile(package, 'w') as archive:
                archive.writestr('R1/上下文/技术设计确认记录.md', dialogue)
                archive.writestr('R1/产物/技术方案.md', 'Contractor access cards lock after 18 hours of inactivity.')
            sample, = load_run_samples(dataset=package, scope='stage')
            replies = [(200, completion(payload)) for payload in (draft, raw_review, correction)]
            with model_server(replies) as (url, requests):
                report = execute_run([sample], self.config(url), root / 'out', scope='stage', max_http_attempts=3)
            audit = root / 'out/samples/001/audit'
            self.assertEqual(read_json(audit / '02-review-2.json'), correction)
            applied = read_json(audit / '02-review-2.applied.json')
            expected = json.loads(json.dumps(raw_review))
            expected['items'][0]['conclusion']['evidence'].append('D00001')
            self.assertEqual(applied, expected)
            self.assertEqual(read_json(audit / 'state.json')['steps'][-1]['applied_response_file'],
                             '02-review-2.applied.json')
        self.assertEqual(report['status'], 'completed')
        self.assertEqual(len(requests), 3)
        self.assertNotIn('repair_source_hints ', requests[1]['messages'][1]['content'])
        repair = requests[2]['messages'][1]['content']
        hints = json.loads(repair.rsplit('\nrepair_source_hints ', 1)[1].splitlines()[0])
        self.assertEqual(hints, [{'item_id': 'K001', 'field': 'conclusion',
                                  'missing': [{'text': '36 小时', 'candidate_source_ids': ['D00001']}]}])
        self.assertNotIn('上次完整回复', repair)
        self.assertLess(len(repair), len(requests[1]['messages'][1]['content']))

    def test_missing_literal_without_linked_evidence_saves_the_original_without_rewriting(self):
        draft = proposed()
        invalid = reviewed(draft)
        invalid['items'][0]['conclusion']['text'] += ' 必须保留 97 小时。'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(p)) for p in (proposed(), invalid)]) as (url, requests):
                report = execute_run([context()], self.config(url), root / 'out', scope='stage', max_http_attempts=3)
            audit = root / 'out/samples/001/audit'
            self.assertEqual(read_json(audit / 'draft.json')['payload'], invalid)
        self.assertEqual(report['status'], 'failed')
        self.assertEqual(len(requests), 2)

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

    def test_cli_validates_run_results_from_the_same_manifest_or_requirement_zip(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            dataset(root / 'input.zip', count=1)
            text = '详情缓存仅处理展示数据，订单不改。'
            (root / 'd.md').write_text('**用户：**\n' + text, encoding='utf-8')
            (root / 'a.md').write_text(text, encoding='utf-8')
            (root / 'manifest.json').write_text(json.dumps({'schema_version': 1, 'requirement_id': 'R',
                'nodes': [{'id': 'N', 'stage': 'interface-review', 'dialogue': 'd.md', 'artifact': 'a.md'}]}),
                encoding='utf-8')
            cases = (
                ('--dataset', 'input.zip', 'requirement', 'D.design.00002'),
                ('--manifest', 'manifest.json', 'stage', 'D00002'),
                ('--manifest', 'manifest.json', 'requirement', 'D.N.00002'),
            )
            for source_flag, name, scope, source_id in cases:
                with self.subTest(source=source_flag, scope=scope):
                    output = root / f'{name}-{scope}'
                    payload = {'items': [{'conclusion': {'text': text, 'evidence': [source_id]},
                        'reason': {'text': '未说明', 'evidence': []},
                        'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}
                    source_args = [source_flag, str(root / name)]
                    with model_server([(200, completion(value)) for value in (payload, reviewed(payload))]) as (url, requests):
                        config = root / 'model.toml'
                        config.write_text('[model]\nendpoint=' + json.dumps(url) +
                                          '\nmodel="fixture"\napi_key_env=""\n', encoding='utf-8')
                        run_log = io.StringIO()
                        with redirect_stdout(run_log):
                            code = main(['run', *source_args, '--scope', scope, '--config', str(config),
                                         '--output', str(output)])
                    self.assertEqual(code, 0, run_log.getvalue())
                    self.assertEqual(len(requests), 2)
                    check_log = io.StringIO()
                    with redirect_stdout(check_log):
                        code = main(['validate', *source_args, '--result', str(output / 'samples/001/result.json')])
                    self.assertEqual(code, 0, check_log.getvalue())
                    self.assertEqual(json.loads(check_log.getvalue())['exact_citations'], 'passed')

    def test_all_first_requests_include_system_prompt_before_any_http(self):
        from decision_memory.grounded import EXTRACT, source_packet
        from decision_memory.sources import index_materials
        large = index_materials('**用户：**\n' + '可复用知识。' * 300, '明确范围。', 'design', 'LARGE', 'd.md', 'a.md')
        limit = len(source_packet(large)) + len(EXTRACT) - 10
        with tempfile.TemporaryDirectory() as temporary, model_server([]) as (url, requests):
            path = Path(temporary) / 'out'
            with self.assertRaises(ValueError):
                execute_run([context(), large], self.config(url, max_input_chars=limit), path, scope='stage')
            self.assertFalse(path.exists())
            self.assertEqual(requests, [])

    def test_artifact_only_preflight_checks_filtered_request_not_large_dialogue(self):
        from decision_memory.sources import index_materials
        sample = index_materials('**用户：**\n' + '无关讨论。' * 5000, '明确范围。', 'design', 'LARGE', 'd.md', 'a.md')
        value = {'items': [{'conclusion': {'text': '明确范围。', 'evidence': [{'source_id': 'A00001', 'quote': '明确范围。'}]},
                           'reason': {'text': '未说明', 'evidence': []},
                           'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}
        with tempfile.TemporaryDirectory() as temporary, model_server([(200, completion(value))]) as (url, requests):
            report = execute_run([sample], self.config(url, max_input_chars=8000), Path(temporary) / 'out',
                                 scope='stage', mode='artifact-only')
            self.assertEqual(report['status'], 'completed')
            self.assertEqual(len(requests), 1)

    def test_absolute_deadline_interrupts_dripping_body_and_slow_headers(self):
        for phase in ('body', 'headers'):
            with self.subTest(phase=phase), tempfile.TemporaryDirectory() as temporary:
                requests = []

                class Handler(BaseHTTPRequestHandler):
                    def do_POST(self):
                        requests.append(self.rfile.read(int(self.headers['Content-Length'])))
                        body = json.dumps(completion({'items': [], 'open_questions': []})).encode()
                        try:
                            if phase == 'headers':
                                time.sleep(3.4)
                            self.send_response(200)
                            self.send_header('Content-Length', str(len(body)))
                            self.end_headers()
                            if phase == 'body':
                                for index in range(0, len(body), 20):
                                    self.wfile.write(body[index:index + 20]); self.wfile.flush()
                                    time.sleep(0.6)
                            else:
                                self.wfile.write(body)
                        except (BrokenPipeError, ConnectionResetError):
                            pass

                    def log_message(self, *args):
                        pass

                server = ThreadingHTTPServer(('127.0.0.1', 0), Handler)
                thread = threading.Thread(target=server.serve_forever, daemon=True); thread.start()
                try:
                    started = time.monotonic()
                    report = execute_run([context()], self.config(f'http://127.0.0.1:{server.server_port}/chat'),
                                         Path(temporary) / 'out', scope='stage', mode='baseline', max_seconds=2)
                    elapsed = time.monotonic() - started
                finally:
                    server.shutdown(); server.server_close(); thread.join()
                self.assertLess(elapsed, 2.8)
                self.assertEqual(report['status'], 'unknown')
                self.assertEqual(len(requests), 1)


if __name__ == '__main__':
    unittest.main()
