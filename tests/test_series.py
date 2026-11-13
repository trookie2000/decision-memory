"""Offline fixtures verify batch engineering, never live employee/model quality."""

import importlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.collaboration import BattleConfig
from decision_memory.coordinator import Battle, run_lock
from http_fixture import completion, model_server
from test_coordinator import FixtureDocs, FixtureMario
from test_validation import valid_payload


class SeriesTests(unittest.TestCase):
    def setUp(self):
        self.assertIsNotNone(importlib.util.find_spec('decision_memory.series'), 'continuous batch controller missing')
        self.Series = importlib.import_module('decision_memory.series').Series
        self.temp = tempfile.TemporaryDirectory()
        self.addCleanup(self.temp.cleanup)
        self.root = Path(self.temp.name) / 'series'
        self.model = Path(self.temp.name) / 'model.toml'
        self.docs = FixtureDocs()
        self.mario = FixtureMario(self.docs)
        self.config = BattleConfig(worker_timeout=30, poll_interval=1, notification_max_bytes=60000)
        self.cases = [{'sample_id': 'N004', 'stage': 'analysis', 'brief': 'First fictional fixture'},
                      {'sample_id': 'N005', 'stage': 'design', 'brief': 'Second fictional fixture'}]
        events = patch.object(Battle, 'event')
        events.start()
        self.addCleanup(events.stop)

    def dependencies(self):
        return {'battle_dependencies': {'docs': self.docs, 'mario_factory': lambda config: self.mario},
                'preflight': lambda *args: {'fixture_only': True}, 'sleep': lambda seconds: None}

    def model_config(self, endpoint='https://example.invalid/chat/completions', key=''):
        self.model.write_text(f'[model]\nendpoint="{endpoint}"\nmodel="fixture"\napi_key_env="{key}"\nretries=0\n')

    def new(self, **extra):
        return self.Series.create(self.root, self.config, self.model, self.cases,
                                  max_seconds=60, **self.dependencies(), **extra)

    def test_two_cases_advance_and_completed_resume_repeats_no_calls(self):
        with model_server([(200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config(endpoint)
            series = self.new()
            result = series.run()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['finished'], 2)
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(self.mario.sent), 6)
            self.assertTrue(all(sent['thread'] is None for sent in self.mario.sent))
            self.Series(self.root, **self.dependencies()).run(resume=True)
            self.assertEqual(len(requests), 2)
            self.assertEqual(len(self.mario.sent), 6)
            self.assertNotIn('GOLD_ONLY_SENTINEL', json.dumps(requests, ensure_ascii=False))
            for case in self.cases:
                case_run = Battle(self.root / 'cases' / case['sample_id'])
                self.assertEqual(case_run.audit()['status'], 'agent_review_complete')
        self.assertTrue((self.root / 'report.md').exists())

    def test_missing_key_blocks_before_any_remote_write(self):
        self.model_config(key='SERIES_TEST_ABSENT_CREDENTIAL')
        deps = self.dependencies()
        deps.pop('preflight')
        series = self.Series.create(self.root, self.config, self.model, self.cases, **deps)
        with patch.dict('os.environ', {}, clear=True):
            result = series.run()
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('SERIES_TEST_ABSENT_CREDENTIAL', result['last_error'])
        self.assertEqual(len(self.docs.creates), 0)
        self.assertEqual(len(self.mario.sent), 0)
        self.assertFalse((self.root / 'cases').exists())

    def test_stop_in_flight_then_resume_does_not_resend_author(self):
        with model_server([(200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, _):
            self.model_config(endpoint)
            self.mario.complete_immediately = False
            series = self.new()
            original_fetch = self.docs.fetch

            def stop_on_poll(doc_id):
                if self.mario.sent:
                    series.request_stop()
                return original_fetch(doc_id)

            self.docs.fetch = stop_on_poll
            self.assertEqual(series.run()['status'], 'stopped')
            self.assertEqual(len(self.mario.sent), 1)
            self.docs.fetch = original_fetch
            self.mario.finish_last()
            self.mario.complete_immediately = True
            recovered = self.Series(self.root, **self.dependencies())
            self.assertEqual(recovered.run(resume=True)['status'], 'completed')
        self.assertEqual(len(self.mario.sent), 6)

    def test_manifest_or_config_edit_is_rejected(self):
        self.model_config()
        self.new()
        path = self.root / 'manifest.json'
        value = json.loads(path.read_text())
        value[0]['brief'] = 'changed'
        path.write_text(json.dumps(value))
        recovered = self.Series(self.root, **self.dependencies())
        self.assertEqual(recovered.run()['status'], 'blocked')
        self.assertIn('清单', recovered.summary()['last_error'])
        self.assertEqual(len(self.mario.sent), 0)

    def test_unknown_delivery_blocks_and_resume_does_not_resend(self):
        self.model_config()
        self.mario.complete_immediately = False
        self.mario.receipt = lambda: {'_meta': {'threadId': 'fixture'}, 'content': [{'type': 'text', 'text': '!!!'}]}
        result = self.new().run()
        self.assertEqual(result['status'], 'blocked')
        self.assertIn('派发', result['last_error'])
        self.Series(self.root, **self.dependencies()).run(resume=True)
        self.assertEqual(len(self.mario.sent), 1)

    def test_run_lock_prevents_two_controllers(self):
        self.model_config()
        series = self.new()
        with run_lock(self.root):
            with self.assertRaisesRegex(ValueError, '正在运行'):
                series.run()

    def test_detached_launch_has_no_key_in_arguments(self):
        self.model_config()
        series = self.new()
        with patch('decision_memory.series.subprocess.Popen') as popen:
            popen.return_value.pid = 4321
            result = series.launch()
        self.assertEqual(result['pid'], 4321)
        args, kwargs = popen.call_args
        self.assertIn('series', args[0])
        self.assertIn('run', args[0])
        self.assertTrue(kwargs['start_new_session'])
        self.assertNotIn('env', kwargs)

    def test_cli_exposes_background_start_status_stop_and_resume(self):
        from decision_memory.cli import parser
        command = parser()
        choices = command._subparsers._group_actions[0].choices
        self.assertIn('series', choices, 'batch CLI entry missing')
        start = command.parse_args(['series', 'start', '--config', 'battle.example.toml',
                                    '--model-config', 'config.local.toml', '--manifest', 'examples/continuous-cases.json',
                                    '--output', 'runs/new-series'])
        self.assertFalse(start.foreground)
        for action in ('status', 'stop', 'resume'):
            self.assertEqual(command.parse_args(['series', action, '--output', 'runs/new-series']).series_command, action)

    def test_config_change_mid_batch_blocks_before_next_send_and_controls_still_work(self):
        with model_server([(200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config(endpoint)
            original_config = self.model.read_text()
            original_dispatch = self.mario.dispatch

            def change_config(*args):
                receipt = original_dispatch(*args)
                if len(self.mario.sent) == 1:
                    self.model.write_text(original_config.replace('model="fixture"', 'model="changed-model"'))
                return receipt

            self.mario.dispatch = change_config
            series = self.new()
            self.assertEqual(series.run()['status'], 'blocked')
            self.assertEqual(len(self.mario.sent), 1)
            self.assertEqual(len(requests), 0)
            recovered = self.Series(self.root, **self.dependencies())
            self.assertIn('配置', recovered.summary()['last_error'])
            self.assertTrue(recovered.request_stop()['stop_requested'])
            self.model.write_text(original_config)
            self.assertEqual(recovered.run(resume=True)['status'], 'completed')
            self.assertTrue(all(request['model'] == 'fixture' for request in requests))

    def test_stop_after_resume_launch_is_not_erased_by_child(self):
        from decision_memory.cli import parser
        self.model_config()
        series = self.new()
        series.request_stop()
        with patch('decision_memory.series.subprocess.Popen') as popen:
            popen.return_value.pid = 4321
            series.launch(resume=True)
        child = parser().parse_args(popen.call_args.args[0][3:])
        series.request_stop()
        result = self.Series(self.root, **self.dependencies()).run(resume=child.resume)
        self.assertEqual(result['status'], 'stopped')
        self.assertEqual(len(self.mario.sent), 0)

    def test_structurally_invalid_dispatch_receipt_stays_blocked_on_resume(self):
        self.model_config()
        self.mario.complete_immediately = False
        self.mario.receipt = lambda: {'_meta': None, 'content': []}
        self.assertEqual(self.new().run()['status'], 'blocked')
        recovered = self.Series(self.root, **self.dependencies())
        self.assertEqual(recovered.run(resume=True)['status'], 'blocked')
        self.assertEqual(len(self.mario.sent), 1)

    def test_resume_scores_existing_prediction_then_checks_next_case_credentials(self):
        from decision_memory.client import ModelError
        from decision_memory.storage import read_json, write_json
        from decision_memory.evaluation import digest
        with model_server([(200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config(endpoint)
            series = self.new()
            case = self.cases[0]
            folder = self.root / 'cases' / case['sample_id']
            battle = Battle.create(folder, series.config, case['sample_id'], case['stage'], case['brief'],
                                   model_config=self.model, mode='baseline', **self.dependencies()['battle_dependencies'])
            battle.run(0)
            battle.run(0)
            battle.run(0)  # independent extraction is already saved
            self.assertEqual(battle.state['phase'], 'scorer')
            predicted = (folder / battle.state['prediction']).read_bytes()
            before_calls = len(requests)
            checked = []

            def missing_key(*args):
                checked.append(True)
                raise ModelError('fixture missing credential')

            deps = self.dependencies()
            deps['preflight'] = missing_key
            resumed = self.Series(self.root, **deps)
            result = resumed.run(resume=True)
            self.assertEqual(result['finished'], 1)
            self.assertEqual(result['current_case'], self.cases[1]['sample_id'])
            self.assertEqual(result['status'], 'blocked')
            self.assertEqual(result['cases'][0]['status'], 'agent_review_complete')
            self.assertEqual(result['cases'][1]['phase'], 'preflight')
            self.assertEqual(len(self.mario.sent), 3)
            self.assertEqual(len(requests), before_calls)
            self.assertEqual((folder / battle.state['prediction']).read_bytes(), predicted)
            self.assertEqual(len(checked), 1)
            self.assertFalse((self.root / 'cases' / self.cases[1]['sample_id']).exists())

    def test_default_short_notifications_advance_two_cases_via_docs(self):
        from dataclasses import replace
        self.config = replace(self.config, notification_max_bytes=4000)
        with model_server([(200, completion(valid_payload())), (200, completion(valid_payload()))]) as (endpoint, requests):
            self.model_config(endpoint)
            series = self.new()
            result = series.run()
            self.assertEqual(result['status'], 'completed')
            self.assertEqual(result['finished'], 2)
            self.assertEqual(len(requests), 2)
            self.assertTrue(all(len(sent['message'].encode('utf-8')) + 64 <= 4000 for sent in self.mario.sent))
            self.assertTrue(any(sent.get('task_input_digest') for sent in self.mario.sent))
            for case in self.cases:
                self.assertEqual(Battle(self.root / 'cases' / case['sample_id']).audit()['status'], 'agent_review_complete')


if __name__ == '__main__':
    unittest.main()
