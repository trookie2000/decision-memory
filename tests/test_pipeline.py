import unittest

from decision_memory.client import Config, ModelClient, ModelError
from decision_memory.pipeline import extract
from decision_memory.sources import make_sample
from http_fixture import completion, model_server
from test_validation import valid_payload


class PipelineTests(unittest.TestCase):
    def setUp(self):
        self.sample = make_sample('门槛改成 120 元', '本次活动门槛为 120 元', 'analysis')

    def client(self, endpoint):
        return ModelClient(Config(endpoint=endpoint, model='fixture', api_key_env='', retries=1))

    def test_real_http_baseline_returns_validated_result_and_usage(self):
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'baseline')
        self.assertEqual(result['items'][0]['conclusion']['evidence'][0]['line'], 1)
        self.assertEqual(result['metrics']['prompt_tokens'], 100)
        self.assertEqual(len(requests), 1)
        self.assertIn('门槛改成 120', requests[0]['messages'][1]['content'])

    def test_429_retries_but_auth_error_does_not(self):
        with model_server([(429, {}), (200, completion(valid_payload()))]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'baseline')
        self.assertEqual(len(requests), 2)
        self.assertEqual(result['metrics']['http_attempts'], 2)
        with model_server([(401, {'secret': 'do not log'}), (200, completion(valid_payload()))]) as (endpoint, requests):
            with self.assertRaises(ModelError) as error:
                extract(self.sample, self.client(endpoint), 'baseline')
        self.assertNotIn('do not log', str(error.exception))
        self.assertEqual(len(requests), 1)

    def test_invalid_json_gets_one_repair_and_keeps_original_usage(self):
        with model_server([(200, completion('broken')), (200, completion(valid_payload()))]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'baseline')
        self.assertEqual(result['metrics']['prompt_tokens'], 200)
        self.assertEqual(len(requests), 2)

    def test_unsupported_quote_fails_after_one_repair(self):
        bad = valid_payload()
        bad['items'][0]['conclusion']['evidence'][0]['quote'] = '999 元'
        with model_server([(200, completion(bad)), (200, completion(bad))]) as (endpoint, requests):
            with self.assertRaisesRegex(ValueError, '引文'):
                extract(self.sample, self.client(endpoint), 'baseline')
        self.assertEqual(len(requests), 2)

    def test_verified_pipeline_preserves_superseded_audit_and_checks_full_input(self):
        decisions = {'decisions': [{'topic': '门槛', 'status': 'active', 'text': '采用120元',
                                  'evidence': [{'source_id': 'D00001', 'quote': '120 元'}]}]}
        reviewed = {**valid_payload(), 'review_notes': ['已核验']}
        with model_server([(200, completion(decisions)), (200, completion(valid_payload())),
                           (200, completion(reviewed))]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'verified')
        self.assertEqual(len(requests), 3)
        self.assertEqual(result['review_notes'], ['已核验'])
        self.assertEqual(result['decisions'][0]['status'], 'active')
        self.assertIn('门槛改成 120', requests[-1]['messages'][1]['content'])

    def test_artifact_only_cannot_cite_dialogue(self):
        with model_server([(200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            with self.assertRaisesRegex(ValueError, '来源'):
                extract(self.sample, self.client(endpoint), 'artifact-only')
        self.assertNotIn('门槛改成', requests[0]['messages'][1]['content'])

    def test_non_object_usage_is_a_controlled_protocol_error(self):
        invalid = completion(valid_payload())
        invalid['usage'] = [1]
        with model_server([(200, invalid), (200, invalid)]) as (endpoint, requests):
            with self.assertRaisesRegex(ValueError, '协议'):
                extract(self.sample, self.client(endpoint), 'baseline')

    def test_unknown_usage_stays_unknown_in_metrics(self):
        response = completion(valid_payload())
        response.pop('usage')
        with model_server([(200, response)]) as (endpoint, requests):
            result = extract(self.sample, self.client(endpoint), 'baseline')
        self.assertIsNone(result['metrics']['prompt_tokens'])
        self.assertIsNone(result['metrics']['estimated_cost'])
        self.assertFalse(result['metrics']['usage_complete'])

    def test_interrupted_http_body_is_retried_and_then_becomes_model_error(self):
        with model_server([(200, 'interrupted-body'), (200, 'interrupted-body')]) as (endpoint, requests):
            with self.assertRaises(ModelError):
                extract(self.sample, self.client(endpoint), 'baseline')
        self.assertEqual(len(requests), 2)

    def test_filtered_or_unfinished_response_cannot_be_successful_empty_result(self):
        for reason in ('content_filter', 'tool_calls', None):
            response = completion({'items': [], 'open_questions': []})
            response['choices'][0]['finish_reason'] = reason
            with self.subTest(reason=reason), model_server([(200, response)]) as (endpoint, requests):
                with self.assertRaises(ModelError):
                    extract(self.sample, self.client(endpoint), 'baseline')


if __name__ == '__main__':
    unittest.main()
