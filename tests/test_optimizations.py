import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path

from decision_memory import official_eval
from decision_memory.client import ModelOutputError, parse_json
from decision_memory.collaboration import BattleConfig
from decision_memory.pipeline import drop_superseded
from decision_memory.sources import make_sample
from decision_memory.validation import validate_result

DIALOGUE = '''# 对话

**AGENT：**
我建议先把低价品过滤掉。

**用户：**
不能删，低价品也要参与排序。
'''
ARTIFACT = '''# 方案
所有候选都参与排序，低价品仅不加权。
'''


def field(text, source_id, quote):
    return {'text': text, 'evidence': [{'source_id': source_id, 'quote': quote}]}


UNSTATED = {'text': '未说明', 'evidence': []}


class SourceRoleTests(unittest.TestCase):
    def test_lines_inherit_the_speaker_of_their_message(self):
        sample = make_sample(DIALOGUE, ARTIFACT, 'design')
        roles = {source.text: source.role for source in sample.catalog.values()}
        self.assertEqual(roles['我建议先把低价品过滤掉。'], 'agent')
        self.assertEqual(roles['不能删，低价品也要参与排序。'], 'user')
        self.assertEqual(roles['所有候选都参与排序，低价品仅不加权。'], 'artifact')
        prompt = json.loads(sample.prompt_text())
        self.assertIn('role', prompt['sources'][0])


class ParseJsonTests(unittest.TestCase):
    def test_tolerates_prose_fences_trailing_commas_and_control_characters(self):
        self.assertEqual(parse_json('结果如下：\n```json\n{"a": [1, 2,],}\n```\n以上。'), {'a': [1, 2]})
        self.assertEqual(parse_json('{"a": "x\ty"}'), {'a': 'x\ty'})

    def test_still_rejects_incomplete_or_non_object_output(self):
        with self.assertRaises(ModelOutputError):
            parse_json('{"a": [1, 2')
        with self.assertRaises(ModelOutputError):
            parse_json('[1, 2]')


class SupersededFilterTests(unittest.TestCase):
    def setUp(self):
        self.sample = make_sample(DIALOGUE, ARTIFACT, 'design')
        ids = {source.text: key for key, source in self.sample.catalog.items()}
        self.agent = ids['我建议先把低价品过滤掉。']
        self.user = ids['不能删，低价品也要参与排序。']
        self.artifact = ids['所有候选都参与排序，低价品仅不加权。']

    def result(self):
        return validate_result({'items': [
            {'kind': 'decision', 'conclusion': field('低价品先过滤', self.agent, '先把低价品过滤掉'),
             'reason': UNSTATED, 'boundary': UNSTATED},
            {'kind': 'correction', 'conclusion': field('低价品参与排序', self.user, '低价品也要参与排序'),
             'reason': UNSTATED, 'boundary': UNSTATED}], 'open_questions': []}, self.sample)

    def test_items_resting_only_on_superseded_lines_are_dropped_and_renumbered(self):
        decisions = [{'status': 'superseded', 'evidence': [{'source_id': self.agent}]},
                     {'status': 'active', 'evidence': [{'source_id': self.user}]}]
        result, notes = drop_superseded(self.result(), decisions, self.sample)
        self.assertEqual([item['conclusion']['text'] for item in result['items']], ['低价品参与排序'])
        self.assertEqual(result['items'][0]['id'], 'K001')
        self.assertEqual(result['items'][0]['kind'], 'correction')
        self.assertEqual(len(notes), 1)
        self.assertEqual(result['items'], validate_result(result, self.sample)['items'])

    def test_lines_also_cited_by_active_decisions_or_artifact_are_kept(self):
        decisions = [{'status': 'superseded', 'evidence': [{'source_id': self.agent}]},
                     {'status': 'active', 'evidence': [{'source_id': self.agent}]}]
        result, notes = drop_superseded(self.result(), decisions, self.sample)
        self.assertEqual(len(result['items']), 2)
        self.assertEqual(notes, [])

    def test_unknown_kind_is_rejected(self):
        with self.assertRaises(ValueError):
            validate_result({'items': [{'kind': 'misc', 'conclusion': field('x', self.user, '不能删'),
                                        'reason': UNSTATED, 'boundary': UNSTATED}],
                             'open_questions': []}, self.sample)


