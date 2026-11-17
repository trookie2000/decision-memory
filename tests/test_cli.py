import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path

from http_fixture import completion, model_server
from test_validation import valid_payload

ROOT = Path(__file__).resolve().parents[1]


class CliTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.directory = Path(self.temp.name)
        self.dialogue = self.directory / 'dialogue.md'
        self.artifact = self.directory / 'artifact.md'
        self.dialogue.write_text('门槛改成 120 元', encoding='utf-8')
        self.artifact.write_text('本次活动门槛为 120 元', encoding='utf-8')
        self.config = self.directory / 'config.toml'

    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'decision_memory', *map(str, args)], cwd=ROOT, capture_output=True, text=True)

    def configure(self, endpoint):
        self.config.write_text(f'[model]\nendpoint = "{endpoint}"\nmodel = "fixture"\napi_key_env = ""\nretries = 0\n', encoding='utf-8')

    def sample_args(self):
        return ['--dialogue', self.dialogue, '--artifact', self.artifact, '--stage', 'analysis']

    def test_extract_validate_and_reject_overwrite_via_normal_entry_point(self):
        output = self.directory / 'result.json'
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            result = self.run_cli('extract', *self.sample_args(), '--config', self.config, '--mode', 'baseline', '--output', output)
            self.assertEqual(result.returncode, 0, result.stderr)
            repeated = self.run_cli('extract', *self.sample_args(), '--config', self.config, '--mode', 'baseline', '--output', output)
            self.assertNotEqual(repeated.returncode, 0)
            self.assertEqual(len(requests), 1)
        self.assertTrue(output.with_suffix('.md').is_file())
        checked = self.run_cli('validate', *self.sample_args(), '--result', output)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        payload = json.loads(output.read_text())
        payload['items'][0]['conclusion']['text'] = '被修改的结论'
        output.write_text(json.dumps(payload))
        self.assertNotEqual(self.run_cli('validate', *self.sample_args(), '--result', output).returncode, 0)

    def test_partial_batch_failure_resumes_only_failed_sample(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            for name in ('REQ-1', 'REQ-2'):
                archive.writestr(f'{name}/上下文/需求确认记录.md', self.dialogue.read_text())
                archive.writestr(f'{name}/产物/需求分析.md', self.artifact.read_text())
        output = self.directory / 'runs'
        gateway_error = {'error': {'code': 'InvalidToken', 'message': 'fixture token expired'},
                         'request_id': 'fixture-batch-request'}
        with model_server([(401, gateway_error), (200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            args = ['batch', '--unit', 'stage', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output]
            first = self.run_cli(*args)
            self.assertNotEqual(first.returncode, 0)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['failed'], 1)
            self.assertIn('InvalidToken', summary['records'][0]['error'])
            self.assertIn('fixture token expired', summary['records'][0]['error'])
            self.assertIn('fixture-batch-request', summary['records'][0]['error'])
            second = self.run_cli(*args, '--resume')
            self.assertEqual(second.returncode, 0, second.stderr)
            self.assertEqual(len(requests), 3)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['reused'], 1)

    def test_missing_credentials_fail_without_creating_success_result(self):
        self.config.write_text('[model]\nendpoint="https://example.invalid/chat/completions"\nmodel="fixture"\napi_key_env="DECISION_MEMORY_MISSING_TEST_KEY"\n')
        output = self.directory / 'result.json'
        result = self.run_cli('extract', *self.sample_args(), '--config', self.config, '--output', output)
        self.assertNotEqual(result.returncode, 0)
        self.assertFalse(output.exists())
        self.assertIn('DECISION_MEMORY_MISSING_TEST_KEY', result.stderr)

    def test_resume_rejects_changed_model_without_spending_another_call(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            archive.writestr('REQ-1/上下文/需求确认记录.md', self.dialogue.read_text())
            archive.writestr('REQ-1/产物/需求分析.md', self.artifact.read_text())
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            args = ['batch', '--unit', 'stage', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', self.directory / 'runs']
            self.assertEqual(self.run_cli(*args).returncode, 0)
            self.config.write_text(self.config.read_text().replace('fixture', 'different-model'))
            resumed = self.run_cli(*args, '--resume')
            self.assertNotEqual(resumed.returncode, 0)
            self.assertEqual(len(requests), 1)

    def test_review_commands_keep_unreviewed_scores_unknown(self):
        result_file = self.directory / 'result.json'
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            extracted = self.run_cli('extract', *self.sample_args(), '--config', self.config, '--mode', 'baseline', '--output', result_file)
            self.assertEqual(extracted.returncode, 0, extracted.stderr)
        worksheet = self.directory / 'review.json'
        created = self.run_cli('review', 'init', '--result', result_file, '--output', worksheet)
        self.assertEqual(created.returncode, 0, created.stderr)
        scored = self.run_cli('review', 'score', '--worksheet', worksheet)
        self.assertEqual(scored.returncode, 0, scored.stderr)
        self.assertIsNone(json.loads(scored.stdout)['precision_reviewed'])

    def test_select_official_stage_and_check_reference_quotes(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            archive.writestr('REQ-1/上下文/需求确认记录.md', self.dialogue.read_text())
            archive.writestr('REQ-1/产物/需求分析.md', self.artifact.read_text())
            archive.writestr('REQ-1/上下文/技术设计确认记录.md', 'OTHER_STAGE_SECRET')
            archive.writestr('REQ-1/产物/技术方案.md', 'OTHER_STAGE_ARTIFACT')
            archive.writestr('评测标注/答案.md', 'ANNOTATION_SECRET')
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            extracted = self.run_cli('extract', '--unit', 'stage', '--dataset', dataset, '--sample-id', 'REQ-1-analysis',
                                     '--config', self.config, '--mode', 'baseline', '--output', self.directory / 'official.json')
        self.assertEqual(extracted.returncode, 0, extracted.stderr)
        self.assertNotIn('SECRET', json.dumps(requests))
        validated = self.run_cli('validate', '--dataset', dataset, '--result', self.directory / 'official.json')
        self.assertEqual(validated.returncode, 0, validated.stderr)
        references = self.directory / 'references.json'
        references.write_text(json.dumps([{'id': 'R1', 'text': '120 元',
                               'evidence': [{'source': 'dialogue', 'quote': '120 元'}]}]))
        checked = self.run_cli('review', 'check-references', '--dataset', dataset, '--sample-id', 'REQ-1-analysis',
                               '--references', references)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['semantic_quality'], 'requires_human_review')

    def test_default_batch_groups_nodes_and_reads_extra_files_without_answers(self):
        dataset = self.directory / 'requirements.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            archive.writestr('REQ-1/上下文/需求确认记录.md', '**用户：**\n门槛先用100元')
            archive.writestr('REQ-1/产物/需求分析.md', '门槛100元')
            archive.writestr('REQ-1/上下文/技术设计确认记录.md', '**用户：**\n门槛改成 120 元')
            archive.writestr('REQ-1/产物/技术方案.md', '本次活动门槛为 120 元')
            archive.writestr('REQ-1/产物/口径说明.md', 'EXTRA_INPUT_DEFINITION')
            archive.writestr('评测标注/答案.md', 'ANSWER_MUST_NOT_LEAK')
        inspected = self.run_cli('inspect', '--dataset', dataset)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        inventory = json.loads(inspected.stdout)
        self.assertEqual(inventory['count'], 1)
        self.assertEqual(inventory['samples'][0]['stage'], 'requirement')
        extra = inventory['samples'][0]['documents'][-1]
        self.assertEqual(extra['document_type'], 'supplement')
        self.assertIsNone(extra['node'])
        payload = valid_payload()
        payload['items'][0]['conclusion']['evidence'][0]['source_id'] = 'D2-00002'
        payload['items'][0]['boundary']['evidence'][0]['source_id'] = 'A2-00001'
        output = self.directory / 'requirements-run'
        with model_server([(200, completion(payload))]) as (endpoint, requests):
            self.configure(endpoint)
            batch = self.run_cli('batch', '--dataset', dataset, '--config', self.config,
                                 '--mode', 'baseline', '--output', output)
        self.assertEqual(batch.returncode, 0, batch.stderr)
        self.assertEqual(json.loads(batch.stdout)['unit'], 'requirement')
        self.assertTrue((output / 'REQ-1.json').is_file())
        self.assertIn('EXTRA_INPUT_DEFINITION', json.dumps(requests))
        self.assertNotIn('ANSWER_MUST_NOT_LEAK', json.dumps(requests))
        validated = self.run_cli('validate', '--dataset', dataset, '--result', output / 'REQ-1.json')
        self.assertEqual(validated.returncode, 0, validated.stderr)

    def test_battle_help_is_available_without_network(self):
        help_result = self.run_cli('battle', '--help')
        self.assertEqual(help_result.returncode, 0)
        for command in ('doctor', 'start', 'resume', 'status', 'reconcile'):
            self.assertIn(command, help_result.stdout)

    def test_v2_review_cli_keeps_raw_output_and_requires_explicit_coverage(self):
        from decision_memory.evaluation import digest
        from decision_memory.pipeline import extract
        from decision_memory.client import Config, ModelClient
        from decision_memory.sources import load_sample
        from decision_memory.storage import save_result
        prediction = self.directory / 'prediction.json'
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            result = extract(load_sample(self.dialogue, self.artifact, 'analysis'),
                             ModelClient(Config(endpoint=endpoint, model='fixture', api_key_env='')), 'baseline')
            save_result(prediction, result)
        original_prediction = prediction.read_bytes()
        references = self.directory / 'references.json'
        reference_rows = [{'id': 'R1', 'text': '门槛 120 元'}]
        references.write_text(json.dumps(reference_rows))
        policy = self.directory / 'policy.json'
        policy.write_text(json.dumps({'schema_version': 1, 'references_digest': digest(reference_rows),
                                     'assignments': [{'reference_id': 'R1', 'kind': 'decision', 'rationale': 'fixture'}]}))
        worksheet = self.directory / 'worksheet.json'
        initialized = self.run_cli('review', 'init', '--result', prediction, '--references', references,
                                   '--schema-version', '2', '--reference-policy', policy, '--review-kind', 'agent',
                                   '--output', worksheet)
        self.assertEqual(initialized.returncode, 0, initialized.stderr)
        judgments = self.directory / 'judgments.json'
        judgments.write_text(json.dumps([{'item_id': 'K001', 'correct': True, 'citation_support': True,
                                         'matches': ['R1'], 'notes': 'fixture'}]))
        reviewed = self.directory / 'reviewed.json'
        applied = self.run_cli('review', 'apply', '--worksheet', worksheet, '--judgments', judgments, '--output', reviewed)
        self.assertEqual(applied.returncode, 0, applied.stderr)
        partial_score = json.loads(applied.stdout)['score']
        self.assertIsNone(partial_score['reference_coverage']['decision']['coverage'])
        coverage = self.directory / 'coverage.json'
        coverage.write_text(json.dumps([{'reference_id': 'R1', 'coverage': 'full', 'supported_by': [
            {'target': 'item', 'id': 'K001', 'field': 'conclusion'}], 'notes': 'fixture full'}]))
        complete_review = self.directory / 'complete.json'
        completed = self.run_cli('review', 'apply', '--worksheet', reviewed, '--reference-judgments', coverage,
                                 '--output', complete_review)
        self.assertEqual(completed.returncode, 0, completed.stderr)
        scored = self.run_cli('review', 'score', '--worksheet', complete_review)
        self.assertEqual(scored.returncode, 0, scored.stderr)
        final_score = json.loads(scored.stdout)
        self.assertTrue(final_score['complete'])
        self.assertEqual(final_score['human_review'], 'pending')
        self.assertEqual(prediction.read_bytes(), original_prediction)
        repeated = self.run_cli('review', 'apply', '--worksheet', reviewed, '--reference-judgments', coverage,
                                '--output', complete_review)
        self.assertEqual(repeated.returncode, 2)

    def test_interrupted_batch_response_records_failure_and_continues(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            for name in ('REQ-1', 'REQ-2'):
                archive.writestr(f'{name}/上下文/需求确认记录.md', self.dialogue.read_text())
                archive.writestr(f'{name}/产物/需求分析.md', self.artifact.read_text())
        output = self.directory / 'runs'
        with model_server([(200, 'interrupted-body'), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            result = self.run_cli('batch', '--unit', 'stage', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output)
        self.assertEqual(result.returncode, 2, result.stderr)
        summary = json.loads((output / 'summary.json').read_text())
        self.assertEqual((summary['failed'], summary['succeeded']), (1, 1))
        self.assertTrue((output / 'REQ-2-analysis.json').exists())

    def test_resume_rejects_modified_markdown_handoff(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            archive.writestr('REQ-1/上下文/需求确认记录.md', self.dialogue.read_text())
            archive.writestr('REQ-1/产物/需求分析.md', self.artifact.read_text())
        output = self.directory / 'runs'
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            args = ['batch', '--unit', 'stage', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output]
            self.assertEqual(self.run_cli(*args).returncode, 0)
            (output / 'REQ-1-analysis.md').write_text('被改写的交接内容')
            result = self.run_cli(*args, '--resume')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Markdown', result.stdout)


if __name__ == '__main__':
    unittest.main()
