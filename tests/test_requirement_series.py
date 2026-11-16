import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.collaboration import BattleConfig
from decision_memory.coordinator import Battle
from decision_memory.series import Series
from decision_memory.storage import read_json
from http_fixture import completion, model_server
from test_coordinator import FixtureDocs
from test_requirement_battle import RequirementMario
from test_requirement_comparison import replies


class RequirementSeriesTests(unittest.TestCase):
    def setUp(self):
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'series'
        self.model = Path(self.temp.name) / 'model.toml'
        self.docs = FixtureDocs()
        self.mario = RequirementMario(self.docs)
        self.cases = [{'sample_id': name, 'stage': 'requirement', 'brief': 'fixture only'} for name in ('N1', 'N2')]
        event = patch.object(Battle, 'event')
        event.start()
        self.addCleanup(event.stop)

    def deps(self, extractor=None):
        deps = {'docs': self.docs, 'mario_factory': lambda config: self.mario}
        if extractor:
            deps['extractor'] = extractor
        return {'battle_dependencies': deps, 'preflight': lambda *args: {'fixture_only': True}, 'sleep': lambda _: None}

    def test_all_inputs_freeze_before_any_extraction_and_resume_is_read_only_for_done_cases(self):
        checked = []

        def extractor(*args):
            gate = read_json(self.root / 'frozen-batch.json')
            self.assertEqual(gate['case_ids'], ['N1', 'N2'])
            for name in ('N1', 'N2'):
                self.assertTrue(read_json(self.root / 'cases' / name / 'state.json')['frozen'])
            checked.append(args[0])
            return Battle._extract_process(*args)

        with model_server([(200, completion(value)) for value in replies() + replies()]) as (endpoint, requests):
            self.model.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            series = Series.create(self.root, BattleConfig(), self.model, self.cases, max_seconds=60,
                                   **self.deps(extractor))
            result = series.run()
            self.assertEqual(result['status'], 'completed', result)
            self.assertEqual((result['prepared'], result['finished']), (2, 2))
            self.assertEqual(len(checked), 2)
            self.assertEqual(len(requests), 6)
            methods = result['comparison_summary']['methods']
            self.assertEqual(methods['whole']['precision'], 1)
            self.assertEqual(methods['per_node']['precision'], 0.5)
            self.assertEqual(methods['whole']['uncertainty_coverage'], 0)
            self.assertEqual(methods['per_node']['uncertainty_coverage'], 1)
            Series(self.root, **self.deps(extractor)).run(resume=True)
            self.assertEqual(len(requests), 6)
        roles = [(s['identity']['sample_id'], s['identity']['role']) for s in self.mario.sent]
        self.assertLess(roles.index(('N2', 'reviewer')), roles.index(('N1', 'scorer')))

    def test_disputed_sample_prevents_all_paid_extraction_including_already_prepared_case(self):
        self.mario.disputed = 'N2'
        with model_server([]) as (endpoint, requests):
            self.model.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
            series = Series.create(self.root, BattleConfig(), self.model, self.cases, **self.deps())
            value = series.run()
            self.assertEqual(value['status'], 'blocked')
            self.assertEqual(value['finished'], 0)
            self.assertEqual(requests, [])
            self.assertFalse((self.root / 'frozen-batch.json').exists())

    def paused_after_freeze(self):
        self.model.write_text('[model]\nendpoint="https://example.invalid/chat/completions"\nmodel="fixture"\napi_key_env=""\nretries=0\n')
        def paused(*args):
            return {'exit_code': 2, 'stderr': 'fixture stop before an actual model request', 'stdout': ''}
        deps = self.deps(paused)
        series = Series.create(self.root, BattleConfig(), self.model, self.cases, **deps)
        self.assertEqual(series.run()['status'], 'blocked')
        self.assertTrue((self.root / 'frozen-batch.json').exists())
        return Battle(self.root / 'cases/N1', **deps['battle_dependencies'])

    def test_direct_resume_rechecks_the_other_cases_reference_policy(self):
        battle = self.paused_after_freeze()
        path = self.root / 'cases/N2/scoring/reference-policy.json'
        policy = read_json(path)
        policy['assignments'][1]['kind'] = 'decision'
        path.write_text(json.dumps(policy))
        with self.assertRaisesRegex(ValueError, '策略|冻结'):
            battle.run(0, retry_extraction=True)

    def test_direct_resume_rechecks_model_configuration(self):
        battle = self.paused_after_freeze()
        self.model.write_text(self.model.read_text() + '\n# changed experiment config\n')
        with self.assertRaisesRegex(ValueError, '配置'):
            battle.run(0, retry_extraction=True)

    def test_direct_resume_rechecks_implementation_snapshot(self):
        battle = self.paused_after_freeze()
        with patch('decision_memory.batch_gate.implementation_signature', return_value={}):
            with self.assertRaisesRegex(ValueError, '实现|提示词'):
                battle.run(0, retry_extraction=True)


if __name__ == '__main__':
    unittest.main()
