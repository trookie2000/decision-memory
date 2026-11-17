"""Exercise knowledge updates against a hand-written, multi-node requirement."""
import copy
import unittest

from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials
from decision_memory.requirement_memory import compile_memory


UNSTATED = {'text': '未说明', 'evidence': []}


def requirement():
    rows = [
        ('N1', 'analysis', '**用户：**\n失败最多尝试 3 次。\n报警范围待确认。', '失败最多尝试 3 次。'),
        ('N2', 'design', '**用户：**\n主链路不重试，失败立即返回。\n旁路请求仍可重试。', '主链路不重试。'),
        ('N3', 'coding', '**用户：**\n保持不重试，报警仍保留。', '已实现不重试。')]
    return assemble_requirement('DEMO-RETRY', [
        (node, stage, index_materials(dialogue, artifact, stage, node, node + '-dialogue.md', node + '-artifact.md'))
        for node, stage, dialogue, artifact in rows])


def evidence(source='D.N1.00002', quote='失败最多尝试 3 次'):
    return [{'source_id': source, 'quote': quote}]


def item(text='失败最多尝试 3 次', source='D.N1.00002', quote='失败最多尝试 3 次'):
    return {'kind': 'decision', 'conclusion': {'text': text, 'evidence': evidence(source, quote)},
            'reason': copy.deepcopy(UNSTATED), 'boundary': copy.deepcopy(UNSTATED)}


def added():
    return {'id': 'E1', 'knowledge_key': 'main-retry', 'node_id': 'N1', 'operation': 'add',
            'previous': None, 'item': item()}


def updated():
    return {'id': 'E2', 'knowledge_key': 'main-retry', 'node_id': 'N2', 'operation': 'update',
            'previous': 'E1', 'item': item('主链路不重试', 'D.N2.00002', '主链路不重试')}


