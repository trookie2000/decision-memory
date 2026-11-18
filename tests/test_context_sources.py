"""Structural provenance uses a flow's own title, never unrelated nearby text."""
import copy
import tempfile
import unittest
from pathlib import Path

from decision_memory.client import Config, ModelClient
from decision_memory.context_sources import CONTEXT_POLICY, flow_context
from decision_memory.grounded import (
    LEGACY_PROTOCOL, V1_LOCAL_POLICY, V1_LOCAL_PROMPT, decode_payload, extract_grounded, review_payload, signature,
)
from decision_memory.requirements import assemble_requirement
from decision_memory.run_report import render_result_html
from decision_memory.sources import index_materials
from decision_memory.storage import read_json, render_markdown, result_digest, save_result, validate_saved
from http_fixture import completion, model_server


def context(artifact=None):
    return index_materials('**用户：**\n只缓存展示；超时保留旧值。', artifact or
        '# 方案\n```\nReadCacheFlow（当前主链路）\n  -> 读取展示缓存\n  -> 超时保留旧值，不阻塞主链路\n```',
        'design', 'CTX', 'dialogue.md', 'artifact.md')


def proposed(source='A00005'):
    return {'items': [{'conclusion': {'text': 'ReadCacheFlow 超时保留旧值，不阻塞主链路。', 'evidence': [source]},
                      'reason': {'text': '未说明', 'evidence': []},
                      'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}


class ContextSourceTests(unittest.TestCase):
    def test_only_actual_flow_body_inherits_its_own_header(self):
        mapping = flow_context(context())
        self.assertEqual(mapping['A00005']['header_source_id'], 'A00003')
        self.assertNotIn('D00002', mapping)
        self.assertNotIn('A00003', mapping)
        bad = context('ReadCacheFlow\n\n超时保留旧值，不阻塞主链路。')
        self.assertEqual(flow_context(bad), {})
        with self.assertRaises(ValueError):
            decode_payload(proposed('A00003'), bad)

    def test_plain_code_and_other_flow_blocks_do_not_supply_a_missing_symbol(self):
        sample = context('```\nReadCacheFlow\n  return oldValue\n```\n'
                         '```\nOtherFlow\n  -> 超时保留旧值，不阻塞主链路\n```')
        with self.assertRaisesRegex(ValueError, 'ReadCacheFlow'):
            decode_payload(proposed('A00007'), sample)

    def test_real_http_saved_context_is_marked_and_replayable_without_changing_text(self):
        final = proposed()
        final.update(review_notes=['结论语义按原文核对。'], field_checks=[
            {'item_index': 1, 'field': name, 'status': 'supported' if name == 'conclusion' else 'unstated', 'notes': '本字段依据。'}
            for name in ('conclusion', 'reason', 'boundary')])
        with tempfile.TemporaryDirectory() as temporary:
            root = Path(temporary)
            with model_server([(200, completion(proposed())), (200, completion(final))]) as (url, requests):
                result = extract_grounded(context(), ModelClient(Config(endpoint=url, model='fixture',
                    api_key_env='', retries=0)), root / 'run')
            self.assertEqual(len(requests), 2)
            field = result['items'][0]['conclusion']
            self.assertEqual(field['text'], final['items'][0]['conclusion']['text'])
            self.assertEqual([r['source_id'] for r in field['evidence']], ['A00005', 'A00003'])
            self.assertEqual(field['evidence_context']['declared_source_ids'], ['A00005'])
            self.assertEqual(field['evidence_context']['added_source_ids'], ['A00003'])
            self.assertEqual(result['grounding_proof']['context_policy_digest'], __import__('decision_memory.evaluation', fromlist=['digest']).digest(CONTEXT_POLICY))
            self.assertIn('程序补充的流程标题', render_result_html(result, context()))
            self.assertIn('程序补充的流程标题', render_markdown(result))
            save_result(root / 'result.json', result, sample=context())
            validate_saved(read_json(root / 'result.json'), context())
            changed = copy.deepcopy(result)
            changed['items'][0]['conclusion']['evidence'].pop()
            changed['result_digest'] = result_digest(changed)
            with self.assertRaises(ValueError):
                validate_saved(changed, context())
            changed = copy.deepcopy(result)
            changed['items'][0]['conclusion']['evidence_context']['blocks'][0]['opening_line'] = 1
            changed['result_digest'] = result_digest(changed)
            with self.assertRaises(ValueError):
                validate_saved(changed, context())

            # Snapshots written before this optional policy remain replayable.
            old_payload = copy.deepcopy(final)
            old_payload['items'][0]['conclusion']['evidence'].append('A00003')
            old = {**result, **review_payload(old_payload, context(), include_context=False,
                                             include_retrieval=False, policy=V1_LOCAL_POLICY)}
            old.pop('input_contract')
            old.update(extraction_protocol=LEGACY_PROTOCOL, version='0.2.0', prompt_hash=V1_LOCAL_PROMPT,
                       source_columns=list(V1_LOCAL_POLICY['columns']))
            old['run_signature'] = signature(context(), old['requested_config'], include_context=False,
                                             include_retrieval=False, protocol=LEGACY_PROTOCOL,
                                             version='0.2.0', prompt=V1_LOCAL_PROMPT)
            old['result_digest'] = result_digest(old)
            validate_saved(old, context())

    def test_unsupported_code_fences_do_not_poison_the_following_plain_flow(self):
        sample = context('```java config\nReadCacheFlow\n  -> 超时保留旧值\n```\n'
                         '```text\nOtherFlow\n  -> 超时保留旧值\n```')
        self.assertEqual(set(flow_context(sample)), {'A00007'})
        self.assertEqual(flow_context(sample)['A00007']['header_source_id'], 'A00006')
        for artifact in ('```\nReadCacheFlow\n  -> 超时保留旧值',
                         '```\nReadCacheFlow\n-> 超时保留旧值\n```'):
            self.assertEqual(flow_context(context(artifact)), {})

    def test_context_never_crosses_requirement_nodes_or_exceeds_the_reference_cap(self):
        sample = assemble_requirement('PAIR', [('early', 'design', context()),
            ('late', 'design', index_materials('**用户：**\n另一个节点。',
                '```\nOtherFlow\n  -> 超时保留旧值\n```', 'design', 'LATE', 'late-d.md', 'late-a.md'))])
        self.assertEqual(flow_context(sample)['A.early.00005']['header_source_id'], 'A.early.00003')
        with self.assertRaisesRegex(ValueError, 'ReadCacheFlow'):
            decode_payload(proposed('A.late.00003'), sample)
        crowded = context('```\nReadCacheFlow\n' + '\n'.join('  -> 规则' + str(i) for i in range(16)) + '\n```')
        value = proposed()
        value['items'][0]['conclusion']['evidence'] = ['A' + str(i).zfill(5) for i in range(3, 19)]
        with self.assertRaisesRegex(ValueError, '超过16'):
            decode_payload(value, crowded)

    def test_program_header_cannot_replace_a_real_user_source(self):
        value = proposed(); value['items'][0]['conclusion']['text'] = '用户要求 ReadCacheFlow 保留旧值。'
        with self.assertRaisesRegex(ValueError, '用户'):
            decode_payload(value, context())


if __name__ == '__main__':
    unittest.main()
