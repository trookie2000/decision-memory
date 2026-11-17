"""URI templates are complete path identifiers, not their prefix before {."""
import unittest
from decision_memory.field_grounding import symbols
from decision_memory.grounded import decode_payload
from decision_memory.sources import index_materials


def payload(path):
    return {'items': [{'conclusion': {'text': '调用 ' + path + ' 查询状态。', 'evidence': ['A00001']},
                      'reason': {'text': '未说明', 'evidence': []},
                      'boundary': {'text': '未说明', 'evidence': []}}], 'open_questions': []}


class UriTemplateTests(unittest.TestCase):
    def test_complete_template_is_not_truncated_before_its_parameter(self):
        path = '/api/v1/tasks/{taskId}/inspect'
        self.assertEqual(symbols('调用 ' + path), [path])
        sample = index_materials('**用户：**\n沿用当前接口。', '`' + path + '` 查询状态。', 'design', 'URI', 'd', 'a')
        self.assertEqual(decode_payload(payload(path), sample)['items'][0]['conclusion']['text'], '调用 ' + path + ' 查询状态。')

    def test_template_parameter_name_and_complete_path_must_still_match(self):
        path = '/api/v1/tasks/{taskId}/inspect'
        for wrong in ('/api/v1/tasks/{otherId}/inspect', path + '/child', '/prefix' + path):
            with self.subTest(wrong=wrong):
                sample = index_materials('**用户：**\n沿用当前接口。', '`' + wrong + '` 查询状态。', 'design', 'URI', 'd', 'a')
                with self.assertRaises(ValueError):
                    decode_payload(payload(path), sample)


if __name__ == '__main__':
    unittest.main()
