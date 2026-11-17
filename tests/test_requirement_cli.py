import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path
from decision_memory.storage import result_digest

from http_fixture import completion, model_server
from test_requirement_memory import added, item, updated
import test_requirement_sources as source_fixtures
import test_optimizations as official_fixtures

ROOT = Path(__file__).resolve().parents[1]


class RequirementCliTests(unittest.TestCase):
    def run_cli(self, *args):
        return subprocess.run([sys.executable, '-m', 'decision_memory', *map(str, args)],
                              cwd=ROOT, capture_output=True, text=True)

    def test_full_requirement_extract_and_offline_validate_use_normal_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = source_fixtures.RequirementSourceTests().fixture(root)
            config, output = root / 'model.toml', root / 'result.json'
            revised = updated()
            revised['item'] = item('主链路不再重试', 'D.N2.00002', '不再重试')
            payload = {'events': [added(), revised], 'open_questions': []}
            with model_server([(200, completion(payload))]) as (endpoint, requests):
                config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
                extracted = self.run_cli('requirement', 'extract', '--manifest', manifest,
                                         '--config', config, '--mode', 'baseline', '--output', output)
                self.assertEqual(extracted.returncode, 0, extracted.stderr)
                repeated = self.run_cli('requirement', 'extract', '--manifest', manifest,
                                        '--config', config, '--mode', 'baseline', '--output', output)
                self.assertNotEqual(repeated.returncode, 0)
                self.assertEqual(len(requests), 1)
                self.assertNotIn('不能传给提取器的参考', json.dumps(requests, ensure_ascii=False))
            raw = json.loads(output.read_text())
            self.assertEqual(raw['items'][0]['conclusion']['text'], '主链路不再重试')
            self.assertEqual(len(raw['items']), 1)
            self.assertEqual(raw['history'][0]['status'], 'superseded')
            rendered = output.with_suffix('.md').read_text()
            self.assertIn('# 需求知识 DEMO-1', rendered)
            self.assertIn('变更历史', rendered)
            self.assertIn('失败最多尝试 3 次', rendered)
            checked = self.run_cli('requirement', 'validate', '--manifest', manifest, '--result', output)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            raw['history'][0]['status'] = 'current'
            output.write_text(json.dumps(raw, ensure_ascii=False))
            self.assertNotEqual(self.run_cli('requirement', 'validate', '--manifest', manifest,
                                            '--result', output).returncode, 0)

    def test_default_verified_persists_field_review_and_validates_without_network(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = source_fixtures.RequirementSourceTests().fixture(root)
            config, output = root / 'model.toml', root / 'result.json'
            revised = updated()
            revised['item'] = item('主链路不再重试', 'D.N2.00002', '不再重试')
            first = {'events': [added(), revised], 'open_questions': []}
            final = {**first, 'review_notes': [], 'grounding_review': {'fields': [
                {'knowledge_key': 'main-retry', 'field': name, 'status': status, 'notes': '已核对原文。'}
                for name, status in [('conclusion', 'supported'), ('reason', 'unstated'), ('boundary', 'unstated')]],
                'reason_checks': []}}
            with model_server([(200, completion(first)), (200, completion(final))]) as (endpoint, requests):
                config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
                extracted = self.run_cli('requirement', 'extract', '--manifest', manifest,
                    '--config', config, '--output', output)
                self.assertEqual(extracted.returncode, 0, extracted.stderr)
                self.assertEqual(len(requests), 2)
                self.assertNotIn('不能传给提取器的参考', json.dumps(requests, ensure_ascii=False))
            saved = json.loads(output.read_text())
            self.assertEqual(saved['checks']['field_grounding'], 'model_reviewed')
            checked = self.run_cli('requirement', 'validate', '--manifest', manifest, '--result', output)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            saved['grounding_review']['fields'].pop()
            saved['result_digest'] = result_digest(saved)
            output.write_text(json.dumps(saved, ensure_ascii=False))
            rejected = self.run_cli('requirement', 'validate', '--manifest', manifest, '--result', output)
            self.assertNotEqual(rejected.returncode, 0)

    def test_inspect_accepts_coding_nodes_without_model_credentials(self):
        with tempfile.TemporaryDirectory() as directory:
            manifest, _ = source_fixtures.RequirementSourceTests().fixture(Path(directory))
            result = self.run_cli('requirement', 'inspect', '--manifest', manifest)
        self.assertEqual(result.returncode, 0, result.stderr)
        inspected = json.loads(result.stdout)['requirements'][0]
        self.assertEqual(inspected['requirement_id'], 'DEMO-1')
        self.assertEqual([node['stage'] for node in inspected['nodes']], ['analysis', 'coding'])

    def test_dataset_requirement_extract_and_validate_resolve_the_same_sources(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            dataset = official_fixtures.OfficialEvalTests().dataset(root)
            config, output = root / 'model.toml', root / 'requirement.json'
            event = added()
            event['node_id'] = 'design'
            event['item'] = item('甲方负责排序', 'A.design.00001', '甲方负责排序')
            with model_server([(200, completion({'events': [event], 'open_questions': []}))]) as (endpoint, requests):
                config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
                result = self.run_cli('requirement', 'extract', '--dataset', dataset, '--requirement-id', 'REQ-one',
                                      '--config', config, '--mode', 'baseline', '--output', output)
                self.assertEqual(result.returncode, 0, result.stderr)
                sent = json.loads(requests[0]['messages'][1]['content'])
                self.assertEqual([node['id'] for node in sent['nodes']], ['analysis', 'design'])
                self.assertNotIn('positives', sent)
            checked = self.run_cli('requirement', 'validate', '--dataset', dataset, '--result', output)
            self.assertEqual(checked.returncode, 0, checked.stderr)
            saved = json.loads(output.read_text())
            self.assertEqual(saved['requirement_id'], 'REQ-one')
            self.assertIn('REQ-one/产物/技术方案.md', saved['items'][0]['conclusion']['evidence'][0]['file'])

    def test_invalid_source_arguments_fail_before_the_model_call(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            manifest, _ = source_fixtures.RequirementSourceTests().fixture(root)
            result = self.run_cli('requirement', 'extract', '--manifest', manifest,
                                  '--requirement-id', 'other', '--config', root / 'missing.toml',
                                  '--output', root / 'result.json')
        self.assertNotEqual(result.returncode, 0)
        self.assertIn('requirement-id', result.stderr)


if __name__ == '__main__':
    unittest.main()
