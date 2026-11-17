"""Grounded protocol checks use fictional sources and real local HTTP."""
import copy
import json
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient, ModelError, ModelOutputError
from decision_memory.grounded import (
    GroundedNeedsReview, decode_payload, extract_grounded, source_packet,
)
from decision_memory.pipeline import MODES, extract
from decision_memory.requirements import assemble_requirement
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, result_digest, save_result, validate_saved
from http_fixture import completion, model_server


def context():
    return index_materials(
        '**用户：**\n详情缓存仅处理展示数据，订单不改。\n**AGENT：**\n旧接口每批上限50条。\n'
        '**用户：**\n保持兼容；为何50条暂未确认。',
        '# 方案\n详情缓存仅处理展示数据，订单不改。\n旧接口每批上限50条。',
        'design', 'FAKE', 'dialogue.md', 'artifact.md')


def field(text='未说明', ids=None):
    return {'text': text, 'evidence': ids or []}


def proposed():
    return {'items': [{'kind': 'scope',
        'conclusion': field('详情缓存仅处理展示数据，订单不改。', ['D00002', 'A00002']),
        'reason': field(), 'boundary': field('限于详情缓存。', ['D00002', 'A00002'])},
        {'kind': 'contract', 'conclusion': field('旧接口每批上限50条。', ['D00004', 'A00003']),
         'reason': field(), 'boundary': field()}],
        'open_questions': [field('为何50条尚未确认。', ['D00006'])]}


def reviewed(value=None):
    value = copy.deepcopy(value or proposed())
    value['review_notes'] = ['逐字段核对完整原文，保留明确未知。']
    value['field_checks'] = [{'item_index': i, 'field': name,
        'status': 'unstated' if item[name]['text'] == '未说明' else 'supported', 'notes': '核对自身引文。'}
        for i, item in enumerate(value['items'], 1) for name in ('conclusion', 'reason', 'boundary')]
    return value


