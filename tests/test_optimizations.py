import io
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

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


class RequirementUnitTests(unittest.TestCase):
    NODES = [('analysis', 'r/上下文/需求确认记录.md', '**用户：**\n归排序方负责。\n', 'r/产物/需求分析.md', '- 排序归排序方。\n'),
             ('design', 'r/上下文/技术设计确认记录.md', '**AGENT：**\n先过滤低价品？\n\n**用户：**\n不能删。\n',
              'r/产物/技术方案.md', '- 低价品保留在候选中。\n')]

    def sample(self):
        from decision_memory.sources import make_requirement_sample
        return make_requirement_sample('REQ-x', self.NODES)

    def test_ids_are_unique_per_node_and_keep_dialogue_artifact_prefix(self):
        sample = self.sample()
        self.assertEqual(sample.stage, 'requirement')
        self.assertEqual(sorted({key[:3] for key in sample.catalog}), ['A1-', 'A2-', 'D1-', 'D2-'])
        source = sample.catalog['D2-00005']
        self.assertEqual((source.node, source.role, source.text), (2, 'user', '不能删。'))
        self.assertEqual(sample.node_stages, {1: '需求分析', 2: '技术设计'})

    def test_prompt_lists_nodes_in_order_and_tags_each_line(self):
        prompt = json.loads(self.sample().prompt_text())
        self.assertEqual([node['stage'] for node in prompt['nodes']], ['需求分析', '技术设计'])
        self.assertTrue(all('node' in line for line in prompt['sources']))

    def test_citations_validate_across_nodes(self):
        sample = self.sample()
        result = validate_result({'items': [{'kind': 'change',
            'conclusion': {'text': '低价品保留', 'evidence': [{'source_id': 'D2-00005', 'quote': '不能删'},
                                                            {'source_id': 'A2-00001', 'quote': '低价品保留在候选中'}]},
            'reason': UNSTATED, 'boundary': UNSTATED}], 'open_questions': []}, sample)
        self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['line'], 5)

    def test_discovery_groups_stage_pairs_into_ordered_requirements(self):
        from decision_memory.sources import discover_requirements
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory) / 'REQ-x'
            for _, dialogue_name, dialogue, artifact_name, artifact in reversed(self.NODES):
                for name, text in ((dialogue_name, dialogue), (artifact_name, artifact)):
                    target = root / Path(name).relative_to('r')
                    target.parent.mkdir(parents=True, exist_ok=True)
                    target.write_text(text, encoding='utf-8')
            annotation = Path(directory) / '评测标注' / '应被沉淀知识标注.md'
            annotation.parent.mkdir()
            annotation.write_text('答案', encoding='utf-8')
            samples = discover_requirements(Path(directory))
        self.assertEqual([sample.sample_id for sample in samples], ['REQ-x'])
        self.assertEqual(samples[0].node_stages, {1: '需求分析', 2: '技术设计'})
        self.assertNotIn('答案', samples[0].prompt_text())

    def test_scorer_maps_requirement_predictions_to_their_requirement(self):
        self.assertEqual(official_eval.requirement_of({'stage': 'requirement', 'sample_id': 'REQ-a-b'}), 'REQ-a-b')
        self.assertEqual(official_eval.requirement_of({'stage': 'design', 'sample_id': 'REQ-a-design'}), 'REQ-a')


class RepairTests(unittest.TestCase):
    def setUp(self):
        self.sample = make_sample(DIALOGUE, ARTIFACT, 'design')
        ids = {source.text: key for key, source in self.sample.catalog.items()}
        self.user = ids['不能删，低价品也要参与排序。']

    def test_repair_strips_bad_citations_and_downgrades_unsupported_fields(self):
        from decision_memory.validation import repair_result
        payload = {'items': [
            {'conclusion': {'text': '低价品参与排序', 'evidence': [{'source_id': self.user, 'quote': '低价品也要参与排序'},
                                                              {'source_id': self.user, 'quote': '编造的引文'}]},
             'reason': {'text': '用户说的', 'evidence': [{'source_id': 'D09999', 'quote': 'x'}]},
             'boundary': {'text': '有边界但没证据', 'evidence': []}},
            {'conclusion': {'text': '无证据结论', 'evidence': [{'source_id': self.user, 'quote': '不存在'}]},
             'reason': UNSTATED, 'boundary': UNSTATED}],
            'open_questions': [{'text': '无证据', 'evidence': []}]}
        fixed, notes = repair_result(payload, self.sample)
        result = validate_result(fixed, self.sample)
        self.assertEqual(len(result['items']), 1)
        item = result['items'][0]
        self.assertEqual(len(item['conclusion']['evidence']), 1)
        self.assertEqual((item['reason']['text'], item['boundary']['text']), ('未说明', '未说明'))
        self.assertEqual(result['open_questions'], [])
        self.assertTrue(any('已删除' in note for note in notes))

    def test_pipeline_repairs_only_after_the_strict_retry_and_records_it(self):
        from decision_memory.pipeline import extract
        bad = {'items': [{'conclusion': {'text': '低价品参与排序', 'evidence': [
                   {'source_id': self.user, 'quote': '低价品也要参与排序'}]},
                          'reason': {'text': '无证据理由', 'evidence': []}, 'boundary': UNSTATED}],
               'open_questions': []}
        client = FakeClient([bad, bad])
        result = extract(self.sample, client, 'baseline')
        self.assertEqual(len(client.prompts), 2)
        self.assertIn('校验失败', client.prompts[1][1])
        self.assertEqual(result['items'][0]['reason']['text'], '未说明')
        self.assertTrue(result['checks']['citation_repairs'])
        self.assertTrue(any('保守修复' in note for note in result['review_notes']))


