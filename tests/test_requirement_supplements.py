"""Supplementary facts have provenance, never an invented stage or authority."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import Mock

from decision_memory.client import Config, ModelClient
from decision_memory.comparison import run_comparison
from decision_memory.evaluation import digest
from decision_memory.evidence_ids import source_packet, decode_event_stream
from decision_memory.field_grounding import field_evidence_candidates
from decision_memory.input_contract import describe_input
from decision_memory.official_eval import validated_inputs
from decision_memory.requirement_audit import _admissions, _attributions, build_coverage_catalog
from decision_memory.requirement_grounding import validate_grounding_review
from decision_memory.requirement_memory import compile_memory, current_order
from decision_memory.requirement_pipeline import extract_requirement
from decision_memory.requirements import (assemble_requirement, discover_requirements,
    load_requirement_manifest, read_requirement_manifest)
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, result_digest, save_result, validate_saved
from http_fixture import completion, model_server

ROOT = Path(__file__).resolve().parents[1]
UNKNOWN = {'text': '未说明', 'evidence': []}


class RequirementSupplementTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.files = {
            'REQ-supp/上下文/需求确认记录.md': '**用户：**\n门槛10件。\n仅展示数据。\n',
            'REQ-supp/产物/需求分析.md': '门槛10件。\n',
            'REQ-supp/上下文/技术设计确认记录.md': '**用户：**\n改为5件。\n',
            'REQ-supp/产物/技术方案.md': '改为5件。\n',
            'REQ-supp/上下文/口径说明.md': '**用户：**\n门槛20件。\n浏览次数不去重。\n仅展示数据。\n',
            'REQ-supp/产物/接口清单.md': '复用既有接口。\n',
            '评测标注/答案.md': 'DO_NOT_READ_GOLD',
        }
        for name, text in self.files.items():
            p = self.root / name
            p.parent.mkdir(parents=True, exist_ok=True)
            p.write_text(text)
        self.sample = discover_requirements(self.root)[0]

    def key(self, text, *, supplement=False):
        return next(key for key, source in self.sample.catalog.items()
                    if source.text == text and (source.document_type == 'supplement') == supplement)

    def evidence(self, text, *, supplement=False):
        return {'source_id': self.key(text, supplement=supplement), 'quote': text}

    def item(self, text, *, supplement=False):
        return {'conclusion': {'text': text, 'evidence': [self.evidence(text, supplement=supplement)]},
                'reason': copy.deepcopy(UNKNOWN), 'boundary': copy.deepcopy(UNKNOWN)}

    def event(self, identity='e1', key='threshold', node='analysis', operation='add', previous=None, item=None):
        result = {'id': identity, 'knowledge_key': key, 'node_id': node, 'operation': operation, 'previous': previous}
        if operation in ('add', 'update'):
            result['item'] = item or self.item('门槛10件。')
        return result

    def compiled(self, events):
        return compile_memory({'events': events, 'open_questions': []}, self.sample)

    def review(self, events):
        compiled = self.compiled(events)
        return {'events': events, 'open_questions': [], 'review_notes': [], 'grounding_review': {
            'fields': [{'knowledge_key': index['knowledge_key'], 'field': field,
                        'status': 'unstated' if item[field]['text'] == '未说明' else 'supported', 'notes': 'fixture'}
                       for index, item in zip(compiled['memory_index'], compiled['items'])
                       for field in ('conclusion', 'reason', 'boundary')], 'reason_checks': []}}

    def test_directory_reads_all_extra_files_without_creating_extra_nodes(self):
        self.assertEqual([node['id'] for node in self.sample.nodes], ['analysis', 'design'])
        self.assertEqual(len(self.sample.file_hashes), 6)
        supplements = [s for s in self.sample.catalog.values() if s.document_type == 'supplement']
        self.assertTrue(supplements)
        self.assertTrue(all(self.sample.node_order(s.source_id) is None for s in supplements))
        packet = json.loads(self.sample.prompt_text())
        self.assertNotIn('DO_NOT_READ_GOLD', json.dumps(packet))
        self.assertTrue(any(row['role'] == 'user' and row['document_type'] == 'supplement'
                            and row['node_order'] is None for row in packet['sources']))
        contract = describe_input(self.sample)
        self.assertEqual(len(contract['nodes']), 2)
        self.assertTrue(any(doc['document_type'] == 'supplement' and doc['node_id'] is None
                            for doc in contract['documents']))

    def test_zip_and_directory_agree_and_legacy_view_does_not_read_supplements(self):
        path = self.root / 'input.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            for name, text in self.files.items():
                archive.writestr(name, text)
        other = discover_requirements(path)[0]
        self.assertEqual(other.fingerprint, self.sample.fingerprint)
        self.assertEqual(other.file_hashes, self.sample.file_hashes)
        legacy = discover_requirements(self.root, include_supplements=False)[0]
        self.assertFalse(legacy.has_supplements)
        self.assertEqual(len(legacy.file_hashes), 4)

    def manifest(self, supplements):
        p = self.root / 'manifest.json'
        p.write_text(json.dumps({'schema_version': 1, 'requirement_id': 'REQ-supp', 'nodes': [
            {'id': node['id'], 'stage': node['stage'],
             'dialogue': next(name for name in node['files'] if '/上下文/' in name),
             'artifact': next(name for name in node['files'] if '/产物/' in name)}
            for node in self.sample.nodes], 'supplements': supplements}))
        return p

    def test_manifest_explicit_supplements_match_directory_and_keep_coding_labels(self):
        names = [name for name in self.files if name.endswith(('口径说明.md', '接口清单.md'))]
        p = self.manifest(names)
        loaded = load_requirement_manifest(p)
        self.assertEqual(loaded.fingerprint, self.sample.fingerprint)
        self.assertEqual(loaded.file_hashes, self.sample.file_hashes)
        raw = json.loads(p.read_text())
        raw['nodes'][-1]['stage'] = 'coding'
        p.write_text(json.dumps(raw))
        self.assertEqual(load_requirement_manifest(p).nodes[-1]['stage'], 'coding')
        with self.assertRaisesRegex(ValueError, '逐节点对照'):
            read_requirement_manifest(p)

    def test_manifest_rejects_annotation_paths_duplicates_and_nodes_reused_as_supplements(self):
        for names in (['评测标注/答案.md'], ['../escape.md'], ['REQ-supp/产物/需求分析.md'],
                      ['REQ-supp/上下文/口径说明.md'] * 2):
            with self.subTest(names=names), self.assertRaises(ValueError):
                load_requirement_manifest(self.manifest(names))

    def test_global_group_alias_cannot_collide_with_real_node_id(self):
        base = index_materials('正式记录', '正式产物', 'coding', 'n', 'd.md', 'a.md')
        sample = assemble_requirement('REQ', [('supplement-001', 'coding', base)], [('D', 's.md', '补充事实')])
        self.assertEqual(len(sample.catalog), 3)
        self.assertEqual(sample.node_order('D.supplement-001.00001'), 0)
        self.assertIsNone(sample.node_order('D.supplement-001-ref.00001'))

    def test_background_fact_can_be_added_without_an_invented_stage(self):
        background = self.event('b', 'count', None, item=self.item('浏览次数不去重。', supplement=True))
        compiled = self.compiled([self.event(), background])
        self.assertEqual(len(compiled['items']), 2)
        self.assertIsNone(compiled['history'][-1]['node_id'])
        self.assertIsNone(current_order(compiled, self.sample, 'count'))
        validate_grounding_review(self.review([self.event(), background])['grounding_review'], compiled, self.sample)
        field_evidence_candidates(compiled, self.sample)

    def test_undated_events_cannot_borrow_formal_stage_evidence(self):
        with self.assertRaisesRegex(ValueError, '未来节点'):
            self.compiled([self.event(node=None)])

    def test_background_cannot_replace_confirmed_value_or_invalidate_it(self):
        for operation in ('update', 'invalidate'):
            event = self.event('b', node=None, operation=operation, previous='e1',
                               item=self.item('门槛20件。', supplement=True))
            if operation == 'invalidate':
                event.update(note='补充材料改口径', evidence=[self.evidence('门槛20件。', supplement=True)])
            with self.subTest(operation=operation), self.assertRaises(ValueError):
                self.compiled([self.event(), event])

    def test_background_cannot_borrow_old_confirmation_as_a_new_replacement_anchor(self):
        item = self.item('门槛20件。', supplement=True)
        item['conclusion']['evidence'].append(self.evidence('门槛10件。'))
        with self.assertRaisesRegex(ValueError, '旧确认'):
            self.compiled([self.event(), self.event('b', operation='update', previous='e1', item=item)])

    def test_background_role_markers_cannot_supply_user_attribution_in_the_default_event_flow(self):
        item = self.item('浏览次数不去重。', supplement=True)
        item['conclusion']['text'] = '用户确认浏览次数不去重。'
        with self.assertRaisesRegex(ValueError, '正式用户证据'):
            self.compiled([self.event('b', 'count', None, item=item)])

    def test_formal_following_decision_can_replace_older_rule_with_background_present(self):
        changed = self.event('b', node='design', operation='update', previous='e1', item=self.item('改为5件。'))
        compiled = self.compiled([self.event(), changed])
        self.assertEqual(compiled['items'][0]['conclusion']['text'], '改为5件。')
        self.assertEqual(compiled['history'][0]['status'], 'superseded')

    def test_background_can_fill_unknown_boundary_but_not_rewrite_known_boundary(self):
        filled = self.item('门槛10件。')
        filled['boundary'] = {'text': '仅展示数据。', 'evidence': [self.evidence('仅展示数据。', supplement=True)]}
        update = self.event('b', node=None, operation='update', previous='e1', item=filled)
        compiled = self.compiled([self.event(), update])
        self.assertEqual(compiled['items'][0]['boundary']['text'], '仅展示数据。')
        self.assertEqual(current_order(compiled, self.sample, 'threshold'), 0)
        first = self.event(item=filled)
        wrong = copy.deepcopy(filled)
        wrong['boundary'] = {'text': '浏览次数不去重。', 'evidence': [self.evidence('浏览次数不去重。', supplement=True)]}
        with self.assertRaisesRegex(ValueError, '已有知识'):
            self.compiled([first, self.event('b', node=None, operation='update', previous='e1', item=wrong)])

    def test_undated_merge_adds_evidence_without_changing_text_or_erasing_known_order(self):
        item = self.item('仅展示数据。')
        merge = self.event('b', node=None, operation='merge', previous='e1')
        merge['evidence'] = {'conclusion': [self.evidence('仅展示数据。', supplement=True)]}
        compiled = self.compiled([self.event(item=item), merge])
        self.assertEqual(len(compiled['items'][0]['conclusion']['evidence']), 2)
        self.assertEqual(current_order(compiled, self.sample, 'threshold'), 0)

    def test_real_future_nodes_still_cannot_supply_earlier_events(self):
        with self.assertRaisesRegex(ValueError, '未来节点'):
            self.compiled([self.event(item=self.item('改为5件。'))])

    def test_supplement_admission_is_distinct_from_user_or_artifact_confirmation(self):
        item = self.item('浏览次数不去重。', supplement=True)
        compiled = self.compiled([self.event('b', 'count', None, item=item)])
        items = {'count': compiled['items'][0]}
        row = {'knowledge_key': 'count', 'basis': 'supplement_direct', 'direct': item['conclusion']['evidence'],
               'proposal': [], 'confirmation': [], 'notes': '无冲突的背景定义'}
        _admissions([row], items, self.sample)
        for basis in ('user_direct', 'artifact_direct'):
            with self.subTest(basis=basis), self.assertRaises(ValueError):
                _admissions([{**row, 'basis': basis}], items, self.sample)
        units = build_coverage_catalog(self.sample)
        self.assertTrue(any(row['quote'] == '复用既有接口。' for row in units))

    def test_source_id_audit_packet_retains_null_order_and_typed_documents(self):
        packet = json.loads(source_packet(self.sample))
        rows = [dict(zip(packet['source_columns'], row)) for row in packet['sources']]
        key = self.key('浏览次数不去重。', supplement=True)
        self.assertIsNone(next(row for row in rows if row['source_id'] == key)['node_order'])
        self.assertTrue(any(doc['document_type'] == 'supplement' for doc in packet['documents']))
        background = self.event('b', 'count', None, item=self.item('浏览次数不去重。', supplement=True))
        background['item']['conclusion']['evidence'] = [key]
        decoded = decode_event_stream({'events': [background], 'open_questions': []}, self.sample)
        self.assertEqual(len(self.compiled(decoded['events'])['items']), 1)

    def test_per_node_comparison_refuses_undefined_global_material_allocation_without_paid_calls(self):
        with self.assertRaisesRegex(ValueError, '公平分配'):
            run_comparison(self.sample, [], Config(endpoint='https://example.invalid/chat/completions', model='fixture'),
                           'verified', self.root / 'compare')
        self.assertFalse((self.root / 'compare').exists())

    def test_verified_http_flow_persists_typed_events_and_rejects_metadata_tampering(self):
        events = [self.event(), self.event('b', 'count', None, item=self.item('浏览次数不去重。', supplement=True))]
        proposed = {'events': events, 'open_questions': []}
        with model_server([(200, completion(proposed)), (200, completion(self.review(events)))]) as (url, requests):
            client = ModelClient(Config(endpoint=url, model='fixture', api_key_env=''))
            result = extract_requirement(self.sample, client)
        self.assertEqual(len(requests), 2)
        self.assertIn('node_id 允许 null', requests[0]['messages'][0]['content'])
        self.assertNotIn('DO_NOT_READ_GOLD', json.dumps(requests))
        p = self.root / 'result.json'
        save_result(p, result)
        validate_saved(read_json(p), self.sample)
        changed = copy.deepcopy(result)
        changed.pop('input_contract')
        changed['result_digest'] = result_digest(changed)
        with self.assertRaises(ValueError):
            validate_saved(changed, self.sample)

    def test_public_cli_reads_supplements_and_replays_the_same_result(self):
        events = [self.event('b', 'count', None, item=self.item('浏览次数不去重。', supplement=True))]
        config, output = self.root / 'model.toml', self.root / 'cli.json'
        with model_server([(200, completion({'events': events, 'open_questions': []})),
                           (200, completion(self.review(events)))]) as (url, requests):
            config.write_text(f'[model]\nendpoint="{url}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            ran = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'extract',
                '--dataset', str(self.root), '--requirement-id', 'REQ-supp', '--config', str(config),
                '--output', str(output)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(len(requests), 2)
        checked = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'validate',
            '--dataset', str(self.root), '--result', str(output)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        saved = read_json(output)
        self.assertEqual(len(saved['input_file_hashes']), 6)
        self.assertEqual(len(saved['nodes']), 2)
        self.assertIsNone(saved['history'][0]['node_id'])
        self.assertEqual(json.loads(checked.stdout)['material_scope'], 'declared_input_contract')
        _, sources, bindings = validated_inputs(self.root, [saved], 'requirement')
        background = [row for row in sources['REQ-supp'] if row['document_type'] == 'supplement']
        self.assertTrue(background)
        self.assertTrue(all(row['node_id'] is None and row['stage'] is None for row in background))
        self.assertEqual(len(bindings[0]['input_file_hashes']), 6)

    def test_legacy_event_validation_and_scoring_keep_the_original_declared_node_pairs(self):
        # Fictional offline legacy fixture: these files were not part of its original input.
        legacy = discover_requirements(self.root, include_supplements=False)[0]
        proposed = {'events': [self.event()], 'open_questions': []}
        with model_server([(200, completion(proposed))]) as (url, requests):
            result = extract_requirement(legacy, ModelClient(Config(endpoint=url, model='fixture', api_key_env='')),
                                         mode='baseline')
        self.assertEqual(len(requests), 1)
        result.pop('input_contract')
        result.pop('event_protocol')
        result['version'] = '0.4.1'
        output = self.root / 'legacy.json'
        save_result(output, result)
        saved = read_json(output)
        checked = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'validate',
            '--dataset', str(self.root), '--result', str(output)], cwd=ROOT, text=True, capture_output=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['material_scope'], 'legacy_declared_node_pairs')
        _, sources, bindings = validated_inputs(self.root, [saved], 'requirement')
        self.assertFalse(any(row['document_type'] == 'supplement' for row in sources['REQ-supp']))
        self.assertEqual(len(bindings[0]['input_file_hashes']), 4)
        with self.assertRaisesRegex(ValueError, '版本|哈希'):
            validate_saved(saved, self.sample)

    def test_background_cannot_restore_invalidated_knowledge(self):
        cancelled = self.event('e2', operation='invalidate', previous='e1')
        cancelled.update(evidence=[self.evidence('门槛10件。')], note='fixture cancellation')
        restored = self.event('b', node=None, operation='update', previous='e2',
                              item=self.item('门槛20件。', supplement=True))
        with self.assertRaisesRegex(ValueError, '恢复已失效'):
            self.compiled([self.event(), cancelled, restored])

    def test_both_audit_protocols_can_publish_an_undated_background_fact_and_replay_it(self):
        node = index_materials('# 需求分析', '# 技术方案', 'analysis', 'N', 'd.md', 'a.md')
        sample = assemble_requirement('REQ-audit', [('N', 'analysis', node)], [('D', 'data.md', '浏览次数不去重。')])
        key = next(key for key, source in sample.catalog.items() if source.document_type == 'supplement')
        ref = {'source_id': key, 'quote': '浏览次数不去重。'}
        event = self.event('b', 'count', None, item={'conclusion': {'text': '浏览次数不去重。', 'evidence': [ref]},
                                                    'reason': UNKNOWN, 'boundary': UNKNOWN})
        fields = [{'knowledge_key': 'count', 'field': field,
                   'status': 'supported' if field == 'conclusion' else 'unstated', 'notes': 'fixture'}
                  for field in ('conclusion', 'reason', 'boundary')]
        for version in ('v1', 'v2'):
            with self.subTest(version=version):
                raw = copy.deepcopy(event)
                reference = ref
                if version == 'v2':
                    raw['item']['conclusion']['evidence'] = [key]
                    reference = key
                proposed = {'events': [raw], 'open_questions': []}
                final = {**proposed, 'review_notes': [], 'grounding_review': {'fields': fields, 'reason_checks': []},
                    'evidence_audit': {'policy': 'evidence-v1', 'evidence_focus': [],
                        'admission_checks': [{'knowledge_key': 'count', 'basis': 'supplement_direct',
                            'direct': [reference], 'proposal': [], 'confirmation': [], 'notes': '背景定义'}],
                        'user_attribution_checks': [], 'coverage_checks': [{
                            'unit_id': 'U001', 'disposition': 'current', 'semantic_coverage': 'full',
                            'targets': [{'type': 'item_field', 'knowledge_key': 'count', 'field': 'conclusion',
                                         'evidence': reference}], 'exclusion_reason': None, 'notes': '完整定义'}]}}
                with model_server([(200, completion(proposed)), (200, completion(final))]) as (url, requests):
                    result = extract_requirement(sample, ModelClient(Config(endpoint=url, model='fixture', api_key_env='')),
                                                 evidence_audit=version, run_dir=self.root / ('audit-' + version))
                self.assertEqual(len(requests), 2)
                self.assertEqual(result['evidence_audit']['audit_status'], 'passed')
                self.assertEqual(result['evidence_audit']['admission_checks'][0]['basis'], 'supplement_direct')
                output = self.root / (version + '.json')
                save_result(output, result, sample=sample)
                validate_saved(read_json(output), sample)
                self.assertTrue(output.with_suffix('.audit.html').is_file())

    def test_supplementary_rationale_gaps_stay_unknown_without_chronological_guessing(self):
        node = index_materials('# 需求分析', '# 技术方案', 'analysis', 'N', 'd.md', 'a.md')
        sample = assemble_requirement('REQ-gap', [('N', 'analysis', node)], [('D', 'gap.md', '浏览次数不去重，原因未说明。')])
        key = next(key for key, source in sample.catalog.items() if source.document_type == 'supplement')
        event = self.event('b', 'count', None, item={
            'conclusion': {'text': '浏览次数不去重。', 'evidence': [{'source_id': key, 'quote': '浏览次数不去重'}]},
            'reason': UNKNOWN, 'boundary': UNKNOWN})
        compiled = compile_memory({'events': [event], 'open_questions': []}, sample)
        proof = {'fields': [{'knowledge_key': 'count', 'field': field, 'notes': 'fixture',
                             'status': 'supported' if field == 'conclusion' else 'unstated'}
                            for field in ('conclusion', 'reason', 'boundary')], 'reason_checks': [{
                'source_id': key, 'quote': '浏览次数不去重，原因未说明。', 'status': 'retained', 'knowledge_key': 'count',
                'unknown_text': '未说明', 'evidence': [{'source_id': key, 'quote': '原因未说明'}], 'notes': '仍未知'}]}
        validate_grounding_review(proof, compiled, sample)

    def test_primary_evidence_can_resolve_undated_gaps_but_undated_evidence_cannot_resolve_formal_ones(self):
        from decision_memory.requirements import resolves_source
        formal = self.key('改为5件。')
        background = self.key('门槛20件。', supplement=True)
        self.assertTrue(resolves_source(self.sample, formal, background))
        self.assertFalse(resolves_source(self.sample, background, formal))

    def test_oversized_supplement_is_rejected_before_any_request(self):
        p = self.root / 'REQ-supp/上下文/口径说明.md'
        p.write_bytes(b'x' * 2_000_001)
        with self.assertRaisesRegex(ValueError, '2 MB'):
            discover_requirements(self.root)
