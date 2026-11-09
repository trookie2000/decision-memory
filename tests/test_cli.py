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
        with model_server([(401, {}), (200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            args = ['batch', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output]
            first = self.run_cli(*args)
            self.assertNotEqual(first.returncode, 0)
            summary = json.loads((output / 'summary.json').read_text())
            self.assertEqual(summary['failed'], 1)
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
            args = ['batch', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', self.directory / 'runs']
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

    def test_interrupted_batch_response_records_failure_and_continues(self):
        dataset = self.directory / 'samples.zip'
        with zipfile.ZipFile(dataset, 'w') as archive:
            for name in ('REQ-1', 'REQ-2'):
                archive.writestr(f'{name}/上下文/需求确认记录.md', self.dialogue.read_text())
                archive.writestr(f'{name}/产物/需求分析.md', self.artifact.read_text())
        output = self.directory / 'runs'
        with model_server([(200, 'interrupted-body'), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.configure(endpoint)
            result = self.run_cli('batch', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output)
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
            args = ['batch', '--dataset', dataset, '--config', self.config, '--mode', 'baseline', '--output', output]
            self.assertEqual(self.run_cli(*args).returncode, 0)
            (output / 'REQ-1-analysis.md').write_text('被改写的交接内容')
            result = self.run_cli(*args, '--resume')
        self.assertEqual(result.returncode, 2, result.stderr)
        self.assertIn('Markdown', result.stdout)


if __name__ == '__main__':
    unittest.main()
