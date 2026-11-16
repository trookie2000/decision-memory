import copy
import json
import unittest

from decision_memory.battle_protocol import marker, parse_document
from decision_memory.requirement_protocol import parse_requirement, reference_policy, requirement_sample


def packet():
    return {'schema_version': 1, 'requirement_id': 'NEW-1', 'nodes': [
        {'id': 'analysis', 'stage': 'analysis', 'dialogue': '**用户：**\n失败最多尝试 3 次。\n报警范围待确认。',
         'artifact': '失败最多尝试 3 次。'},
        {'id': 'design', 'stage': 'design', 'dialogue': '**用户：**\n改成不重试。\n报警范围仍未确认。',
         'artifact': '不重试。'}]}


def gold():
    return [
        {'id': 'R1', 'kind': 'decision', 'text': '最终不重试。',
         'evidence': [{'node_id': 'design', 'source': 'dialogue', 'quote': '改成不重试'}]},
        {'id': 'R2', 'kind': 'uncertainty', 'text': '报警范围仍待确认。',
         'evidence': [{'node_id': 'design', 'source': 'dialogue', 'quote': '报警范围仍未确认'}]}]


def author_document(identity, done, value=None, references=None):
    result = {**identity, 'status': 'completed'}
    blocks = {'result.json': result, 'requirement.json': value or packet(), 'gold.json': references or gold()}
    return ''.join('## ' + name + '\n\n```json\n' + json.dumps(content, ensure_ascii=False) + '\n```\n\n'
                   for name, content in blocks.items()) + done + '\n'


class RequirementProtocolTests(unittest.TestCase):
    def test_author_return_keeps_all_nodes_and_classified_references(self):
        identity = {'task_id': 'DM-fixture', 'sample_id': 'NEW-1', 'role': 'author', 'round': 1,
                    'input_version': 'fixture-only'}
        done = marker(identity, 'nonce')
        parsed = parse_document(author_document(identity, done), identity, done, 'requirement')
        self.assertEqual(parsed['requirement'], packet())
        self.assertEqual(parsed['references'], gold())
        sample = requirement_sample(parsed['requirement'])
        self.assertEqual(sample.catalog['D.design.00002'].text, '改成不重试。')
        self.assertEqual(sample.catalog['D.analysis.00002'].file, 'nodes/analysis/dialogue.md')
        policy = reference_policy(parsed['references'])
        self.assertEqual([(row['reference_id'], row['kind']) for row in policy['assignments']],
                         [('R1', 'decision'), ('R2', 'uncertainty')])

    def test_reference_must_quote_the_exact_designated_node_and_source(self):
        for node, source, quote in (('analysis', 'dialogue', '改成不重试'),
                                    ('design', 'artifact', '改成不重试'),
                                    ('missing', 'dialogue', '改成不重试'),
                                    ('design', 'dialogue', '改成 不重试')):
            with self.subTest(node=node, source=source, quote=quote), self.assertRaises(ValueError):
                refs = gold()
                refs[0]['evidence'][0] = {'node_id': node, 'source': source, 'quote': quote}
                parse_requirement(packet(), refs, 'NEW-1')

    def test_wrong_identity_extra_answer_fields_and_duplicate_nodes_are_rejected(self):
        for change in ('id', 'extra', 'duplicate', 'empty', 'nontext'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                value = packet()
                if change == 'id':
                    value['requirement_id'] = 'someone-else'
                elif change == 'extra':
                    value['gold'] = gold()
                elif change == 'duplicate':
                    value['nodes'][1]['id'] = 'analysis'
                elif change == 'nontext':
                    value['nodes'][0]['dialogue'] = ['not text']
                else:
                    value['nodes'] = []
                parse_requirement(value, gold(), 'NEW-1')

    def test_unknown_classification_and_duplicate_reference_ids_are_rejected(self):
        for change in ('kind', 'id', 'empty'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                refs = gold()
                if change == 'kind':
                    refs[1]['kind'] = 'maybe'
                elif change == 'id':
                    refs[1]['id'] = 'R1'
                else:
                    refs = []
                parse_requirement(packet(), refs, 'NEW-1')

    def test_scorer_protocol_can_return_two_v2_judgment_sets_without_prediction_echo(self):
        identity = {'task_id': 'DM-test', 'sample_id': 'NEW-1', 'role': 'scorer', 'round': 1,
                    'input_version': 'v', 'prediction_digest': 'bundle-digest'}
        done = marker(identity, 'nonce')
        values = {'A': {'judgments': [], 'open_question_judgments': [], 'reference_judgments': []},
                  'B': {'judgments': [], 'open_question_judgments': [], 'reference_judgments': []}}
        text = ('## result.json\n```json\n' + json.dumps({**identity, 'status': 'completed'}) + '\n```\n'
                '## judgments.json\n```json\n' + json.dumps(values) + '\n```\n' + done)
        self.assertEqual(parse_document(text, identity, done, 'requirement')['judgments'], values)


if __name__ == '__main__':
    unittest.main()
