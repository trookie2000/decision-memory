"""Public CLI behavior using a real local HTTP server and no model credentials."""
import copy
import hashlib
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_memory.storage import result_digest
from http_fixture import completion, model_server
from test_requirement_audit import audit_reply, event

ROOT = Path(__file__).resolve().parents[1]


def replies():
    first = {'events': [event()], 'open_questions': []}
    final = {**copy.deepcopy(first), 'review_notes': [], 'grounding_review': {'fields': [
        {'knowledge_key': 'detail-cache', 'field': name, 'status': status, 'notes': '已核对原文。'}
        for name, status in [('conclusion', 'supported'), ('reason', 'unstated'), ('boundary', 'unstated')]],
        'reason_checks': []}, 'evidence_audit': audit_reply()}
    return first, final


class AuditCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'decision_memory', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True, timeout=20)

    def fixture(self, root, endpoint, extra=''):
        (root / 'dialogue.md').write_text('**用户：**\n仅商品详情缓存改造；周五上线。', encoding='utf8')
        (root / 'artifact.md').write_text('# 技术方案\n仅商品详情缓存改造；周五上线。', encoding='utf8')
        manifest = root / 'manifest.json'
        manifest.write_text(json.dumps({'schema_version': 1, 'requirement_id': 'AUDIT', 'nodes': [
            {'id': 'N1', 'stage': 'design', 'dialogue': 'dialogue.md', 'artifact': 'artifact.md'}]}))
        config = root / 'model.toml'
        config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=3\n{extra}')
        return manifest, config, root / 'result.json'

    def extract(self, paths, mode='verified', audit=True):
        manifest, config, output = paths
        args = ['requirement', 'extract', '--manifest', manifest, '--config', config,
                '--output', output, '--mode', mode]
        if audit:
            args += ['--evidence-audit', 'v1']
        return self.run_cli(*args)

    def state(self, root):
        return json.loads((root / 'result.run' / 'state.json').read_text())

    def test_success_persists_two_steps_schema3_html_and_validates_offline(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(paths[2].read_text())
            self.assertEqual(saved['schema_version'], 3)
            self.assertEqual(saved['evidence_audit']['audit_status'], 'passed')
            self.assertEqual(saved['metrics']['calls'], 2)
            self.assertEqual(saved['metrics']['http_attempts'], 2)
            state = self.state(root)
            self.assertEqual(state['status'], 'completed')
            self.assertEqual(state['effective_config']['retries'], 0)
            self.assertEqual(state['transport_policy'], 'no-auto-http-retry')
            self.assertTrue(all(row['status'] == 'completed' for row in state['steps']))
            self.assertTrue((root / 'result.run' / '01-extract-1.json').is_file())
            self.assertTrue((root / 'result.audit.html').is_file())
            self.assertIn('候选处置', (root / 'result.audit.html').read_text())
            self.assertEqual(len(requests), 2)
            sent = requests[1]['messages'][1]['content']
            self.assertEqual(sent.count('"scope": "requirement"'), 1)
            checked = self.run_cli('requirement', 'validate', '--manifest', paths[0], '--result', paths[2])
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(len(requests), 2)

    def test_structure_only_repairs_are_bounded_by_four_actual_http_requests(self):
        first, final = replies()
        bad_final = copy.deepcopy(final)
        bad_final['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion({'events': []})), (200, completion(first)),
                (200, completion(bad_final)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            result = self.extract(self.fixture(root, endpoint))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(requests), 4)
            self.assertEqual(len(self.state(root)['steps']), 4)
            self.assertTrue((root / 'result.run' / '01-extract-1.json').is_file())

    def test_needs_review_is_a_durable_draft_not_a_successful_result(self):
        first, final = replies()
        final['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2, result.stderr)
            self.assertEqual(len(requests), 2)
            self.assertFalse(paths[2].exists())
            self.assertEqual(self.state(root)['status'], 'needs_review')
            draft = json.loads((root / 'result.run' / 'draft.json').read_text())
            self.assertEqual(draft['evidence_audit']['audit_status'], 'needs_review')
            repeated = self.extract(paths)
            self.assertEqual(repeated.returncode, 2)
            self.assertEqual(len(requests), 2)

    def test_mixed_errors_repair_structure_before_stopping_for_review(self):
        first, final = replies()
        final['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        malformed = copy.deepcopy(final)
        malformed['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(malformed)),
                (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 3)
            self.assertEqual(self.state(root)['status'], 'needs_review')
            self.assertIn('needs_review', requests[2]['messages'][1]['content'])

    def test_still_malformed_after_repair_is_invalid_not_review_complete(self):
        first, final = replies()
        final['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        final['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 3)
            self.assertEqual(self.state(root)['status'], 'failed')
            draft = json.loads((root / 'result.run' / 'draft.json').read_text())
            self.assertEqual(draft['validation'], 'invalid')
            self.assertFalse(paths[2].exists())

    def test_http_errors_do_not_use_the_configured_automatic_retries(self):
        for status in (429, 503):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, model_server([
                    (status, {'error': {'message': 'temporary'}})]) as (endpoint, requests):
                root = Path(directory)
                result = self.extract(self.fixture(root, endpoint))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(len(requests), 1)
                self.assertEqual(self.state(root)['status'], 'failed')
                self.assertIsNone(self.state(root)['metrics']['prompt_tokens'])

    def test_interrupted_second_request_is_unknown_and_first_step_is_preserved(self):
        first, _ = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, 'interrupted-body')]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 2)
            state = self.state(root)
            self.assertEqual(state['status'], 'unknown')
            self.assertEqual(state['steps'][0]['status'], 'completed')
            self.assertEqual(state['steps'][1]['status'], 'unknown')
            self.assertEqual(state['steps'][1]['provider_execution'], 'unknown')
            self.assertEqual(state['steps'][1]['failure_phase'], 'transport')
            self.assertTrue((root / 'result.run' / '01-extract-1.json').is_file())
            self.assertFalse(paths[2].exists())
            self.assertIn('calls', state)
            self.assertEqual(state['calls'][0]['prompt_tokens'], 100)
            self.assertEqual(state['calls'][0]['completion_tokens'], 40)
            self.assertEqual(state['calls'][0]['transport_status'], 'received')
            self.assertEqual(state['calls'][1]['transport_status'], 'unknown')
            self.assertEqual(state['steps'][0]['call_numbers'], [1])
            self.assertEqual(state['steps'][1]['call_numbers'], [2])
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertEqual(len(requests), 2)

    def test_short_content_length_is_unknown_even_when_the_json_is_valid(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, ('short-content-length', completion(first))),
                (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            self.assertEqual(self.state(root)['status'], 'unknown')
            self.assertFalse(paths[2].exists())

    def test_structure_repair_cannot_promote_existing_needs_review_to_passed(self):
        first, final = replies()
        malformed = copy.deepcopy(final)
        malformed['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        malformed['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(malformed)),
                (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 3)
            self.assertFalse(paths[2].exists())
            draft = json.loads((root / 'result.run' / 'draft.json').read_text())
            self.assertEqual(draft['validation'], 'invalid')

    def test_invalid_json_or_truncated_model_output_does_not_trigger_structure_repair(self):
        invalid = completion('{"events":[')
        truncated = completion({'events': []}); truncated['choices'][0]['finish_reason'] = 'length'
        for payload in (invalid, truncated):
            with self.subTest(payload=payload), tempfile.TemporaryDirectory() as directory, model_server([
                    (200, payload)]) as (endpoint, requests):
                root = Path(directory)
                result = self.extract(self.fixture(root, endpoint))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(len(requests), 1)
                self.assertEqual(self.state(root)['status'], 'failed')

    def test_strict_json_failure_saves_private_content_and_received_transport(self):
        content = '```json\n{"events":[],"note":"synthetic-private-model-text"}\n```'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(content))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint, 'json_mode=true\n')
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            self.assertEqual(requests[0]['response_format'], {'type': 'json_object'})
            state = self.state(root)
            step = state['steps'][0]
            self.assertEqual(step['provider_execution'], 'response_received')
            self.assertEqual(step['failure_phase'], 'parse')
            diagnostic = step['response_diagnostic']
            self.assertEqual(diagnostic['category'], 'invalid_json')
            self.assertEqual(diagnostic['finish_reason'], 'stop')
            self.assertEqual(diagnostic['json_error'], {'line': 1, 'column': 1, 'position': 0})
            self.assertTrue(diagnostic['code_fenced'])
            self.assertEqual(diagnostic['content_bytes'], len(content.encode('utf8')))
            self.assertEqual(diagnostic['content_sha256'], hashlib.sha256(content.encode('utf8')).hexdigest())
            raw = root / 'result.run' / step['raw_response_file']
            self.assertEqual(raw.read_text(), content)
            self.assertEqual(raw.stat().st_mode & 0o777, 0o600)
            self.assertEqual(state['calls'][0]['response_diagnostic'], diagnostic)
            self.assertEqual(state['metrics']['prompt_tokens'], 100)
            self.assertNotIn('synthetic-private-model-text', json.dumps(state))
            self.assertNotIn('synthetic-private-model-text', result.stderr)
            self.assertFalse(paths[2].exists())
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertEqual(len(requests), 1)

    def test_json_error_position_and_length_stop_are_durable_without_repair(self):
        invalid = completion('{\n  "events": [\n}')
        truncated = completion('{"events":[')
        truncated['choices'][0]['finish_reason'] = 'length'
        for payload, category in ((invalid, 'invalid_json'), (truncated, 'output_truncated')):
            with self.subTest(category=category), tempfile.TemporaryDirectory() as directory, model_server([
                    (200, payload)]) as (endpoint, requests):
                root = Path(directory)
                result = self.extract(self.fixture(root, endpoint))
                self.assertEqual(result.returncode, 2)
                self.assertEqual(len(requests), 1)
                step = self.state(root)['steps'][0]
                self.assertEqual(step['provider_execution'], 'response_received')
                self.assertEqual(step['failure_phase'], 'parse')
                self.assertEqual(step['response_diagnostic']['category'], category)
                if category == 'invalid_json':
                    self.assertEqual(step['response_diagnostic']['json_error']['line'], 3)
                    self.assertEqual(step['response_diagnostic']['json_error']['column'], 1)
                else:
                    self.assertEqual(step['response_diagnostic']['finish_reason'], 'length')
                self.assertEqual((root / 'result.run' / step['raw_response_file']).read_text(),
                                 payload['choices'][0]['message']['content'])

    def test_model_credential_echo_is_removed_from_content_and_diagnostics(self):
        secret, extra = 'synthetic-content-auth-value', 'synthetic-content-extra-header'
        content = 'not JSON: ' + secret + ' Bearer ' + secret + ' ' + extra
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(content))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            paths[1].write_text(paths[1].read_text().replace('api_key_env=""', 'api_key_env="AUDIT_CONTENT_TOKEN"')
                                + 'headers_env={"X-Test-Token"="AUDIT_CONTENT_EXTRA"}\n')
            env = os.environ.copy()
            env.update(AUDIT_CONTENT_TOKEN=secret, AUDIT_CONTENT_EXTRA=extra)
            result = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'extract',
                '--manifest', str(paths[0]), '--config', str(paths[1]), '--output', str(paths[2]),
                '--evidence-audit', 'v1'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            step = self.state(root)['steps'][0]
            self.assertTrue(step['response_diagnostic']['redaction_applied'])
            raw = root / 'result.run' / step['raw_response_file']
            self.assertIn('[REDACTED]', raw.read_text())
            for value in (secret, extra):
                self.assertNotIn(value, result.stdout + result.stderr)
                for path in (root / 'result.run').rglob('*'):
                    if path.is_file():
                        self.assertNotIn(value, path.read_text(), str(path))

    def test_protocol_failure_records_the_error_before_bounded_repair(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion({'events': []})), (200, completion(first)),
                (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            result = self.extract(self.fixture(root, endpoint))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(requests), 3)
            step = self.state(root)['steps'][0]
            self.assertEqual(step['provider_execution'], 'response_received')
            self.assertEqual(step['failure_phase'], 'validation')
            self.assertIn('open_questions', step['validation_error'])
            self.assertEqual(step['validation'], 'invalid')

    def test_mode_conflicts_existing_runs_and_candidate_overflow_send_no_requests(self):
        with tempfile.TemporaryDirectory() as directory, model_server([]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            for mode in ('baseline', 'artifact-only'):
                self.assertEqual(self.extract(paths, mode=mode).returncode, 2)
            (root / 'result.run').mkdir()
            self.assertEqual(self.extract(paths).returncode, 2)
            paths[1].write_text(paths[1].read_text().replace('retries=3', 'retries=0'))
            self.assertEqual(self.extract(paths, mode='baseline', audit=False).returncode, 2)
            self.assertEqual(requests, [])
            (root / 'result.run').rmdir()
            (root / 'artifact.md').write_text('\n'.join('约束%d。' % n for n in range(257)))
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertEqual(requests, [])

    def test_existing_html_report_refuses_requests_before_creating_a_run(self):
        with tempfile.TemporaryDirectory() as directory, model_server([]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            (root / 'result.audit.html').write_text('existing report')
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertFalse((root / 'result.run').exists())
            self.assertEqual(requests, [])

    def test_valid_json_with_wrong_top_level_can_be_repaired_but_fenced_text_cannot(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion('[]')), (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            self.assertEqual(self.extract(self.fixture(root, endpoint)).returncode, 0)
            self.assertEqual(len(requests), 3)
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion('```json\n' + json.dumps(first) + '\n```'))]) as (endpoint, requests):
            root = Path(directory)
            self.assertEqual(self.extract(self.fixture(root, endpoint)).returncode, 2)
            self.assertEqual(len(requests), 1)

    def test_credentials_echoed_by_http_error_never_enter_checkpoint_files(self):
        secret = 'synthetic-audit-auth-value'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (401, {'error': {'message': 'invalid token ' + secret}})]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            paths[1].write_text(paths[1].read_text().replace('api_key_env=""', 'api_key_env="AUDIT_TEST_TOKEN"'))
            env = os.environ.copy()
            env['AUDIT_TEST_TOKEN'] = secret
            result = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'extract',
                '--manifest', str(paths[0]), '--config', str(paths[1]), '--output', str(paths[2]),
                '--evidence-audit', 'v1'], cwd=ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            self.assertNotIn(secret, result.stderr)
            self.assertTrue((root / 'result.run' / 'state.json').is_file())
            for path in (root / 'result.run').rglob('*'):
                if path.is_file():
                    self.assertNotIn(secret, path.read_text(), str(path))

    def test_dynamic_input_limit_keeps_the_completed_first_call(self):
        first, _ = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint, 'max_input_chars=4000\n')
            result = self.extract(paths)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            self.assertTrue((root / 'result.run' / '01-extract-1.json').is_file())
            self.assertEqual(self.state(root)['status'], 'failed')

    def test_saved_audit_tampering_cannot_be_hidden_by_outer_rehash_or_schema_downgrade(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, _):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            self.assertEqual(self.extract(paths).returncode, 0)
            saved = json.loads(paths[2].read_text())
            for change in ('catalog', 'delete', 'downgrade'):
                modified = copy.deepcopy(saved)
                if change == 'catalog': modified['evidence_audit']['unit_catalog'].pop()
                if change == 'delete': modified.pop('evidence_audit')
                if change == 'downgrade': modified['schema_version'] = 2
                modified['result_digest'] = result_digest(modified)
                paths[2].write_text(json.dumps(modified, ensure_ascii=False))
                checked = self.run_cli('requirement', 'validate', '--manifest', paths[0], '--result', paths[2])
                self.assertEqual(checked.returncode, 2, change)

    def test_html_escapes_source_text_and_highlights_codepoint_spans(self):
        from decision_memory.requirement_audit_report import render_audit_html
        from decision_memory.requirement_audit import validate_evidence_audit
        from decision_memory.requirement_memory import compile_memory
        from test_requirement_audit import grounding, sample
        context = sample(artifact='# 技术方案\n仅商品详情缓存改造；周五上线。\n<script>alert(1)</script>')
        compiled = compile_memory(replies()[0], context)
        value = audit_reply()
        value['coverage_checks'].append({'unit_id': 'U004', 'disposition': 'non_reusable',
            'semantic_coverage': 'not_applicable', 'targets': [], 'exclusion_reason': 'format', 'notes': '测试原文。'})
        proof = grounding(compiled, context)
        audit = validate_evidence_audit(value, compiled, proof, context)
        html = render_audit_html({**compiled, 'evidence_audit': audit, 'sample_id': 'AUDIT'}, context)
        self.assertNotIn('<script>', html)
        self.assertIn('&lt;script&gt;', html)
        self.assertIn('<mark>', html)
        self.assertNotIn('<script src=', html)


if __name__ == '__main__':
    unittest.main()
