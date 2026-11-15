import copy
import unittest
from dataclasses import replace

from decision_memory.requirement_pipeline import extract_requirement, validate_requirement_saved
from decision_memory.storage import result_digest
from test_requirement_memory import added, evidence, item, requirement, updated


class Client:
    def __init__(self, replies):
        self.replies, self.prompts, self.calls = list(replies), [], []
        self.config = type('Config', (), {'model': 'test-model', 'public': lambda _: {'model': 'test-model'}})()

    def complete(self, system, user):
        self.prompts.append((system, user))
        self.calls.append({'number': len(self.prompts)})
        return copy.deepcopy(self.replies.pop(0))

    def metrics(self):
        return {'calls': len(self.calls)}


class RequirementPipelineTests(unittest.TestCase):
    def test_verified_reviews_the_sources_and_saves_only_final_replayed_events(self):
        first = {'events': [added()], 'open_questions': []}
        final = {'events': [added(), updated()], 'open_questions': [], 'review_notes': ['后续节点更新了原策略。']}
        client = Client([first, final])
        result = extract_requirement(requirement(), client, 'verified')
        self.assertEqual(result['items'][0]['conclusion']['text'], '主链路不重试')
        self.assertEqual(result['scope'], 'requirement')
        self.assertEqual(result['requirement_id'], 'DEMO-RETRY')
        self.assertEqual(result['metrics']['calls'], 2)
        self.assertIn('D.N2.00002', client.prompts[1][1])
        self.assertEqual(len(result['events']), 2)

    def test_artifact_only_never_sends_dialogue_and_rejects_dialogue_citations(self):
        event = added()
        event['item'] = item(source='A.N1.00001')
        client = Client([{'events': [event], 'open_questions': []}])
        result = extract_requirement(requirement(), client, 'artifact-only')
        self.assertNotIn('报警范围待确认', client.prompts[0][1])
        self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['source_id'], 'A.N1.00001')
        invalid = Client([{'events': [added()], 'open_questions': []}] * 2)
        with self.assertRaises(ValueError):
            extract_requirement(requirement(), invalid, 'artifact-only')

    def test_saved_results_replay_and_detect_tampering_even_after_rehash(self):
        sample = requirement()
        result = extract_requirement(sample, Client([{'events': [added(), updated()], 'open_questions': []}]), 'baseline')
        result['result_digest'] = result_digest(result)
        self.assertEqual(validate_requirement_saved(result, sample), result)
        modified = copy.deepcopy(result)
        modified['items'][0]['conclusion']['text'] = '失败最多尝试 3 次'
        modified['result_digest'] = result_digest(modified)
        with self.assertRaises(ValueError):
            validate_requirement_saved(modified, sample)
        changed = replace(sample, fingerprint='different-node-order')
        with self.assertRaises(ValueError):
            validate_requirement_saved(result, changed)


if __name__ == '__main__':
    unittest.main()