class GroundedTests(unittest.TestCase):
    def client(self, endpoint):
        return ModelClient(Config(endpoint=endpoint, model='fixture', api_key_env='', retries=2))

    def test_packet_is_lossless_stage_and_ordered_requirement_without_aliases(self):
        stage = context()
        packet = json.loads(source_packet(stage))
        self.assertEqual(packet['input_scope'], 'stage')
        rows = [dict(zip(packet['source_columns'], row)) for row in packet['sources']]
        for row in rows:
            source = stage.catalog[row['source_id']]
            self.assertEqual([row[k] for k in ('file', 'line', 'role', 'message', 'text')],
                             [source.file, source.line, source.role, source.message, source.text])
            self.assertNotIn('#C', row['source_id'])
        whole = assemble_requirement('REQ', [('N1', 'design', stage)])
        packet = json.loads(source_packet(whole))
        self.assertEqual(packet['input_scope'], 'requirement')
        self.assertEqual(packet['nodes'], list(whole.nodes))
        self.assertEqual(packet['sources'][0][1:3], ['N1', 0])

    def test_all_invalid_ids_reported_before_expansion_and_objects_rejected(self):
        value = proposed()
        value['items'][0]['conclusion']['evidence'] = ['A99999', 'D99999']
        value['open_questions'][0]['evidence'] = ['X12345']
        with self.assertRaises(ValueError) as caught:
            decode_payload(value, context())
        for source in ('A99999', 'D99999', 'X12345'):
            self.assertIn(source, str(caught.exception))
        for refs in ([{'source_id': 'A00002', 'quote': '详情'}], ['A00002', 'A00002']):
            value['items'][0]['conclusion']['evidence'] = refs
            with self.assertRaises(ValueError):
                decode_payload(value, context())

    def test_two_calls_expand_full_lines_and_offline_replay_rejects_tampering(self):
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(proposed())), (200, completion(reviewed()))]) as (url, requests):
                client = self.client(url)
                result = extract(context(), client, 'grounded', run_dir=root / 'run')
            self.assertEqual(len(requests), 2)
            self.assertEqual(client.config.retries, 2)
            self.assertEqual(result['schema_version'], 5)
            self.assertEqual(result['input_scope'], 'stage')
            self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['quote'], context().catalog['D00002'].text)
            self.assertEqual(len(result['open_questions']), 1)
            self.assertEqual(result['checks']['human_review'], 'pending')
            self.assertEqual(result['grounding_proof']['status'], 'model_supported')
            self.assertEqual(read_json(root / 'run/state.json')['status'], 'completed')
            save_result(root / 'result.json', result, sample=context())
            validate_saved(read_json(root / 'result.json'), context(), client, 'grounded')
            for key in ('grounding_proof', 'extraction_protocol'):
                changed = copy.deepcopy(result)
                changed.pop(key)
                changed['result_digest'] = result_digest(changed)
                with self.assertRaises(ValueError):
                    validate_saved(changed, context())
            changed = copy.deepcopy(result)
            changed['items'][0]['conclusion']['evidence'][0]['quote'] = '详情'
            changed['result_digest'] = result_digest(changed)
            with self.assertRaises(ValueError):
                validate_saved(changed, context())

    def test_existing_agent_system_fact_is_allowed_but_explicit_user_attribution_needs_own_user(self):
        value = proposed()
        value['items'][1]['conclusion']['evidence'] = ['D00004']
        decode_payload(value, context())
        value['items'][1]['reason'] = field('用户要求兼容。', ['A00003'])
        with self.assertRaisesRegex(ValueError, '用户'):
            decode_payload(value, context())

    def test_normalized_unstated_punctuation_survives_full_save_and_replay(self):
        for text in ('未说明。', '未说明.'):
            value = reviewed()
            value['items'][0]['reason']['text'] = text
            with self.subTest(text=text), tempfile.TemporaryDirectory() as temporary:
                root = Path(temporary)
                with model_server([(200, completion(proposed())), (200, completion(value))]) as (url, requests):
                    result = extract_grounded(context(), self.client(url), root / 'run')
                save_result(root / 'result.json', result, sample=context())
                saved = read_json(root / 'result.json')
                validate_saved(saved, context())
                self.assertEqual(saved['items'][0]['reason']['protocol_normalization']['original_text'], text)

    def test_common_user_attributions_cannot_be_published_from_agent_only(self):
        for prefix in ('用户已确认', '用户已经明确要求', '用户的建议', '根据用户反馈'):
            value = proposed()
            value['items'][1]['reason'] = field(prefix + '每批上限50条。', ['D00004'])
            with self.subTest(prefix=prefix), tempfile.TemporaryDirectory() as temporary:
                with model_server([(200, completion(value)), (200, completion(reviewed(value)))]) as (url, requests):
                    with self.assertRaisesRegex(ValueError, '用户'):
                        extract_grounded(context(), self.client(url), Path(temporary) / 'run')

    def test_whole_markdown_reports_actual_grounded_field_review(self):
        from decision_memory.storage import render_markdown
        result = {'sample_id': 'FAKE', 'scope': 'requirement', 'schema_version': 5, 'mode': 'grounded',
                  'model': 'fixture', 'nodes': [], 'items': [], 'open_questions': [], 'metrics': {},
                  'grounding_proof': {'status': 'model_supported'}}
        self.assertIn('模型已逐字段复核', render_markdown(result))

    def test_field_symbol_cannot_borrow_another_fields_source(self):
        stage = index_materials('**用户：**\n沿用 CacheWriter。', '仅缓存展示。', 'design', 'FAKE', 'd.md', 'a.md')
        value = {'items': [{'conclusion': field('仅缓存展示。', ['A00001']),
                          'reason': field('沿用 CacheWriter。', ['A00001']),
                          'boundary': field('沿用 CacheWriter。', ['D00002'])}], 'open_questions': []}
        with self.assertRaisesRegex(ValueError, 'CacheWriter'):
            decode_payload(value, stage)

    def test_unsupported_semantics_is_draft_and_not_protocol_repaired(self):
        value = reviewed()
        value['field_checks'][0]['status'] = 'unsupported'
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(proposed())), (200, completion(value))]) as (url, requests):
                with self.assertRaises(GroundedNeedsReview):
                    extract_grounded(context(), self.client(url), root / 'run')
            self.assertEqual(len(requests), 2)
            self.assertTrue((root / 'run/draft.json').exists())
            self.assertEqual(read_json(root / 'run/state.json')['status'], 'needs_review')

    def test_each_step_has_one_structure_repair_and_existing_directory_never_resends(self):
        invalid = proposed(); invalid['items'][0]['conclusion']['evidence'] = ['A99999']
        review = reviewed(); review.pop('field_checks')
        responses = [(200, completion(p)) for p in (invalid, proposed(), review, reviewed())]
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run'
            with model_server(responses) as (url, requests):
                extract_grounded(context(), self.client(url), path)
                with self.assertRaises(ValueError):
                    extract_grounded(context(), self.client(url), path)
            self.assertEqual(len(requests), 4)
            self.assertIn('A99999', requests[1]['messages'][1]['content'])

    def test_parse_and_transport_failures_are_not_automatically_resent(self):
        for response, exception, state in ((completion('{'), ModelOutputError, 'failed'),
                                           ('interrupted-body', ModelError, 'unknown')):
            with self.subTest(state=state), tempfile.TemporaryDirectory() as temporary:
                path = Path(temporary) / 'run'
                with model_server([(200, response), (200, completion(proposed()))]) as (url, requests):
                    with self.assertRaises(exception):
                        extract_grounded(context(), self.client(url), path)
                self.assertEqual(len(requests), 1)
                self.assertEqual(read_json(path / 'state.json')['status'], state)

    def test_structure_repair_cannot_erase_an_unsupported_field(self):
        invalid = reviewed()
        invalid['field_checks'][0]['status'] = 'unsupported'
        invalid['field_checks'].pop()
        with tempfile.TemporaryDirectory() as temporary:
            path = Path(temporary) / 'run'
            with model_server([(200, completion(p)) for p in (proposed(), invalid, reviewed())]) as (url, requests):
                with self.assertRaisesRegex(ValueError, '待复核'):
                    extract_grounded(context(), self.client(url), path)
            self.assertEqual(len(requests), 3)

    def test_whole_snapshot_is_explicit_and_legacy_event_mode_does_not_silently_accept_it(self):
        from decision_memory.requirement_pipeline import extract_requirement
        self.assertEqual(MODES, ('baseline', 'verified', 'artifact-only'))
        whole = assemble_requirement('REQ', [('N1', 'design', context())])
        value = proposed()
        for item in value['items']:
            for name in ('conclusion', 'reason', 'boundary'):
                item[name]['evidence'] = [s[0] + '.N1.' + s[1:] for s in item[name]['evidence']]
        value['open_questions'][0]['evidence'] = ['D.N1.00006']
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(value)), (200, completion(reviewed(value)))]) as (url, requests):
                client = self.client(url)
                with self.assertRaises(ValueError):
                    extract_requirement(whole, client, 'grounded')
                result = extract_grounded(whole, client, root / 'run')
            self.assertEqual(len(requests), 2)
            self.assertEqual(result['input_scope'], 'requirement')
            self.assertEqual(result['requirement_id'], whole.sample_id)
            self.assertEqual(result['nodes'], list(whole.nodes))
            save_result(root / 'result.json', result, sample=whole)
            validate_saved(read_json(root / 'result.json'), whole)
            with self.assertRaises(ValueError):
                validate_saved(read_json(root / 'result.json'), context())


if __name__ == '__main__':
    unittest.main()