KNOWLEDGE = '''# 标注
## 一、需求 1（示例）
| ID | 知识候选 | 产物结论 | 上下文依据 | 边界 |
|---|---|---|---|---|
| K1 | 甲方负责排序 | 技术方案 §1 | 用户否定本地排序 | 仅限本链路 |
| K2 | 门槛为 100 | 需求分析 §五 | 对话保留变更 | 当前默认值 |
| P1 | 本地排序 | 需求确认开头 | 检验误提 | 被否定 |
## 二、需求 2（示例）
| K3 | 浏览不去重 | 需求分析 §五 | 用户纠正 | 一次计一次 |
## 三、隐性问题恢复候选
| H1 | “少一两次没关系” | 弱一致计数 | 需求 2 技术设计确认记录第 3 行 | 不适用资金 |
'''
CONTEXT = '''# 有效上下文
## 一、需求 1（示例）
来源：[a](../REQ-one/上下文/需求确认记录.md)
## 二、需求 2（示例）
来源：[b](../REQ-two/上下文/需求确认记录.md)
'''


class OfficialEvalTests(unittest.TestCase):
    def dataset(self, directory):
        path = Path(directory) / 'samples.zip'
        with zipfile.ZipFile(path, 'w') as archive:
            archive.writestr('rules/requirements/评测标注/应被沉淀知识标注.md', KNOWLEDGE)
            archive.writestr('rules/requirements/评测标注/有效上下文标注.md', CONTEXT)
            for name, analysis, design in (
                    ('REQ-one', '门槛为 100。', '甲方负责排序。\n失败时不改变候选。'),
                    ('REQ-two', '浏览不去重。', '弱一致计数，少一两次没关系。')):
                prefix = 'rules/requirements/' + name
                archive.writestr(prefix + '/上下文/需求确认记录.md', '**用户：**\n' + analysis)
                archive.writestr(prefix + '/产物/需求分析.md', analysis)
                archive.writestr(prefix + '/上下文/技术设计确认记录.md', '**用户：**\n' + design)
                archive.writestr(prefix + '/产物/技术方案.md', design)
        return path

    def test_references_are_grouped_by_requirement_with_stage_tags(self):
        with tempfile.TemporaryDirectory() as directory:
            references = official_eval.load_references(self.dataset(directory))
        self.assertEqual([ref['id'] for ref in references['REQ-one']['positives']], ['K1', 'K2'])
        self.assertEqual(references['REQ-one']['positives'][0]['stages'], ['design'])
        self.assertEqual(references['REQ-one']['positives'][1]['stages'], ['analysis'])
        self.assertEqual([ref['id'] for ref in references['REQ-one']['negatives']], ['P1'])
        self.assertEqual([ref['id'] for ref in references['REQ-two']['positives']], ['K3', 'H1'])
        self.assertEqual(references['REQ-two']['positives'][1]['stages'], ['design'])

    def test_judgment_validation_rejects_matches_on_invalid_items(self):
        payload = {'items': [{'item_id': 'design:K001', 'verdict': 'superseded', 'matches': ['K1'],
                              'citation_support': True}],
                   'positives': [{'ref_id': 'K1', 'coverage': 'missing'}]}
        with self.assertRaises(ValueError):
            official_eval.validate_judgment(payload, {'design:K001'}, {'K1'})

    def test_metrics_follow_competition_thresholds(self):
        judgment = {'items': [{'item_id': f'design:K{i:03d}', 'verdict': 'valid' if i < 19 else 'one_off',
                               'matches': [], 'citation_support': True} for i in range(20)],
                    'positives': [{'ref_id': f'K{i}', 'coverage': 'full' if i < 9 else 'partial'}
                                  for i in range(10)]}
        value = official_eval.metrics([judgment])
        self.assertAlmostEqual(value['recall'], 0.9)
        self.assertAlmostEqual(value['recall_weighted'], 0.95)
        self.assertAlmostEqual(value['precision'], 0.95)
        self.assertEqual(value['projected_points'], {'recall': 20, 'precision': 20, 'source': 10})
        self.assertEqual(value['partial_refs'], ['K9'])


class BattleTargetTests(unittest.TestCase):
    def test_operator_supplied_numeric_targets_are_accepted(self):
        config = BattleConfig(group_id='123', author_id='456', reviewer_id='789')
        self.assertEqual(config.group_id, '123')

    def test_same_author_and_reviewer_is_rejected(self):
        with self.assertRaises(ValueError):
            BattleConfig(group_id='123', author_id='456', reviewer_id='456')


if __name__ == '__main__':
    unittest.main()


class FakeClient:
    """Returns queued parsed replies; records prompts so tests can assert what the model saw."""

    def __init__(self, replies):
        self.replies, self.prompts, self.calls = list(replies), [], []
        self.config = type('C', (), {'model': 'fake', 'public': lambda self: {'model': 'fake'}})()

    def complete(self, system, user):
        self.prompts.append((system, user))
        return self.replies.pop(0)

    def metrics(self):
        return {'calls': len(self.prompts)}


