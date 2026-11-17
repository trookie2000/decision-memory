"""Cross-entry input semantics, with fictional files and local HTTP only."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from dataclasses import replace
from pathlib import Path

from decision_memory.client import Config, ModelClient
from decision_memory.grounded import decode_payload, source_packet
from decision_memory.pipeline import extract
from decision_memory.requirements import discover_requirements as manifest_requirements
from decision_memory.sources import discover_requirements
from decision_memory.storage import read_json, result_digest, save_result, validate_saved
from http_fixture import completion, model_server
from test_grounded import context, field, reviewed

ROOT = Path(__file__).resolve().parents[1]


class InputContractTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            'REQ-contract/上下文/需求确认记录.md': '**用户：**\r\n先用10件。\r\n',
            'REQ-contract/产物/需求分析.md': '先用10件。\n',
            'REQ-contract/上下文/技术设计确认记录.md': '**用户：**\n改为5件。\n',
            'REQ-contract/产物/技术方案.md': '改为5件。\n',
            'REQ-contract/上下文/口径说明.md': '**用户：**\n参考值20件。\n浏览次数不去重。\n',
            'REQ-contract/产物/接口清单.md': '复用原有接口。\n',
            '评测标注/答案.md': 'ANSWER_MUST_NOT_LEAK',
        }
        for name, text in self.files.items():
            target = self.root / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_bytes(text.encode())
        self.sample = discover_requirements(self.root)[0]

    def packet(self, sample=None):
        value = json.loads(source_packet(sample or self.sample))
        rows = {row[0]: dict(zip(value['source_columns'], row)) for row in value['sources']}
        return value, rows

    def proposal(self):
        return {'items': [{'conclusion': field('改为5件。', ['D2-00002', 'A2-00001']),
                           'reason': field(), 'boundary': field()}], 'open_questions': []}

    def test_directory_requirement_keeps_nodes_and_supplements_without_inferred_time(self):
        packet, rows = self.packet()
        self.assertEqual(packet['input_scope'], 'requirement')
        self.assertEqual([node['stage'] for node in packet['nodes']], ['需求分析', '技术设计'])
        self.assertEqual(rows['D1-00002']['node_order'], 0)
        self.assertEqual(rows['D2-00002']['node_order'], 1)
        for key in ('D3-00002', 'A4-00001'):
            self.assertIsNone(rows[key]['node_id'])
            self.assertIsNone(rows[key]['node_order'])
            self.assertEqual(rows[key]['document_type'], 'supplement')
        self.assertEqual(rows['D3-00002']['role'], 'user')
        self.assertEqual(rows['A4-00001']['role'], 'material')
        self.assertNotIn('ANSWER_MUST_NOT_LEAK', json.dumps(packet))

    def test_directory_zip_and_manifest_preserve_the_same_ordered_material_semantics(self):
        path = self.root / 'sample.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, text in self.files.items():
                archive.writestr(name, text)
        from_zip = discover_requirements(path)[0]
        self.assertEqual(self.packet()[0], self.packet(from_zip)[0])
        self.assertEqual(self.sample.file_hashes, from_zip.file_hashes)
        packet, rows = self.packet()
        other, other_rows = self.packet(manifest_requirements(self.root)[0])
        self.assertEqual((packet['input_scope'], other['input_scope']), ('requirement', 'requirement'))
        project = lambda values: sorted((row['file'], row['line'], row['node_order'], row['role'],
                                          row['document_type'], row['text']) for row in values
                                         if row['document_type'] != 'supplement')
        self.assertEqual(project(rows.values()), project(other_rows.values()))

    def test_missing_node_for_ordered_material_fails_before_a_model_call(self):
        catalog = dict(self.sample.catalog)
        catalog['D2-00002'] = replace(catalog['D2-00002'], node=None)
        with self.assertRaises(ValueError):
            source_packet(replace(self.sample, catalog=catalog))

    def test_supplement_user_marker_cannot_supply_a_confirmed_user_attribution(self):
        payload = self.proposal()
        payload['items'][0]['reason'] = field('用户确认参考值20件。', ['D3-00002'])
        with self.assertRaisesRegex(ValueError, '用户'):
            decode_payload(payload, self.sample)
        payload['items'][0]['reason'] = field()
        payload['items'].append({'conclusion': field('浏览次数不去重。', ['D3-00003']),
                                 'reason': field(), 'boundary': field()})
        self.assertEqual(len(decode_payload(payload, self.sample)['items']), 2)

    def test_new_result_binds_requirement_scope_nodes_and_material_types(self):
        with model_server([(200, completion(self.proposal())), (200, completion(reviewed(self.proposal())))]) as (url, requests):
            client = ModelClient(Config(endpoint=url, model='fixture', api_key_env=''))
            result = extract(self.sample, client, 'grounded', run_dir=self.root / 'run')
        self.assertEqual(len(requests), 2)
        first = json.loads(requests[0]['messages'][1]['content'])
        self.assertEqual(first['input_scope'], 'requirement')
        self.assertEqual(len(first['nodes']), 2)
        self.assertEqual(result['input_scope'], 'requirement')
        self.assertEqual(result['nodes'], first['nodes'])
        self.assertEqual(len(result['input_contract']['documents']), 6)
        output = self.root / 'result.json'
        save_result(output, result, sample=self.sample)
        validate_saved(read_json(output), self.sample)
        from decision_memory.official_eval import validated_inputs
        _, source_rows, _ = validated_inputs(self.root, [read_json(output)], 'requirement')
        supplementary = [row for row in source_rows['REQ-contract'] if row['document_type'] == 'supplement']
        self.assertTrue(supplementary)
        self.assertTrue(all(row['node_id'] is None and row['node_order'] is None for row in supplementary))
        for mutation in ('scope', 'node', 'material', 'requirement_id', 'protocol_downgrade', 'version'):
            changed = copy.deepcopy(result)
            if mutation == 'scope':
                changed['input_scope'] = 'stage'
            elif mutation == 'node':
                changed['nodes'][0]['order'] = 9
            elif mutation == 'material':
                changed['input_contract']['documents'][-1]['document_type'] = 'artifact'
            elif mutation == 'requirement_id':
                changed['requirement_id'] = 'other'
            elif mutation == 'protocol_downgrade':
                changed['extraction_protocol'] = 'whole-line-grounded-v1'
            else:
                changed.pop('version')
            changed['result_digest'] = result_digest(changed)
            with self.subTest(mutation=mutation), self.assertRaises(ValueError):
                validate_saved(changed, self.sample)

    def test_grounded_can_run_and_validate_through_the_requirement_manifest_cli(self):
        manifest = self.root / 'manifest.json'
        manifest.write_text(json.dumps({'schema_version': 1, 'requirement_id': 'REQ-contract', 'nodes': [
            {'id': 'analysis', 'stage': 'analysis', 'dialogue': 'REQ-contract/上下文/需求确认记录.md',
             'artifact': 'REQ-contract/产物/需求分析.md'},
            {'id': 'design', 'stage': 'design', 'dialogue': 'REQ-contract/上下文/技术设计确认记录.md',
             'artifact': 'REQ-contract/产物/技术方案.md'}]}))
        payload = self.proposal()
        payload['items'][0]['conclusion']['evidence'] = ['D.design.00002', 'A.design.00001']
        output, config = self.root / 'cli.json', self.root / 'config.toml'
        with model_server([(200, completion(payload)), (200, completion(reviewed(payload)))]) as (url, requests):
            config.write_text(f'[model]\nendpoint="{url}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            result = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'extract',
                '--manifest', str(manifest), '--mode', 'grounded', '--config', str(config), '--output', str(output)],
                cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(result.returncode, 0, result.stderr)
        self.assertEqual(len(requests), 2)
        saved = read_json(output)
        self.assertEqual(saved['scope'], 'requirement')
        self.assertEqual([node['id'] for node in saved['nodes']], ['analysis', 'design'])
        checked = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'validate',
            '--manifest', str(manifest), '--result', str(output)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['snapshot_replay'], 'passed')

    def test_frozen_legacy_stage_result_still_validates_offline(self):
        saved = read_json(Path(__file__).parent / 'fixtures/grounded-v1-stage.json')
        self.assertEqual(saved['extraction_protocol'], 'whole-line-grounded-v1')
        validate_saved(saved, context())

    def test_legacy_results_are_not_reused_as_current_model_runs(self):
        saved = read_json(Path(__file__).parent / 'fixtures/grounded-v1-stage.json')
        client = ModelClient(Config(**saved['requested_config']))
        with self.assertRaisesRegex(ValueError, '运行配置或模式'):
            validate_saved(saved, context(), client, 'grounded')
        self.assertEqual(client.calls, [])

    def test_premerge_legacy_prompt_is_validated_with_its_own_release_fingerprint(self):
        from decision_memory.evaluation import digest
        saved = read_json(Path(__file__).parent / 'fixtures/grounded-v1-stage.json')
        saved['version'] = '0.3.0'
        saved['prompt_hash'] = '81de86cb3815c973941c27724e072bec783c072a66ab0a7ae4cf1b4a18a559db'
        saved['run_signature'] = digest([context().fingerprint, saved['requested_config'],
                                         'grounded', saved['prompt_hash'], '0.3.0'])
        saved['result_digest'] = result_digest(saved)
        validate_saved(saved, context())
        saved['prompt_hash'] = 'c635f1d36f8d701806ba1a2a7f4b9f336ec155ade27081b939e2c817514bbfb6'
        saved['result_digest'] = result_digest(saved)
        with self.assertRaises(ValueError):
            validate_saved(saved, context())

    def test_both_dataset_cli_entries_send_the_same_whole_requirement_and_validate_it(self):
        packets, contracts = [], []
        config = self.root / 'model.toml'
        for command, selector in ((['extract'], ['--sample-id', 'REQ-contract']),
                                  (['requirement', 'extract'], ['--requirement-id', 'REQ-contract'])):
            with self.subTest(command=command):
                output = self.root / ('root.json' if len(command) == 1 else 'requirement.json')
                with model_server([(200, completion(self.proposal())),
                                   (200, completion(reviewed(self.proposal())))]) as (url, requests):
                    config.write_text(f'[model]\nendpoint="{url}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
                    result = subprocess.run([sys.executable, '-m', 'decision_memory', *command,
                        '--dataset', str(self.root), *selector, '--mode', 'grounded', '--config', str(config),
                        '--output', str(output)], cwd=ROOT, text=True, capture_output=True)
                self.assertEqual(result.returncode, 0, result.stderr)
                self.assertEqual(len(requests), 2)
                packet = json.loads(requests[0]['messages'][1]['content'])
                packets.append(packet)
                contracts.append(read_json(output)['input_contract'])
                self.assertEqual(packet['input_scope'], 'requirement')
                self.assertEqual(len(packet['documents']), 6)
                self.assertNotIn('ANSWER_MUST_NOT_LEAK', json.dumps(requests))
                for check in (['validate'], ['requirement', 'validate']):
                    checked = subprocess.run([sys.executable, '-m', 'decision_memory', *check,
                        '--dataset', str(self.root), '--result', str(output)],
                        cwd=ROOT, text=True, capture_output=True)
                    self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(packets[0], packets[1])
        self.assertEqual(contracts[0], contracts[1])
        inspected = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'inspect',
            '--dataset', str(self.root), '--mode', 'grounded'], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(inspected.returncode, 0, inspected.stderr)
        self.assertEqual(json.loads(inspected.stdout)['requirements'][0]['input_contract'], contracts[0])

    def test_manifest_coding_node_preserves_order_without_changing_the_stage_cli(self):
        from decision_memory.requirements import assemble_requirement
        from decision_memory.sources import index_materials
        sample = assemble_requirement('REQ-coding', [
            ('N1', 'analysis', index_materials('**用户：**\n先保留', '先保留', 'analysis', 'N1', 'a-d.md', 'a-a.md')),
            ('N2', 'coding', index_materials('**用户：**\n保持兼容', '保持兼容', 'coding', 'N2', 'c-d.md', 'c-a.md'))])
        packet, rows = self.packet(sample)
        self.assertEqual([node['stage'] for node in packet['nodes']], ['analysis', 'coding'])
        self.assertEqual(rows['D.N2.00002']['node_order'], 1)
        self.assertEqual(rows['D.N2.00002']['document_type'], 'dialogue')