class ExtraFileTests(unittest.TestCase):
    """The folder layout is fixed, but a requirement may carry more files than the two stage pairs."""

    def build(self, directory):
        files = {
            'REQ-a/上下文/技术设计确认记录.md': '**用户：**\n不能删。\n',
            'REQ-a/产物/技术方案.md': '- 候选保留。\n',
            'REQ-a/上下文/需求确认记录.md': '**用户：**\n归排序方。\n',
            'REQ-a/产物/需求分析.md': '- 排序归排序方。\n',
            'REQ-a/上下文/数据口径说明.md': '# 口径\n浏览 pv 一次计一次。\n',
            'REQ-a/产物/接口清单.md': '- 复用现有接口。\n',
            'REQ-b/上下文/需求确认记录.md': '**用户：**\n别的需求。\n',
            'REQ-b/产物/需求分析.md': '- 别的需求。\n',
            '评测标注/应被沉淀知识标注.md': '答案',
        }
        for name, text in files.items():
            target = Path(directory) / name
            target.parent.mkdir(parents=True, exist_ok=True)
            target.write_text(text, encoding='utf-8')

    def test_extra_files_have_types_but_no_inferred_chronology(self):
        from decision_memory.sources import discover_requirements
        with tempfile.TemporaryDirectory() as directory:
            self.build(directory)
            sample = discover_requirements(Path(directory), sample_id='REQ-a')[0]
        self.assertEqual(sample.node_stages, {1: '需求分析', 2: '技术设计'})
        lines = {source.text: (source.source_id, source.role) for source in sample.catalog.values()}
        self.assertEqual(lines['归排序方。'], ('D1-00002', 'user'))
        self.assertEqual(lines['不能删。'], ('D2-00002', 'user'))
        self.assertEqual(lines['浏览 pv 一次计一次。'], ('D3-00002', 'material'))
        self.assertEqual(lines['- 复用现有接口。'], ('A4-00001', 'material'))
        prompt = json.loads(sample.prompt_text())
        self.assertEqual(len(prompt['nodes']), 2)
        for key in ('D3-00002', 'A4-00001'):
            self.assertEqual(sample.catalog[key].document_type, 'supplement')
            self.assertIsNone(sample.catalog[key].node)
        self.assertEqual(len(prompt['documents']), 6)
        self.assertNotIn('答案', sample.prompt_text())

    def test_only_the_selected_requirement_is_read(self):
        from decision_memory import sources
        with tempfile.TemporaryDirectory() as directory:
            self.build(directory)
            reads = []
            original = sources.read_text
            with patch.object(sources, 'read_text', side_effect=lambda target: reads.append(target.name) or original(target)):
                sources.discover_requirements(Path(directory), sample_id='REQ-b')
        self.assertEqual(sorted(reads), ['需求分析.md', '需求确认记录.md'])

    def test_zip_and_directory_give_the_same_sample(self):
        from decision_memory.sources import discover_requirements
        with tempfile.TemporaryDirectory() as directory:
            self.build(directory)
            archive_path = Path(directory) / 'samples.zip'
            with zipfile.ZipFile(archive_path, 'w') as archive:
                for file in Path(directory).rglob('*.md'):
                    archive.write(file, file.relative_to(directory).as_posix())
            from_dir = discover_requirements(Path(directory), sample_id='REQ-a')[0]
            from_zip = discover_requirements(archive_path, sample_id='REQ-a')[0]
        self.assertEqual(from_zip.node_stages, from_dir.node_stages)
        self.assertEqual({k: v.text for k, v in from_zip.catalog.items()}, {k: v.text for k, v in from_dir.catalog.items()})