class EndToEndTests(unittest.TestCase):
    def test_verified_mode_drops_superseded_items_after_review(self):
        from decision_memory.pipeline import extract
        sample = make_sample(DIALOGUE, ARTIFACT, 'design')
        ids = {source.text: key for key, source in sample.catalog.items()}
        agent, user = ids['我建议先把低价品过滤掉。'], ids['不能删，低价品也要参与排序。']
        items = [{'conclusion': field('低价品先过滤', agent, '先把低价品过滤掉'), 'reason': UNSTATED,
                  'boundary': UNSTATED},
                 {'conclusion': field('低价品参与排序', user, '低价品也要参与排序'), 'reason': UNSTATED,
                  'boundary': UNSTATED}]
        decisions = {'decisions': [
            {'topic': '低价品', 'status': 'superseded', 'text': '先过滤',
             'evidence': [{'source_id': agent, 'quote': '先把低价品过滤掉'}]},
            {'topic': '低价品', 'status': 'active', 'text': '参与排序',
             'evidence': [{'source_id': user, 'quote': '低价品也要参与排序'}]}]}
        client = FakeClient([decisions, {'items': items, 'open_questions': []},
                             {'items': items, 'open_questions': [], 'review_notes': []}])
        result = extract(sample, client, 'verified')
        self.assertEqual([item['conclusion']['text'] for item in result['items']], ['低价品参与排序'])
        self.assertTrue(any('已剔除' in note for note in result['review_notes']))
        self.assertIn('决策轨迹', client.prompts[2][1])

    def test_evaluate_scores_a_prediction_directory(self):
        from decision_memory.pipeline import extract
        from decision_memory.sources import discover_samples
        from decision_memory.storage import save_result
        with tempfile.TemporaryDirectory() as directory:
            dataset = OfficialEvalTests().dataset(directory)
            predictions = Path(directory) / 'run'
            predictions.mkdir()
            for sample in discover_samples(dataset, requirement_id='REQ-one'):
                items = [] if sample.stage == 'analysis' else [
                    {'conclusion': {'text': '甲方负责排序', 'evidence': [
                        {'source_id': 'A00001', 'quote': '甲方负责排序'}]}, 'reason': UNSTATED, 'boundary': UNSTATED}]
                prediction = extract(sample, FakeClient([{'items': items, 'open_questions': []}]), 'baseline')
                save_result(predictions / (sample.sample_id + '.json'), prediction)
            (predictions / 'summary.json').write_text('{}')
            judge = {'items': [{'item_id': 'design:K001', 'verdict': 'valid', 'matches': ['K1'],
                                'citation_support': True, 'notes': 'ok'}],
                     'positives': [{'ref_id': 'K1', 'coverage': 'full', 'items': ['design:K001'], 'notes': '规则完整。'},
                                   {'ref_id': 'K2', 'coverage': 'missing', 'items': [], 'notes': '没有门槛预测。'}]}
            client = FakeClient([judge])
            report = official_eval.evaluate(dataset, predictions, client)
        total = report['total']
        self.assertEqual((total['recall'], total['precision']), (0.5, 1.0))
        self.assertEqual(total['missing_refs'], ['K2'])
        self.assertIn('P1', client.prompts[0][1])
        self.assertIn('# 本地自测评分', official_eval.render_report(report))


class ReferenceQuoteTests(unittest.TestCase):
    def test_reference_quotes_may_use_rendered_markdown_text(self):
        from decision_memory.evaluation import validate_references
        sample = make_sample('**用户：** 差几秒没关系，别让详情页变慢。\n', '- **数据来源**：读取库存缓存。\n', 'design')
        refs = [{'id': 'R1', 'text': 'x', 'evidence': [
            {'source': 'dialogue', 'quote': '用户：差几秒没关系'},
            {'source': 'artifact', 'quote': '数据来源：读取库存缓存。'}]}]
        self.assertEqual(validate_references(refs, sample), refs)

    def test_reference_quotes_still_must_exist_in_the_named_file(self):
        from decision_memory.evaluation import validate_references
        sample = make_sample('**用户：** 差几秒没关系。\n', '- 读取库存缓存。\n', 'design')
        with self.assertRaises(ValueError):
            validate_references([{'id': 'R1', 'text': 'x', 'evidence': [
                {'source': 'artifact', 'quote': '差几秒没关系'}]}], sample)

    def test_speaker_label_before_a_mid_message_fragment_is_ignored(self):
        from decision_memory.evaluation import validate_references
        sample = make_sample('**用户：** 好的。但失败时别报错。\n', '- 失败不报错。\n', 'design')
        refs = [{'id': 'R1', 'text': 'x', 'evidence': [{'source': 'dialogue', 'quote': '用户：但失败时别报错。'}]}]
        self.assertEqual(validate_references(refs, sample), refs)
