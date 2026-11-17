"""Source IDs over the real CLI/HTTP boundary; no remote model or credentials."""
import copy
import json
import os
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_memory.storage import result_digest
from http_fixture import completion, model_server
import test_requirement_audit_cli as v1
from test_evidence_ids import first_reply


def replies():
    first = first_reply()
    final = copy.deepcopy(v1.replies()[1])
    final['events'] = copy.deepcopy(first['events'])
    audit = final['evidence_audit']
    audit['admission_checks'][0]['direct'] = ['D.N1.00002#C001']
    for row, prefix in zip(audit['coverage_checks'], ('D', 'A')):
        row['targets'][0]['evidence'] = prefix + '.N1.00002#C001'
    return first, final


class AuditV2CliTests(unittest.TestCase):
    run_cli = v1.AuditCliTests.run_cli
    fixture = v1.AuditCliTests.fixture
    state = v1.AuditCliTests.state

    def extract(self, paths):
        manifest, config, output = paths
        return self.run_cli('requirement', 'extract', '--manifest', manifest, '--config', config,
                            '--output', output, '--mode', 'verified', '--evidence-audit', 'v2')

    def test_two_steps_expand_ids_publish_schema4_and_validate_without_http(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            (root / 'gold.json').write_text('GOLD_SENTINEL_NEVER_SEND')
            result = self.extract(paths)
            self.assertEqual(result.returncode, 0, result.stderr)
            saved = json.loads(paths[2].read_text())
            self.assertEqual(saved['schema_version'], 4)
            self.assertEqual(saved['evidence_audit_version'], 'v2')
            self.assertEqual(saved['model_evidence_format'], 'source-id-v1')
            self.assertEqual(saved['source_format'], 'columnar-v1')
            self.assertEqual(saved['literal_grounding']['policy'], 'source-symbols-v1')
            self.assertEqual(saved['items'][0]['conclusion']['evidence'][0],
                             {'source_id': 'D.N1.00002', 'quote': '仅商品详情缓存改造；',
                              'file': 'dialogue.md', 'line': 2, 'message': 1})
            self.assertEqual(saved['metrics']['http_attempts'], 2)
            raw = json.loads((root / 'result.run' / '01-extract-1.json').read_text())
            self.assertEqual(raw['events'][0]['item']['conclusion']['evidence'][0], 'D.N1.00002#C001')
            sent = requests[1]['messages'][1]['content']
            self.assertEqual(sent.count('"source_columns"'), 1)
            self.assertIn('field_evidence_candidates', sent)
            self.assertNotIn('GOLD_SENTINEL_NEVER_SEND', json.dumps(requests))
            self.assertEqual(self.state(root)['effective_config']['retries'], 0)
            self.assertTrue((root / 'result.audit.html').is_file())
            self.assertTrue((root / 'result.md').is_file())
            checked = self.run_cli('requirement', 'validate', '--manifest', paths[0], '--result', paths[2])
            self.assertEqual(checked.returncode, 0, checked.stderr)
            self.assertEqual(len(requests), 2)

    def test_bad_ids_and_final_coverage_repairs_stop_at_four_requests(self):
        first, final = replies()
        bad_first = copy.deepcopy(first)
        bad_first['events'][0]['item']['conclusion']['evidence'] = ['D.FAKE.1']
        bad_final = copy.deepcopy(final)
        bad_final['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(bad_first)), (200, completion(first)),
                (200, completion(bad_final)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            result = self.extract(self.fixture(root, endpoint))
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(requests), 4)
            self.assertEqual(len(self.state(root)['steps']), 4)

    def test_unquoted_implementation_symbol_must_be_repaired(self):
        first, final = replies()
        bad = copy.deepcopy(final)
        bad['events'][0]['item']['conclusion']['text'] += '，由 mergeFieldUnit 实现'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(bad)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            result = self.extract(paths)
            self.assertEqual(result.returncode, 0, result.stderr)
            self.assertEqual(len(requests), 3)
            self.assertIn('mergeFieldUnit', self.state(root)['steps'][1]['validation_error'])
            self.assertNotIn('mergeFieldUnit', paths[2].read_text())

    def test_needs_review_is_preserved_as_draft_and_never_resent(self):
        first, final = replies()
        final['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertEqual(self.state(root)['status'], 'needs_review')
            self.assertFalse(paths[2].exists())
            self.assertEqual(json.loads((root / 'result.run' / 'draft.json').read_text())['schema_version'], 4)
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertEqual(len(requests), 2)

    def test_repair_cannot_erase_uncertainty(self):
        first, final = replies()
        malformed = copy.deepcopy(final)
        malformed['evidence_audit']['admission_checks'][0]['basis'] = 'needs_review'
        malformed['evidence_audit']['coverage_checks'].pop()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(malformed)),
                (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            self.assertEqual(self.extract(paths).returncode, 2)
            self.assertFalse(paths[2].exists())
            self.assertEqual(self.state(root)['status'], 'failed')
            self.assertEqual(len(requests), 3)

    def test_invalid_json_is_not_repaired_and_interrupted_body_is_unknown(self):
        first, _ = replies()
        for responses, count, status in (
                ([(200, completion('```json\n{}\n```'))], 1, 'failed'),
                ([(200, completion(first)), (200, 'interrupted-body')], 2, 'unknown')):
            with self.subTest(status=status), tempfile.TemporaryDirectory() as directory, model_server(responses) as (endpoint, requests):
                root = Path(directory)
                paths = self.fixture(root, endpoint)
                self.assertEqual(self.extract(paths).returncode, 2)
                self.assertEqual(self.state(root)['status'], status)
                self.assertEqual(self.extract(paths).returncode, 2)
                self.assertEqual(len(requests), count)

    def test_literal_proof_or_format_tampering_rejects_even_with_outer_rehash(self):
        first, final = replies()
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion(first)), (200, completion(final))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            self.assertEqual(self.extract(paths).returncode, 0)
            saved = json.loads(paths[2].read_text())
            for change in ('delete', 'proof', 'schema', 'compound_downgrade', 'format', 'quote'):
                modified = copy.deepcopy(saved)
                if change == 'delete': modified.pop('literal_grounding')
                if change == 'proof': modified['literal_grounding']['fields'] = []
                if change == 'schema': modified['schema_version'] = 3
                if change == 'compound_downgrade':
                    modified.update(schema_version=3, evidence_audit_version='v1')
                    for key in ('model_evidence_format', 'source_format', 'literal_grounding'):
                        modified.pop(key)
                    modified['checks'].pop('literal_grounding')
                if change == 'format': modified['model_evidence_format'] = 'handwritten'
                if change == 'quote':
                    # Rebuild every event-derived field: codec check must still reject a non-ID fragment.
                    from decision_memory.requirements import load_requirement_manifest
                    from decision_memory.requirement_memory import compile_memory
                    context = load_requirement_manifest(paths[0])
                    modified['events'][0]['item']['conclusion']['evidence'][0]['quote'] = '仅商品详情缓存改造'
                    modified.update(compile_memory({k: modified[k] for k in ('events', 'open_questions', 'review_notes')}, context))
                modified['result_digest'] = result_digest(modified)
                paths[2].write_text(json.dumps(modified, ensure_ascii=False))
                checked = self.run_cli('requirement', 'validate', '--manifest', paths[0], '--result', paths[2])
                self.assertEqual(checked.returncode, 2, change)
            self.assertEqual(len(requests), 2)

    def test_valid_json_credential_echo_never_reaches_run_or_repair(self):
        secret = 'synthetic-v2-only-auth-value'
        with tempfile.TemporaryDirectory() as directory, model_server([
                (200, completion({'events': [], 'review_notes': [secret]}))]) as (endpoint, requests):
            root = Path(directory)
            paths = self.fixture(root, endpoint)
            paths[1].write_text(paths[1].read_text().replace('api_key_env=""', 'api_key_env="V2_FIXTURE_TOKEN"'))
            env = os.environ.copy()
            env['V2_FIXTURE_TOKEN'] = secret
            result = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'extract',
                '--manifest', str(paths[0]), '--config', str(paths[1]), '--output', str(paths[2]),
                '--evidence-audit', 'v2'], cwd=v1.ROOT, env=env, capture_output=True, text=True, timeout=20)
            self.assertEqual(result.returncode, 2)
            self.assertEqual(len(requests), 1)
            self.assertEqual(self.state(root)['steps'][0]['response_diagnostic']['category'], 'credential_echo')
            for path in (root / 'result.run').rglob('*'):
                if path.is_file(): self.assertNotIn(secret, path.read_text())


if __name__ == '__main__':
    unittest.main()
