"""Read ordered inputs without losing original lines or opening reference data."""
import json
import tempfile
import unittest
import zipfile
from pathlib import Path
from unittest.mock import patch

from decision_memory.requirements import assemble_requirement, discover_requirements, load_requirement_manifest
from decision_memory.sources import index_materials


class RequirementSourceTests(unittest.TestCase):
    def fixture(self, root):
        for name, content in {
            'analysis-dialogue.md': '**用户：**\n失败最多尝试 3 次。\n',
            'analysis-artifact.md': '# 分析\n失败重试。\n',
            'coding-dialogue.md': '**用户：**\n主链路不再重试。\n',
            'coding-artifact.md': '# 实现确认\n失败立即返回。\n',
            'gold.json': '[{"text":"不能传给提取器的参考"}]',
        }.items():
            (root / name).write_text(content, encoding='utf-8')
        value = {'schema_version': 1, 'requirement_id': 'DEMO-1', 'nodes': [
            {'id': 'N1', 'stage': 'analysis', 'dialogue': 'analysis-dialogue.md', 'artifact': 'analysis-artifact.md'},
            {'id': 'N2', 'stage': 'coding', 'dialogue': 'coding-dialogue.md', 'artifact': 'coding-artifact.md'}]}
        path = root / 'manifest.json'
        path.write_text(json.dumps(value, ensure_ascii=False))
        return path, value

    def test_nodes_keep_unique_source_ids_roles_and_original_positions(self):
        with tempfile.TemporaryDirectory() as directory:
            path, _ = self.fixture(Path(directory))
            sample = load_requirement_manifest(path)
        first, last = sample.catalog['D.N1.00002'], sample.catalog['D.N2.00002']
        self.assertEqual((first.file, first.line, first.role), ('analysis-dialogue.md', 2, 'user'))
        self.assertEqual((last.file, last.line, last.text), ('coding-dialogue.md', 2, '主链路不再重试。'))
        self.assertEqual(sample.sample_id, 'DEMO-1')
        self.assertEqual(sample.stage, 'requirement')
        self.assertEqual(sample.node_order('D.N2.00002'), 1)
        prompt = json.loads(sample.prompt_text())
        self.assertEqual([node['id'] for node in prompt['nodes']], ['N1', 'N2'])
        self.assertEqual([node['stage'] for node in prompt['nodes']], ['analysis', 'coding'])
        self.assertNotIn('不能传给提取器的参考', sample.prompt_text())

    def test_node_order_changes_the_frozen_input(self):
        with tempfile.TemporaryDirectory() as directory:
            path, value = self.fixture(Path(directory))
            first = load_requirement_manifest(path)
            value['nodes'].reverse()
            path.write_text(json.dumps(value))
            second = load_requirement_manifest(path)
        self.assertNotEqual(first.fingerprint, second.fingerprint)
        self.assertEqual(second.node_order('D.N2.00002'), 0)

    def test_duplicate_nodes_unknown_keys_and_empty_nodes_fail(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            for change in ('duplicate', 'extra', 'node_extra', 'empty'):
                with self.subTest(change=change), self.assertRaises(ValueError):
                    path, value = self.fixture(root)
                    if change == 'duplicate':
                        value['nodes'][1]['id'] = 'N1'
                    elif change == 'extra':
                        value['gold'] = 'gold.json'
                    elif change == 'node_extra':
                        value['nodes'][0]['reference'] = 'gold.json'
                    else:
                        value['nodes'] = []
                    path.write_text(json.dumps(value))
                    load_requirement_manifest(path)

    def test_parent_paths_absolute_paths_and_symlinks_cannot_escape(self):
        with tempfile.TemporaryDirectory() as directory:
            parent = Path(directory)
            root = parent / 'inputs'
            root.mkdir()
            outside = parent / 'outside.md'
            outside.write_text('外部材料')
            (root / 'link.md').symlink_to(outside)
            for target in ('../outside.md', str(outside), 'link.md'):
                with self.subTest(target=target), self.assertRaises(ValueError):
                    path, value = self.fixture(root)
                    value['nodes'][0]['dialogue'] = target
                    path.write_text(json.dumps(value))
                    load_requirement_manifest(path)

    def test_material_change_changes_fingerprint(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.fixture(root)
            old = load_requirement_manifest(path)
            (root / 'coding-dialogue.md').write_text('**用户：**\n失败后仍重试。\n')
            new = load_requirement_manifest(path)
        self.assertNotEqual(old.fingerprint, new.fingerprint)

    def test_size_limits_reject_a_large_material_or_requirement(self):
        with tempfile.TemporaryDirectory() as directory:
            root = Path(directory)
            path, _ = self.fixture(root)
            (root / 'analysis-dialogue.md').write_bytes(b'x' * 2_000_001)
            with self.assertRaisesRegex(ValueError, '2 MB'):
                load_requirement_manifest(path)
        sample = index_materials('x' * 1_000_001, 'y' * 1_000_000, 'analysis', 'N', 'd.md', 'a.md')
        nodes = [(f'N{i}', 'analysis', sample) for i in range(4)]
        with self.assertRaisesRegex(ValueError, '8 MB'):
            assemble_requirement('DEMO', nodes)

    def test_node_limit_is_checked_before_opening_materials(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps({'schema_version': 1, 'requirement_id': 'DEMO', 'nodes': [
                {'id': f'N{i}', 'stage': 'test', 'dialogue': 'missing.md', 'artifact': 'missing2.md'}
                for i in range(65)]}))
            with self.assertRaisesRegex(ValueError, '64'):
                load_requirement_manifest(path)

    def test_total_size_limit_stops_reading_later_nodes(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'manifest.json'
            path.write_text(json.dumps({'schema_version': 1, 'requirement_id': 'DEMO', 'nodes': [
                {'id': f'N{i}', 'stage': 'test', 'dialogue': f'd{i}.md', 'artifact': f'a{i}.md'}
                for i in range(5)]}))
            opened = []

            def read_material(target):
                opened.append(target.name)
                if len(opened) > 8:
                    self.fail('读入已超限的材料后，仍继续读取后续节点')
                return 'x' * 1_000_001

            with patch('decision_memory.requirements.read_text', read_material):
                with self.assertRaisesRegex(ValueError, '8 MB'):
                    load_requirement_manifest(path)

    def test_selected_dataset_requirement_does_not_read_other_inputs_or_annotations(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'samples.zip'
            with zipfile.ZipFile(path, 'w') as archive:
                for name in ('REQ-one', 'REQ-two'):
                    prefix = 'rules/requirements/' + name
                    for filename in ('需求确认记录.md', '技术设计确认记录.md'):
                        archive.writestr(prefix + '/上下文/' + filename, '**用户：**\n规则已确认。')
                    for filename in ('需求分析.md', '技术方案.md'):
                        archive.writestr(prefix + '/产物/' + filename, '规则已确认。')
                archive.writestr('rules/requirements/评测标注/应被沉淀知识标注.md', '不能读取此参考')
            real_read = zipfile.ZipFile.read

            def guarded_read(archive, name, *args, **kwargs):
                self.assertIn('/REQ-one/', name)
                return real_read(archive, name, *args, **kwargs)

            with patch.object(zipfile.ZipFile, 'read', guarded_read):
                samples = discover_requirements(path, 'REQ-one')
        self.assertEqual(len(samples), 1)
        self.assertEqual([node['stage'] for node in samples[0].nodes], ['analysis', 'design'])
        self.assertIn('A.design.00001', samples[0].catalog)


if __name__ == '__main__':
    unittest.main()
