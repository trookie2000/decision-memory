import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelError
from decision_memory.comparison import run_comparison, validate_comparison
from decision_memory.requirements import assemble_requirement, read_requirement_manifest
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, result_digest
from http_fixture import completion, model_server
from test_requirement_memory import item
from test_requirement_protocol import packet

ROOT = Path(__file__).resolve().parents[1]
UNSTATED = {'text': '未说明', 'evidence': []}


def input_files(root, value=None):
    value = value or packet()
    manifest = {'schema_version': 1, 'requirement_id': value['requirement_id'], 'nodes': []}
    for node in value['nodes']:
        entry = {'id': node['id'], 'stage': node['stage']}
        for field in ('dialogue', 'artifact'):
            name = node['id'] + '-' + field + '.md'
            (root / name).write_text(node[field], encoding='utf-8')
            entry[field] = name
        manifest['nodes'].append(entry)
    path = root / 'manifest.json'
    path.write_text(json.dumps(manifest, ensure_ascii=False))
    (root / 'gold.json').write_text('GOLD_ONLY_SENTINEL_NOT_FOR_EXTRACTION')
    requirement_id, nodes = read_requirement_manifest(path)
    return path, assemble_requirement(requirement_id, nodes), nodes


def replies():
    return [
        {'events': [{'id': 'E1', 'knowledge_key': 'retry', 'node_id': 'design', 'operation': 'add',
                     'previous': None, 'item': item('最终不重试', 'D.design.00002', '改成不重试')}],
         'open_questions': []},
        {'items': [item('失败最多尝试 3 次', 'D00002', '失败最多尝试 3 次')],
         'open_questions': [{'text': '报警范围待确认',
                             'evidence': [{'source_id': 'D00003', 'quote': '报警范围待确认'}]}]},
        {'items': [item('不重试', 'A00001', '不重试')], 'open_questions': []}]


class RequirementComparisonTests(unittest.TestCase):
    def test_model_fixture_wakes_an_idle_selector_on_shutdown(self):
        script = '''
import socketserver
import threading
from http_fixture import model_server

waiting = threading.Event()

class IdleSelector(socketserver._ServerSelector):
    def select(self, timeout=None):
        waiting.set()
        return super().select()

socketserver._ServerSelector = IdleSelector
with model_server([]) as (_, requests):
    assert waiting.wait(2)
    assert requests == []
assert requests == []
'''
        finished = subprocess.run([sys.executable, '-B', '-c', script], cwd=ROOT / 'tests',
                                  capture_output=True, text=True, timeout=5)
        self.assertEqual(finished.returncode, 0, finished.stderr)

    def test_real_cli_runs_both_methods_without_reference_leak_or_baseline_cleanup(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, sample, nodes = input_files(root)
            config_path = root / 'model.toml'
            output = root / 'compare'
            with model_server([(200, completion(value)) for value in replies()]) as (endpoint, requests):
                config_path.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
                command = [sys.executable, '-m', 'decision_memory', 'requirement', 'compare', '--manifest', str(path),
                           '--config', str(config_path), '--mode', 'baseline', '--output', str(output)]
                process = subprocess.run(command, cwd=ROOT, capture_output=True, text=True)
                self.assertEqual(process.returncode, 0, process.stderr)
                bundle = read_json(output / 'prediction.json')
                self.assertEqual(len(requests), 3)
                self.assertNotIn('GOLD_ONLY_SENTINEL', json.dumps(requests))
                self.assertNotEqual(subprocess.run(command, cwd=ROOT, capture_output=True).returncode, 0)
                self.assertEqual(len(requests), 3)
            validate_comparison(bundle, sample, nodes, output, Config.load(config_path))
            views = {bundle['labels'][label]: view for label, view in bundle['prediction']['variants'].items()}
            self.assertEqual([row['conclusion']['text'] for row in views['whole']['items']], ['最终不重试'])
            self.assertEqual([row['conclusion']['text'] for row in views['per_node']['items']],
                             ['失败最多尝试 3 次', '不重试'])
            self.assertEqual(views['per_node']['open_questions'][0]['text'], '报警范围待确认')
            self.assertEqual(views['per_node']['items'][0]['conclusion']['evidence'][0]['source_id'],
                             'D.analysis.00002')
            self.assertEqual(bundle['metrics']['whole']['calls'], 1)
            self.assertEqual(bundle['metrics']['per_node']['calls'], 2)

    def test_modified_scoring_view_is_rejected_even_after_rehash(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, sample, nodes = input_files(root)
            output = root / 'compare'
            with model_server([(200, completion(value)) for value in replies()]) as (endpoint, _):
                config = Config(endpoint, 'fixture', api_key_env='', retries=0)
                bundle = run_comparison(sample, nodes, config, 'baseline', output)
            modified = copy.deepcopy(bundle)
            label = next(label for label, method in modified['labels'].items() if method == 'per_node')
            modified['prediction']['variants'][label]['items'].pop(0)
            modified['result_digest'] = result_digest(modified)
            with self.assertRaises(ValueError):
                validate_comparison(modified, sample, nodes, output)
            raw = output / 'nodes' / 'analysis.json'
            value = read_json(raw)
            value['items'][0]['conclusion']['text'] = '被改写的答案'
            value['result_digest'] = result_digest(value)
            raw.write_text(json.dumps(value, ensure_ascii=False))
            with self.assertRaises(ValueError):
                validate_comparison(bundle, sample, nodes, output)

    def test_failed_partial_run_cannot_repeat_paid_calls_in_the_same_directory(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, sample, nodes = input_files(root)
            output = root / 'compare'
            with model_server([(200, completion(replies()[0])), (400, {'error': {'message': 'fixture rejection'}})]) as (endpoint, requests):
                config = Config(endpoint, 'fixture', api_key_env='', retries=0)
                with self.assertRaises(ModelError):
                    run_comparison(sample, nodes, config, 'baseline', output)
                self.assertTrue((output / 'whole.json').exists())
                with self.assertRaises(ValueError):
                    run_comparison(sample, nodes, config, 'baseline', output)
                self.assertEqual(len(requests), 2)

    def test_per_node_algorithm_can_receive_a_coding_node_without_changing_legacy_cli(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            value = packet()
            value['nodes'][1]['stage'] = 'coding'
            _, sample, nodes = input_files(root, value)
            with model_server([(200, completion(value)) for value in replies()]) as (endpoint, _):
                config = Config(endpoint, 'fixture', api_key_env='', retries=0)
                result = run_comparison(sample, nodes, config, 'baseline', root / 'compare')
            self.assertEqual(len(result['raw_files']), 3)

    def test_changed_node_snapshot_is_rejected_before_any_model_request(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            _, sample, nodes = input_files(root)
            changed = index_materials('新的一版原文。', '新的产物。', 'analysis', 'analysis',
                                      'analysis-dialogue.md', 'analysis-artifact.md')
            nodes[0] = ('analysis', 'analysis', changed)
            with model_server([]) as (endpoint, requests):
                config = Config(endpoint, 'fixture', api_key_env='', retries=0)
                with self.assertRaisesRegex(ValueError, '输入|版本'):
                    run_comparison(sample, nodes, config, 'baseline', root / 'compare')
                self.assertEqual(requests, [])


if __name__ == '__main__':
    unittest.main()
