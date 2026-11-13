import tempfile
import unittest
import zipfile
import hashlib
from pathlib import Path
from unittest.mock import patch

from decision_memory.sources import discover_samples, load_sample, make_sample


class SourceTests(unittest.TestCase):
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
