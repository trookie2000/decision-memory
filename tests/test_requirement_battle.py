"""Offline end-to-end check of a multi-node (requirement) battle case; fixtures are not live results."""

import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.battle_protocol import worker_message
from decision_memory.collaboration import BattleConfig
from decision_memory.coordinator import Battle
from decision_memory.storage import read_json
from http_fixture import completion, model_server
from test_coordinator import FixtureDocs, FixtureMario, document_fixture

NODES = {
    '需求确认记录.md': '**AGENT：**\n门槛 50 元？\n\n**用户：**\n门槛改成 120 元。\n',
    '需求分析.md': '- 门槛 120 元，可配置。\n',
    '技术设计确认记录.md': '**用户：**\n候选不能删，失败走原降级。\n',
    '技术方案.md': '- 下游失败时保留候选并降级。\n',
}
GOLD = [{'id': 'R1', 'text': 'GOLD_ONLY_SENTINEL 门槛 120 元', 'evidence': [
            {'node': 1, 'source': 'dialogue', 'quote': '门槛改成 120 元'}]},
        {'id': 'R2', 'text': '失败保留候选', 'evidence': [
            {'node': 2, 'source': 'artifact', 'quote': '下游失败时保留候选并降级'}]}]


def requirement_author_document(identity, done):
    metadata = {**identity, 'status': 'completed'}
    text = '## result.json\n\n```json\n' + json.dumps(metadata, ensure_ascii=False) + '\n```\n'
    for name, body in NODES.items():
        text += f'## {name}\n\n```markdown\n{body}```\n'
    text += '## gold.json\n\n```json\n' + json.dumps(GOLD, ensure_ascii=False) + '\n```\n'
    return text + '\n' + done + '\n'


class RequirementMario(FixtureMario):
    def finish_last(self):
        sent = self.sent[-1]
        identity = sent['identity']
        if identity['role'] != 'author':
            return super().finish_last()
        output = {**identity, 'task_input_digest': sent['task_input_digest']} if sent.get('task_input_digest') else identity
        self.docs.contents[sent['doc_id']] = requirement_author_document(output, sent['marker'])


def model_answer():
    # One item citing both nodes (the fixture scorer judges exactly one item, K001).
    return {'items': [
        {'kind': 'change', 'conclusion': {'text': '门槛由 50 元调整为 120 元', 'evidence': [
            {'source_id': 'D1-00005', 'quote': '门槛改成 120 元'}, {'source_id': 'A1-00001', 'quote': '门槛 120 元'}]},
         'reason': {'text': '用户在需求确认中改为 120 元', 'evidence': [{'source_id': 'D1-00005', 'quote': '改成 120 元'}]},
         'boundary': {'text': '技术设计阶段要求下游失败时保留候选', 'evidence': [
             {'source_id': 'D2-00002', 'quote': '候选不能删'}]}}], 'open_questions': []}


class RequirementBattleTests(unittest.TestCase):
    def setUp(self):
        events = patch.object(Battle, 'event')
        events.start()
        self.addCleanup(events.stop)
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'battle'
        self.docs = FixtureDocs()
        self.mario = RequirementMario(self.docs)
        self.config = BattleConfig(worker_timeout=30, poll_interval=1, notification_max_bytes=60000)

    def test_multi_node_case_freezes_official_layout_and_cli_answers_by_requirement(self):
        model_config = Path(self.temp.name) / 'model.toml'
        with model_server([(200, completion(model_answer()))]) as (endpoint, requests):
            model_config.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            battle = Battle.create(self.root, self.config, 'G900', 'requirement', 'fixture brief',
                                   model_config=model_config, docs=self.docs, mario_factory=lambda config: self.mario)
            for _ in range(15):
                battle.run(0)
                if battle.state['status'] not in ('running', 'waiting'):
                    break
        self.assertEqual(battle.state['status'], 'agent_review_complete', battle.state.get('last_error'))
        for folder, name in (('上下文', '需求确认记录.md'), ('产物', '需求分析.md'),
                             ('上下文', '技术设计确认记录.md'), ('产物', '技术方案.md')):
            self.assertEqual((self.root / 'inputs/G900' / folder / name).read_text(encoding='utf-8'), NODES[name])
        prompt = json.dumps(requests, ensure_ascii=False)
        self.assertNotIn('GOLD_ONLY_SENTINEL', prompt)
        self.assertIn('D2-00002', prompt)
        prediction = read_json(self.root / battle.state['prediction'])
        self.assertEqual((prediction['sample_id'], prediction['stage']), ('G900', 'requirement'))
        reviewer_payload = read_json(self.root / 'operations/r01-reviewer/payload.json')
        self.assertEqual([node['stage'] for node in reviewer_payload['nodes']], ['analysis', 'design'])
        scorer_payload = read_json(self.root / 'operations/score-a01/payload.json')
        self.assertEqual(scorer_payload['nodes'][1]['artifact'], NODES['技术方案.md'])
        self.assertTrue(battle.audit()['model_prediction_available'])

    def test_author_message_asks_for_two_nodes_and_node_tagged_gold(self):
        identity = {'task_id': 'T', 'sample_id': 'G900', 'role': 'author', 'round': 1, 'input_version': 'v'}
        message = worker_message(identity, 'DM_DONE|x', 'https://example', 'requirement', {'brief': 'b'})
        for block in ('需求确认记录.md', '需求分析.md', '技术设计确认记录.md', '技术方案.md', 'gold.json', '"node"'):
            self.assertIn(block, message)


if __name__ == '__main__':
    unittest.main()
