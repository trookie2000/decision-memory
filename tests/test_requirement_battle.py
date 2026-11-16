"""Fixtures verify orchestration and binding, never real employee quality."""
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.battle_protocol import sections
from decision_memory.collaboration import BattleConfig
from decision_memory.coordinator import Battle
from decision_memory.storage import read_json
from http_fixture import completion, model_server
from test_coordinator import FixtureDocs, FixtureMario
from test_requirement_comparison import replies
from test_requirement_protocol import author_document, gold, packet


class RequirementMario(FixtureMario):
    def __init__(self, docs, revision_rounds=0, disputed=None):
        super().__init__(docs, revision_rounds)
        self.disputed = disputed

    def payload(self, identity):
        for content in self.docs.contents.values():
            blocks = sections(content)
            if 'task.json' in blocks:
                task = json.loads(blocks['task.json'])
                if task['identity'] == identity:
                    return task['payload']
        raise AssertionError('fixture did not receive the bound task packet')

    def finish_last(self):
        sent = self.sent[-1]
        identity = sent['identity']
        metadata = {**identity, 'task_input_digest': sent['task_input_digest']}
        if identity['role'] == 'author':
            value = packet()
            value['requirement_id'] = identity['sample_id']
            refs = gold()
            refs[0]['text'] = 'GOLD_ONLY_SENTINEL 最终不重试'
            text = author_document(metadata, sent['marker'], value, refs)
        else:
            metadata['status'] = 'completed'
            result = None
            if identity['role'] == 'reviewer':
                metadata['status'] = ('human_review' if identity['sample_id'] == self.disputed else
                                      ('needs_revision' if identity['round'] <= self.revision_rounds else 'passed'))
                metadata['feedback'] = '离线协议 fixture，非真实质量评审。'
            else:
                result = {}
                payload = self.payload(identity)
                for label, view in payload['prediction']['variants'].items():
                    rows, supporters = [], []
                    for item in view['items']:
                        correct = '3 次' not in item['conclusion']['text']
                        rows.append({'item_id': item['id'], 'correct': correct, 'citation_support': True,
                                     'matches': ['R1'] if correct else [], 'notes': '离线评分 fixture。'})
                        if correct:
                            supporters.append({'target': 'item', 'id': item['id'], 'field': 'conclusion'})
                    questions = [{'question_id': f'Q{i:03d}', 'correct': True, 'citation_support': True,
                                  'matches': ['R2'], 'notes': '离线未知信息 fixture。'}
                                 for i in range(1, len(view['open_questions']) + 1)]
                    result[label] = {'judgments': rows, 'open_question_judgments': questions,
                                     'reference_judgments': [
                                         {'reference_id': 'R1', 'coverage': 'full', 'supported_by': supporters,
                                          'notes': '离线引用绑定 fixture。'},
                                         {'reference_id': 'R2', 'coverage': 'full' if questions else 'absent',
                                          'supported_by': [{'target': 'open_question', 'id': 'Q001'}] if questions else [],
                                          'notes': '离线未知覆盖 fixture。'}]}
            text = '## result.json\n```json\n' + json.dumps(metadata, ensure_ascii=False) + '\n```\n'
            if result is not None:
                text += '## judgments.json\n```json\n' + json.dumps(result, ensure_ascii=False) + '\n```\n'
            text += sent['marker']
        self.docs.contents[sent['doc_id']] = text


class RequirementBattleTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.folder = Path(self.temp.name)
        self.docs = FixtureDocs()
        self.mario = RequirementMario(self.docs, revision_rounds=1)
        self.config = BattleConfig(worker_timeout=30, poll_interval=1)
        self.model = self.folder / 'model.toml'
        event = patch.object(Battle, 'event')
        event.start()
        self.addCleanup(event.stop)

    def deps(self):
        return {'docs': self.docs, 'mario_factory': lambda config: self.mario}

    def drive(self, battle):
        for _ in range(22):
            battle.run(0)
            if battle.state['status'] not in ('running', 'waiting'):
                break
        return battle

    def test_requirement_revisions_real_cli_comparison_and_v2_score(self):
        with model_server([(200, completion(value)) for value in replies()]) as (endpoint, requests):
            self.model.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = Battle.create(self.folder / 'battle', self.config, 'NEW-1', 'requirement', 'fictional fixture',
                                   model_config=self.model, **self.deps())
            self.drive(battle)
            self.assertEqual(battle.state['status'], 'agent_review_complete', battle.summary())
            self.assertEqual(battle.state['round'], 2)
            self.assertEqual(len(requests), 3)
            self.assertNotIn('GOLD_ONLY_SENTINEL', json.dumps(requests))
            self.assertEqual(len(self.mario.sent), 5)
            score = battle.state['score']
            self.assertTrue(score['complete'])
            self.assertEqual(set(score['methods'].values()), {'whole', 'per_node'})
            self.assertEqual(battle.audit()['status'], 'agent_review_complete')
            Battle(battle.root, **self.deps()).run(0)
            self.assertEqual(len(requests), 3)
            self.assertEqual(len(self.mario.sent), 5)
        self.assertTrue((battle.input_path('manifest.json')).exists())
        self.assertEqual(read_json(battle.path('scoring/reference-policy.json'))['assignments'][1]['kind'], 'uncertainty')

    def test_direct_battle_resume_cannot_bypass_missing_batch_freeze(self):
        self.mario.revision_rounds = 0
        with model_server([]) as (endpoint, requests):
            self.model.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = Battle.create(self.folder / 'batch' / 'cases' / 'NEW-1', self.config,
                                   'NEW-1', 'requirement', 'fixture',
                                   batch_root=self.folder / 'batch', **self.deps())
            self.drive(battle)
            self.assertEqual(battle.state['status'], 'awaiting_model')
            with self.assertRaisesRegex(ValueError, '批次.*冻结'):
                Battle(battle.root, **self.deps()).run(0, model_config=self.model)
            self.assertEqual(requests, [])

    def test_audit_never_accepts_a_score_without_a_real_prediction(self):
        self.mario.revision_rounds = 0
        battle = Battle.create(self.folder / 'battle', self.config, 'NEW-1', 'requirement', 'fixture', **self.deps())
        self.drive(battle)
        self.assertEqual(battle.state['status'], 'awaiting_model')
        battle.state['score'] = {'complete': True}
        battle.save()
        with self.assertRaisesRegex(ValueError, '预测|评分'):
            battle.audit()


if __name__ == '__main__':
    unittest.main()
