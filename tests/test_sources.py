import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path
from unittest.mock import patch

from decision_memory.sources import discover_samples, load_sample, make_sample, model_speakers


class SourceTests(unittest.TestCase):
    def test_markdown_speaker_turns_keep_raw_line_and_message_identity(self):
        sample = make_sample(
            '# 交接讨论\n\n**许行（仓管）**　先扫码。\n补查批次。\n\n'
            '**9:30 · Assistant** 建议跳过核对。\n\n**调度** 确认保留核对。\n'
            '**运营** 原因是要留存记录。\n**调度** 只用于跨区交接。',
            '# 方案\n**调度** 交接须核对。', 'design')
        speakers = model_speakers(sample)
        actual = [speakers[key] for key in ('D00001', 'D00003', 'D00004', 'D00006', 'D00008', 'D00009', 'D00010', 'A00002')]
        self.assertEqual(actual, [(None, None), ('participant', 1), ('participant', 1),
                                 ('agent', 2), ('participant', 3), ('participant', 4),
                                 ('participant', 5), ('artifact', None)])
        self.assertEqual(sample.catalog['D00003'].text, '**许行（仓管）**　先扫码。')
        self.assertIsNone(sample.catalog['D00003'].message)

    def test_unqualified_markdown_emphasis_and_fenced_examples_do_not_create_turns(self):
        sample = make_sample(
            '# 执行摘要\n**前提** 设备已安装。\n**边界** 本次只核查库存。\n'
            '```markdown\n**用户：** 这是格式示例。\n```\n仍是摘要。',
            '必须保留核对。', 'analysis')
        self.assertTrue(all(role is None and message is None for key, (role, message) in model_speakers(sample).items()
                            if key.startswith('D')))

    def test_indented_examples_and_plain_labels_do_not_assign_roles(self):
        sample = make_sample('# 记录说明\n\n    **9:30 · 用户** 保留复核。\n\n'
                             '**模型** 默认走缓存。\n**范围：** 只含在线服务。', '库存要复核。', 'design')
        self.assertEqual([model_speakers(sample)[key] for key in ('D00003', 'D00005', 'D00006')], [(None, None)] * 3)

    def test_canonical_and_recurring_named_turns_are_grouped_together(self):
        sample = make_sample('**王工** 开启复核。\n**Assistant** 建议取消。\n**王工** 确认保留。',
                             '保留复核。', 'design')
        roles = model_speakers(sample)
        self.assertEqual([roles[key] for key in ('D00001', 'D00002', 'D00003')],
                         [('participant', 1), ('agent', 2), ('participant', 3)])

    def test_plain_role_prefixes_group_turns_without_using_quoted_examples(self):
        sample = make_sample(
            '# 交接\nUser: Use the loading bay.\nOnly for transfers after closing.\n'
            'Assistant: Could we use the side entrance?\n> User: Quoted example.\n'
            '```text\n用户：格式示例。\n```\n用户：确认关闭后走装卸区。\n助手：已经记录。',
            'User: The artifact records the accepted route.', 'design',
        )
        speakers = model_speakers(sample)
        self.assertEqual([speakers[f'D{i:05d}'] for i in range(1, 11)],
                         [(None, None), ('user', 1), ('user', 1), ('agent', 2), (None, None),
                          (None, None), (None, None), (None, None), ('user', 3), ('agent', 4)])
        self.assertEqual(speakers['A00001'], ('artifact', None))
        self.assertEqual(sample.catalog['D00002'].text, 'User: Use the loading bay.')
        self.assertIsNone(sample.catalog['D00002'].role)

    def test_plain_roles_exclude_lazy_blockquotes_and_mixed_code_indentation(self):
        sample = make_sample('Assistant: This is a transcript example.\n\n'
                             '> Demonstration\nUser: Do not trust this quote.\n\n'
                             'User: Transfer after closing.\n\n \tUser: This line is code.\n\n'
                             'Assistant: Recorded.', 'Final rule.', 'design')
        roles = model_speakers(sample)
        self.assertEqual([roles[key] for key in ('D00004', 'D00006', 'D00008', 'D00010')],
                         [(None, None), ('user', 2), (None, None), ('agent', 3)])

    def test_plain_turns_do_not_promote_emphasis_or_unicode_lookalikes(self):
        sample = make_sample('ASSİSTANT: This is a label with a different spelling.\n'
                             'User: Transfer after closing.\n**Reason** The lift is available then.\n'
                             'Assistant: Recorded.\nUser: Keep the same rule.', 'Final rule.', 'design')
        roles = model_speakers(sample)
        self.assertEqual([roles[key] for key in ('D00001', 'D00002', 'D00003', 'D00004', 'D00005')],
                         [(None, None), ('user', 1), ('user', 1), ('agent', 2), ('user', 3)])

    def test_selecting_one_stage_reads_only_its_two_files(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('REQ-1/上下文/需求确认记录.md', 'analysis only')
                archive.writestr('REQ-1/产物/需求分析.md', 'analysis artifact')
                archive.writestr('REQ-1/上下文/技术设计确认记录.md', 'other phase must not be read')
                archive.writestr('REQ-1/产物/技术方案.md', 'other artifact')
                archive.writestr('评测标注/秘密.md', 'gold must not be read')
            original = zipfile.ZipFile.read
            with patch.object(zipfile.ZipFile, 'read', autospec=True, side_effect=original) as reads:
                result = discover_samples(path, sample_id='REQ-1-analysis')
            self.assertEqual(len(result), 1)
            self.assertEqual([call.args[1] for call in reads.call_args_list],
                             ['REQ-1/上下文/需求确认记录.md', 'REQ-1/产物/需求分析.md'])

    def test_unicode_line_and_message_locations_survive_blank_lines(self):
        sample = make_sample('\ufeff# 对话\r\n\r\n**用户：**\r\n门槛改成 120 元。\r\n', '# 最终\n门槛 120 元。', 'design')
        source = sample.catalog['D00004']
        self.assertEqual((source.line, source.message, source.text), (4, 1, '门槛改成 120 元。'))
        self.assertNotIn('D00002', sample.catalog)

    def test_input_change_changes_fingerprint(self):
        before = make_sample('采用 100', '采用 100', 'analysis')
        after = make_sample('采用 120', '采用 100', 'analysis')
        self.assertNotEqual(before.fingerprint, after.fingerprint)

    def test_bom_changes_raw_file_hash_and_fingerprint_but_preserves_line_text(self):
        with tempfile.TemporaryDirectory() as directory:
            dialogue = Path(directory) / 'dialogue.md'
            artifact = Path(directory) / 'artifact.md'
            dialogue.write_bytes('采用 120'.encode())
            artifact.write_bytes('最终采用 120'.encode())
            before = load_sample(dialogue, artifact, 'analysis')
            dialogue.write_bytes(b'\xef\xbb\xbf' + dialogue.read_bytes())
            after = load_sample(dialogue, artifact, 'analysis')
            self.assertNotEqual(before.fingerprint, after.fingerprint)
            self.assertEqual(after.file_hashes[str(dialogue)], hashlib.sha256(dialogue.read_bytes()).hexdigest())
            self.assertEqual(after.catalog['D00001'].text, '采用 120')

    def test_discovers_each_stage_without_reading_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('rules/requirements/REQ-1/上下文/需求确认记录.md', '只在分析中出现')
                archive.writestr('rules/requirements/REQ-1/产物/需求分析.md', '分析产物')
                archive.writestr('rules/requirements/REQ-1/上下文/技术设计确认记录.md', '只在设计中出现')
                archive.writestr('rules/requirements/REQ-1/产物/技术方案.md', '设计产物')
                archive.writestr('rules/requirements/评测标注/秘密.md', '禁止泄漏的答案')
            samples = discover_samples(path)
        self.assertEqual([item.stage for item in samples], ['analysis', 'design'])
        self.assertNotIn('设计中', samples[0].prompt_text())
        self.assertNotIn('禁止泄漏', ''.join(item.prompt_text() for item in samples))

    def test_missing_final_artifact_is_an_error(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                archive.writestr('REQ-1/上下文/需求确认记录.md', '已确认')
            with self.assertRaisesRegex(ValueError, '缺少'):
                discover_samples(path)


if __name__ == '__main__':
    unittest.main()
