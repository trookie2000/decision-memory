"""The blind answering request cannot borrow the candidate's unsupported fields."""
import copy
import json
import subprocess
import sys
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient, ModelError
from decision_memory.field_verification import (FIELDS, UNKNOWN, project, validate_answers,
    validate_questions, verify_fields)
from decision_memory.official_eval import validated_inputs
from decision_memory.pipeline import extract
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, result_digest, save_result, validate_saved
from http_fixture import completion, model_server
from test_optimizations import FakeClient

ROOT = Path(__file__).resolve().parents[1]


class IndependentFieldTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name)
        self.dialogue = ('**用户：**\n门槛最终为25件。\n降低门槛因为80件过高；25件的具体依据未说明。\n'
                         '仅灰度入口展示，老链路不变。\n**Agent：**\n可能降低数据库压力。')
        self.artifact = '门槛最终为25件。'
        self.sample = index_materials(self.dialogue, self.artifact, 'analysis', 'FIXTURE', 'dialogue.md', 'artifact.md')
        self.original = {'conclusion': self.field('门槛最终为25件。', 'A00001'),
                         'reason': self.field('幻觉理由污染：因为跨省计费补贴。', 'D00006'),
                         'boundary': self.field('全站适用。', 'D00004')}
        self.base = extract(self.sample, FakeClient([{'items': [self.original], 'open_questions': []}]), 'baseline')
        self.base['result_digest'] = result_digest(self.base)
        self.questions = [{'item_id': 'K001', 'field': field, 'questions': [question]}
            for field, question in zip(FIELDS, ('门槛最终取值是什么？', '门槛调整及取值依据是什么？', '适用范围与例外是什么？'))]
        self.answers = validate_answers({'answers': [
            {'item_id': 'K001', 'field': 'conclusion', 'status': 'answered', **self.field('门槛最终为25件。', 'A00001')},
            {'item_id': 'K001', 'field': 'reason', 'status': 'answered',
             **self.field('原门槛过高所以降低，25件的具体依据未说明。', 'D00003')},
            {'item_id': 'K001', 'field': 'boundary', 'status': 'answered',
             **self.field('仅灰度入口展示，老链路不变。', 'D00004')} ]}, self.base, self.sample)
        self.checks = [{'item_id': 'K001', 'field': name,
                        'original_support': 'supported' if name == 'conclusion' else 'insufficient',
                        'action': 'keep' if name == 'conclusion' else 'repair',
                        'value': self.base['items'][0][name] if name == 'conclusion' else
                                 {key: self.answers[index][key] for key in ('text', 'evidence')},
                        'notes': 'fixture source-bound comparison'} for index, name in enumerate(FIELDS)]

    def field(self, text, key):
        return {'text': text, 'evidence': [{'source_id': key, 'quote': self.sample.catalog[key].text}]}

    def project(self, checks=None, answers=None):
        return project(self.base, answers or self.answers, {'field_checks': checks or self.checks}, self.sample)

    def responses(self):
        return [(200, completion(value)) for value in ({'questions': self.questions},
                {'answers': self.answers}, {'field_checks': self.checks})]

    def test_repair_uses_independently_answered_fields_and_retains_partial_unknown(self):
        projection, checks = self.project()
        self.assertIn('具体依据未说明', projection['items'][0]['reason']['text'])
        self.assertEqual(projection['items'][0]['boundary']['text'], self.answers[2]['text'])
        self.assertEqual(projection['item_mapping'], [{'item_id': 'K001', 'original_item_id': 'K001'}])
        self.assertEqual(checks[1]['original_support'], 'insufficient')

    def test_cross_field_evidence_is_rejected_even_when_the_quote_exists(self):
        changed = copy.deepcopy(self.checks)
        changed[1]['value']['evidence'] = self.answers[2]['evidence']
        with self.assertRaisesRegex(ValueError, '自身独立回答'):
            self.project(changed)

    def test_final_text_cannot_invent_a_new_reason_during_comparison(self):
        changed = copy.deepcopy(self.checks)
        changed[1]['value']['text'] = '为了提升性能。'
        with self.assertRaisesRegex(ValueError, '逐字采用独立回答'):
            self.project(changed)

    def test_unsupported_reason_can_abstain_without_creating_a_business_open_question(self):
        changed = copy.deepcopy(self.checks)
        changed[1].update(action='abstain', value=UNKNOWN)
        answers = copy.deepcopy(self.answers)
        answers[1].update(status='unstated', **UNKNOWN)
        projection, _ = self.project(changed, answers)
        self.assertEqual(projection['items'][0]['reason'], UNKNOWN)
        self.assertEqual(projection['open_questions'], self.base['open_questions'])

    def test_available_partial_explanation_cannot_be_erased_by_abstention(self):
        checks = copy.deepcopy(self.checks)
        checks[1].update(action='abstain', value=UNKNOWN)
        with self.assertRaisesRegex(ValueError, '不受支持的理由'):
            self.project(checks)

    def test_independent_answer_cannot_drop_an_explicit_remaining_reason_gap(self):
        answers = copy.deepcopy(self.answers)
        answers[1]['text'] = '因为原门槛过高。'
        with self.assertRaisesRegex(ValueError, '保留剩余未知'):
            validate_answers({'answers': answers}, self.base, self.sample)

    def test_unsupported_boundary_cannot_be_deleted_to_broaden_the_rule(self):
        changed = copy.deepcopy(self.checks)
        changed[2].update(action='abstain', value=UNKNOWN)
        with self.assertRaisesRegex(ValueError, '边界'):
            self.project(changed)

    def test_uncertain_boundary_holds_the_whole_item_and_preserves_the_original(self):
        changed = copy.deepcopy(self.checks)
        changed[2].update(action='hold', value=None)
        projection, _ = self.project(changed)
        self.assertEqual(projection['items'], [])
        self.assertEqual(projection['field_review_queue'][0]['original'], self.base['items'][0])

    def test_conclusion_cannot_be_changed_under_the_name_of_repair(self):
        changed = copy.deepcopy(self.checks)
        changed[0].update(action='repair')
        with self.assertRaisesRegex(ValueError, '理由或边界'):
            self.project(changed)

    def test_confirmed_text_with_wrong_evidence_can_rebind_without_rewriting_text(self):
        changed = copy.deepcopy(self.checks)
        changed[0].update(action='rebind', original_support='insufficient')
        projection, _ = self.project(changed)
        self.assertEqual(projection['items'][0]['conclusion']['text'], self.base['items'][0]['conclusion']['text'])

    def test_agent_only_answer_cannot_be_published_as_confirmed_reason(self):
        answers, checks = copy.deepcopy(self.answers), copy.deepcopy(self.checks)
        answers[1].update(**self.field('可能降低数据库压力。', 'D00006'))
        checks[1]['value'] = {key: answers[1][key] for key in ('text', 'evidence')}
        with self.assertRaisesRegex(ValueError, 'Agent提议'):
            self.project(checks, answers)

    def test_user_attribution_cannot_be_supplied_by_an_artifact(self):
        answers, checks = copy.deepcopy(self.answers), copy.deepcopy(self.checks)
        answers[1].update(**self.field('用户确认门槛。', 'A00001'))
        checks[1]['value'] = {key: answers[1][key] for key in ('text', 'evidence')}
        with self.assertRaisesRegex(ValueError, '正式用户'):
            self.project(checks, answers)

    def test_original_support_cannot_say_unstated_for_a_real_assertion(self):
        checks = copy.deepcopy(self.checks)
        checks[1]['original_support'] = 'unstated'
        with self.assertRaisesRegex(ValueError, '原字段'):
            self.project(checks)

    def test_missing_duplicate_or_unknown_question_groups_are_rejected(self):
        for rows in (self.questions[:-1], [self.questions[0]] * 3,
                     [{**self.questions[0], 'item_id': []}, *self.questions[1:]]):
            with self.subTest(rows=rows), self.assertRaises(ValueError):
                validate_questions({'questions': rows}, self.base)

    def test_leading_yes_no_question_is_rejected(self):
        questions = copy.deepcopy(self.questions)
        questions[0]['questions'] = ['是否应为25件？']
        with self.assertRaisesRegex(ValueError, '开放问题'):
            validate_questions({'questions': questions}, self.base)

    def test_conflict_requires_two_real_references(self):
        answers = copy.deepcopy(self.answers)
        answers[2]['status'] = 'conflict'
        with self.assertRaisesRegex(ValueError, '双方证据'):
            validate_answers({'answers': answers}, self.base, self.sample)

    def test_unconfirmed_source_answer_cannot_publish_a_field(self):
        answers = copy.deepcopy(self.answers)
        answers[1]['status'] = 'unconfirmed'
        with self.assertRaisesRegex(ValueError, '自身独立回答'):
            self.project(answers=answers)

    def test_http_flow_uses_three_separate_requests_and_blind_answering_input(self):
        with model_server(self.responses()) as (url, requests):
            client = ModelClient(Config(endpoint=url, model='fixture', api_key_env='', retries=2))
            result = verify_fields(self.base, self.sample, client, self.root / 'run')
        self.assertEqual(len(requests), 3)
        blind = requests[1]['messages'][1]['content']
        self.assertNotIn('幻觉理由污染', blind)
        self.assertNotIn('原字段', blind)
        self.assertNotIn('field_checks', blind)
        self.assertIn('门槛最终为25件', blind)
        self.assertEqual(client.config.retries, 2)
        self.assertEqual(result['base_result'], self.base)
        save_result(self.root / 'final.json', result, sample=self.sample)
        validate_saved(read_json(self.root / 'final.json'), self.sample)
        with self.assertRaisesRegex(ValueError, '模式'):
            validate_saved(result, self.sample, client, 'verified')
        self.assertIn('独立字段修订', (self.root / 'final.md').read_text())

    def test_recomputed_digest_cannot_hide_a_projection_or_proof_change(self):
        with model_server(self.responses()) as (url, _):
            result = verify_fields(self.base, self.sample, ModelClient(Config(endpoint=url, model='fixture', api_key_env='')),
                                   self.root / 'run')
        for target in ('projection', 'answer', 'base', 'queue', 'metadata'):
            with self.subTest(target=target), self.assertRaises(ValueError):
                changed = copy.deepcopy(result)
                if target == 'projection':
                    changed['items'][0]['boundary']['text'] = '全站适用。'
                elif target == 'answer':
                    changed['verification']['answers'][1]['evidence'][0]['quote'] = '不存在的引文'
                elif target == 'base':
                    changed['base_result']['items'][0]['reason']['text'] = 'changed'
                elif target == 'queue':
                    changed['field_review_queue'] = [{'hidden': True}]
                else:
                    changed['checks']['human_review'] = 'passed'
                changed['result_digest'] = result_digest(changed)
                validate_saved(changed, self.sample)

    def test_event_result_is_projected_without_rewriting_its_history(self):
        from decision_memory.requirements import assemble_requirement
        from decision_memory.requirement_pipeline import extract_requirement
        sample = assemble_requirement('FIXTURE', [('N', 'analysis', self.sample)])
        mapping = {key: key[0] + '.N.' + key[1:] for key in self.sample.catalog}
        def convert(value):
            text = json.dumps(value)
            for old, new in mapping.items():
                text = text.replace(old, new)
            return json.loads(text)
        event = {'id': 'e1', 'knowledge_key': 'threshold', 'node_id': 'N', 'operation': 'add', 'previous': None,
                 'item': convert(self.original)}
        base = extract_requirement(sample, FakeClient([{'events': [event], 'open_questions': []}]), 'baseline')
        save_result(self.root / 'base.json', base)
        responses = [(status, convert(value)) for status, value in self.responses()]
        with model_server(responses) as (url, _):
            result = verify_fields(base, sample, ModelClient(Config(endpoint=url, model='fixture', api_key_env='')),
                                   self.root / 'run')
        self.assertEqual(result['base_result']['history'], base['history'])
        self.assertNotIn('history', result)
        save_result(self.root / 'final.json', result, sample=sample)
        validate_saved(read_json(self.root / 'final.json'), sample)
        for key in ('nodes', 'requirement_id'):
            changed = copy.deepcopy(result)
            changed[key] = []
            changed['result_digest'] = result_digest(changed)
            with self.assertRaises(ValueError):
                validate_saved(changed, sample)

    def test_keep_preserves_existing_unstated_normalization_metadata(self):
        base = copy.deepcopy(self.base)
        original = {'text': '未说明', 'evidence': [],
                    'protocol_normalization': {'rule': 'unstated-punctuation-v1', 'original_text': '未说明。'}}
        base['items'][0]['reason'] = original
        answers, checks = copy.deepcopy(self.answers), copy.deepcopy(self.checks)
        answers[1].update(status='unstated', **UNKNOWN)
        checks[1].update(action='keep', original_support='unstated', value=UNKNOWN)
        projection, _ = project(base, answers, {'field_checks': checks}, self.sample)
        self.assertEqual(projection['items'][0]['reason'], original)

    def test_rebinding_cannot_drop_the_implementation_symbol_from_the_actual_quote(self):
        sample = index_materials('正式记录', '刷新调用useCacheCache；保持旧链路。', 'analysis', 'SYMBOL', 'd.md', 'a.md')
        value = {'conclusion': {'text': '刷新调用useCacheCache。', 'evidence': [
            {'source_id': 'A00001', 'quote': sample.catalog['A00001'].text}]}, 'reason': UNKNOWN, 'boundary': UNKNOWN}
        base = extract(sample, FakeClient([{'items': [value], 'open_questions': []}]), 'baseline')
        answers = [{'item_id': 'K001', 'field': field, 'status': 'answered' if field == 'conclusion' else 'unstated',
                    **base['items'][0][field]} for field in FIELDS]
        checks = [{'item_id': 'K001', 'field': field, 'original_support': 'supported' if field == 'conclusion' else 'unstated',
                   'action': 'keep', 'value': base['items'][0][field], 'notes': 'fixture'} for field in FIELDS]
        checks[0].update(action='rebind', original_support='insufficient', value={
            'text': value['conclusion']['text'], 'evidence': [{'source_id': 'A00001', 'quote': '保持旧链路'}]})
        with self.assertRaisesRegex(ValueError, '实现标识符'):
            project(base, answers, {'field_checks': checks}, sample)

    def test_transport_error_is_not_automatically_reissued_and_run_is_preserved(self):
        with model_server([(503, {'error': {'message': 'fixture'}})]) as (url, requests):
            client = ModelClient(Config(endpoint=url, model='fixture', api_key_env='', retries=2))
            with self.assertRaises(ModelError):
                verify_fields(self.base, self.sample, client, self.root / 'run')
        self.assertEqual(len(requests), 1)
        self.assertEqual(read_json(self.root / 'run/state.json')['status'], 'failed')
        with self.assertRaisesRegex(ValueError, '已存在'):
            verify_fields(self.base, self.sample, ModelClient(client.config), self.root / 'run')

    def test_invalid_semantic_response_is_saved_without_a_repair_that_can_erase_hold(self):
        checks = copy.deepcopy(self.checks)
        checks[2].update(action='hold', value=None)
        checks.pop(0)
        responses = self.responses()
        responses[-1] = (200, completion({'field_checks': checks}))
        with model_server(responses) as (url, requests):
            with self.assertRaises(ValueError):
                verify_fields(self.base, self.sample, ModelClient(Config(endpoint=url, model='fixture', api_key_env='')),
                              self.root / 'run')
        self.assertEqual(len(requests), 3)
        self.assertEqual(read_json(self.root / 'run/draft.json')['payload']['field_checks'][1]['action'], 'hold')

    def test_cli_can_publish_replay_and_supply_official_scoring_sources(self):
        folder = self.root / 'dataset/REQ/上下文'
        folder.mkdir(parents=True)
        (folder / '需求确认记录.md').write_text(self.dialogue)
        artifacts = self.root / 'dataset/REQ/产物'
        artifacts.mkdir()
        (artifacts / '需求分析.md').write_text(self.artifact)
        from decision_memory.sources import discover_requirements
        sample = discover_requirements(self.root / 'dataset')[0]
        mapping = {'A00001': 'A1-00001', 'D00003': 'D1-00003', 'D00004': 'D1-00004', 'D00006': 'D1-00006'}
        original = json.loads(json.dumps(self.original))
        for field in original.values():
            for ref in field['evidence']:
                ref['source_id'] = mapping[ref['source_id']]
        base = extract(sample, FakeClient([{'items': [original], 'open_questions': []}]), 'baseline')
        save_result(self.root / 'base.json', base)
        responses = []
        for status, value in self.responses():
            text = json.dumps(value)
            for old, new in mapping.items():
                text = text.replace(old, new)
            responses.append((status, json.loads(text)))
        with model_server(responses) as (url, requests):
            (self.root / 'model.toml').write_text(f'[model]\nendpoint="{url}"\nmodel="fixture"\napi_key_env=""\n')
            ran = subprocess.run([sys.executable, '-m', 'decision_memory', 'field', 'verify', '--dataset',
                str(self.root / 'dataset'), '--result', str(self.root / 'base.json'), '--config',
                str(self.root / 'model.toml'), '--output', str(self.root / 'final.json')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(ran.returncode, 0, ran.stderr)
        self.assertEqual(len(requests), 3)
        final = read_json(self.root / 'final.json')
        _, sources, _ = validated_inputs(self.root / 'dataset', [final], 'requirement')
        self.assertTrue(sources['REQ'])
        checked = subprocess.run([sys.executable, '-m', 'decision_memory', 'requirement', 'validate', '--dataset',
            str(self.root / 'dataset'), '--result', str(self.root / 'final.json')], cwd=ROOT, capture_output=True, text=True)
        self.assertEqual(checked.returncode, 0, checked.stderr)
        self.assertEqual(json.loads(checked.stdout)['snapshot_replay'], 'passed')