class RequirementMemoryTests(unittest.TestCase):
    def compile(self, events, questions=None):
        return compile_memory({'events': events, 'open_questions': questions or []}, requirement())

    def test_update_replaces_current_knowledge_and_keeps_superseded_version(self):
        result = self.compile([added(), updated()])
        self.assertEqual([entry['conclusion']['text'] for entry in result['items']], ['主链路不重试'])
        self.assertEqual(result['history'][0]['status'], 'superseded')
        self.assertEqual(result['history'][0]['item']['conclusion']['text'], '失败最多尝试 3 次')
        self.assertEqual(result['memory_index'][0]['version'], 2)
        self.assertEqual(result['relations'], [{'from': 'E2', 'to': 'E1', 'type': 'updates'}])

    def test_unstated_normalization_survives_events_history_and_replay(self):
        first = added()
        first['item']['reason'] = {'text': '未说明。', 'evidence': []}
        result = self.compile([first])
        wanted = {'text': '未说明', 'evidence': [], 'protocol_normalization': {
            'rule': 'unstated-punctuation-v1', 'original_text': '未说明。'}}
        self.assertEqual(result['items'][0]['reason'], wanted)
        self.assertEqual(result['events'][0]['item']['reason'], wanted)
        self.assertEqual(result['history'][0]['item']['reason'], wanted)
        replayed = self.compile(result['events'], result['open_questions'])
        self.assertEqual(replayed, result)

    def test_an_earlier_artifact_can_be_invalidated(self):
        first = added()
        first['item'] = item(source='A.N1.00001')
        invalidation = {'id': 'E2', 'knowledge_key': 'main-retry', 'node_id': 'N2',
                        'operation': 'invalidate', 'previous': 'E1',
                        'evidence': evidence('D.N2.00002', '主链路不重试'), 'note': '新节点取消原重试策略。'}
        result = self.compile([first, invalidation])
        self.assertEqual(result['items'], [])
        self.assertEqual(result['history'][0]['status'], 'invalidated')
        self.assertEqual(result['relations'][0]['type'], 'invalidates')
        self.assertEqual(result['history'][1]['note'], '新节点取消原重试策略。')

    def test_merge_adds_evidence_without_changing_meaning_or_version(self):
        merge = {'id': 'E3', 'knowledge_key': 'main-retry', 'node_id': 'N3', 'operation': 'merge',
                 'previous': 'E2', 'evidence': {'conclusion': evidence('D.N3.00002', '不重试')}}
        result = self.compile([added(), updated(), merge])
        self.assertEqual(len(result['items']), 1)
        self.assertEqual(result['items'][0]['conclusion']['text'], '主链路不重试')
        self.assertEqual([e['source_id'] for e in result['items'][0]['conclusion']['evidence']],
                         ['D.N2.00002', 'D.N3.00002'])
        self.assertEqual(result['memory_index'][0]['version'], 2)
        self.assertEqual(result['memory_index'][0]['event_id'], 'E3')
        self.assertEqual(result['history'][1]['status'], 'supporting')

    def test_separate_scopes_coexist_and_pending_questions_stay_separate(self):
        side = {'id': 'E3', 'knowledge_key': 'side-retry', 'node_id': 'N2', 'operation': 'add',
                'previous': None, 'item': item('旁路请求仍可重试', 'D.N2.00003', '旁路请求仍可重试')}
        question = {'text': '报警范围待确认', 'evidence': evidence('D.N1.00003', '报警范围待确认')}
        result = self.compile([added(), updated(), side], [question])
        self.assertEqual([row['conclusion']['text'] for row in result['items']], ['主链路不重试', '旁路请求仍可重试'])
        self.assertEqual(result['open_questions'][0]['text'], '报警范围待确认')

    def test_wrong_predecessors_keys_and_duplicate_event_ids_fail(self):
        for change in ('previous', 'key', 'duplicate', 'second_add'):
            with self.subTest(change=change), self.assertRaises(ValueError):
                first, second = added(), updated()
                if change == 'previous':
                    second['previous'] = 'missing'
                elif change == 'key':
                    second['knowledge_key'] = 'unrelated-scope'
                elif change == 'duplicate':
                    second['id'] = 'E1'
                else:
                    second['operation'], second['previous'] = 'add', None
                self.compile([first, second])

    def test_future_evidence_and_backward_node_order_fail(self):
        first = added()
        first['item'] = item('主链路不重试', 'D.N2.00002', '主链路不重试')
        with self.assertRaises(ValueError):
            self.compile([first])
        with self.assertRaises(ValueError):
            self.compile([updated(), added()])

    def test_merge_cannot_smuggle_new_text_or_unknown_reason_evidence(self):
        merge = {'id': 'E3', 'knowledge_key': 'main-retry', 'node_id': 'N3', 'operation': 'merge',
                 'previous': 'E2', 'evidence': {'conclusion': evidence('D.N3.00002', '不重试')},
                 'item': item('伪造的策略')}
        with self.assertRaises(ValueError):
            self.compile([added(), updated(), merge])
        merge.pop('item')
        merge['evidence']['reason'] = evidence('D.N3.00002', '不重试')
        with self.assertRaises(ValueError):
            self.compile([added(), updated(), merge])

    def test_latest_merge_is_the_predecessor_and_inactive_memory_needs_update(self):
        merge = {'id': 'E3', 'knowledge_key': 'main-retry', 'node_id': 'N3', 'operation': 'merge',
                 'previous': 'E2', 'evidence': {'conclusion': evidence('D.N3.00002', '不重试')}}
        again = copy.deepcopy(merge)
        again['id'] = 'E4'
        with self.assertRaises(ValueError):
            self.compile([added(), updated(), merge, again])
        invalidate = {'id': 'E2', 'knowledge_key': 'main-retry', 'node_id': 'N2', 'operation': 'invalidate',
                      'previous': 'E1', 'note': '原策略失效。', 'evidence': evidence('D.N2.00002', '主链路不重试')}
        with self.assertRaises(ValueError):
            self.compile([added(), invalidate, merge])
        reactivated = updated()
        reactivated.update(id='E3', node_id='N3', previous='E2',
                           item=item('主链路不重试', 'D.N3.00002', '不重试'))
        result = self.compile([added(), invalidate, reactivated])
        self.assertEqual(result['memory_index'][0]['version'], 2)
        self.assertEqual(result['items'][0]['conclusion']['text'], '主链路不重试')


if __name__ == '__main__':
    unittest.main()
