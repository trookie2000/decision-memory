import hashlib
import json
import tempfile
import unittest
from pathlib import Path
from unittest.mock import patch

from decision_memory.collaboration import BattleConfig, DocsCLI, MarioMCP, TransportError, parse_sse, receipt_details
from http_fixture import model_server


class CollaborationTests(unittest.TestCase):
    def test_full_base64_message_key_and_authoritative_thread_meta(self):
        receipt = {'_meta': {'threadId': 'mario-thread'}, 'content': [{'type': 'text',
                    'text': 'messageKey: `OMSG_ab/+/==` threadId: wrong-group-id'}]}
        details = receipt_details(receipt)
        self.assertEqual(details['thread_id'], 'mario-thread')
        self.assertEqual(details['message_keys'], ['OMSG_ab/+/=='])

    def test_real_http_mcp_discovery_then_single_dispatch(self):
        responses = [(200, {'jsonrpc': '2.0', 'id': 1, 'result': {}}), (200, {}),
                     (200, {'jsonrpc': '2.0', 'id': 3, 'result': {'tools': [
                         {'name': 'mario_list_skills'}, {'name': 'mario_run_skill_task'}]}}),
                     (200, {'jsonrpc': '2.0', 'id': 4, 'result': {'_meta': {'skills': [
                         {'skillName': 'kim-skill-assistant-cc/codex', 'skillId': 'discovered', 'source': 'user'}]}}}),
                     (200, {'jsonrpc': '2.0', 'id': 5, 'result': {'_meta': {'threadId': 'actual-thread'},
                         'content': [{'type': 'text', 'text': 'messageKey: OMSG_fixture'}]}})]
        with model_server(responses) as (endpoint, requests):
            mcp = MarioMCP(BattleConfig(mcp_url=endpoint))
            skill_id = mcp.discover()['skill_id']
            receipt = mcp.dispatch(skill_id, 'previous-thread', '10120718088572', 'fixture request')
        args = requests[-1]['params']['arguments']
        self.assertEqual(args['threadId'], 'previous-thread')
        self.assertEqual(args['skillId'], 'discovered')
        self.assertNotIn('agentId', args)
        self.assertIn('<@=userId(10120718088572)=>', args['instruction'])
        self.assertEqual(len(requests), 5)
        self.assertEqual(receipt_details(receipt)['message_keys'], ['OMSG_fixture'])

    def test_sse_matches_rpc_id_and_multiline_data(self):
        body = ': ping\n\ndata: {"jsonrpc":"2.0","method":"notifications/progress"}\n\n'
        body += 'data: {"id": 8,\ndata: "result": {"ok": true}}\n\n'
        self.assertEqual(parse_sse(body, 8), {'ok': True})
        self.assertIsNone(parse_sse(body, 7))
        with self.assertRaisesRegex(TransportError, 'bad_request'):
            parse_sse('data: {"id":8,"error":{"code":1,"message":"bad_request"}}\n\n', 8)

    def test_docs_capture_hash_and_business_error_are_checked(self):
        with tempfile.TemporaryDirectory() as directory:
            path = Path(directory) / 'stdout.raw'
            raw = json.dumps({'content': '正文'}).encode()
            path.write_bytes(raw)
            manifest = {'ok': True, 'capture': {'stdoutPath': str(path), 'exitCode': 0,
                                               'stdoutSha256': hashlib.sha256(raw).hexdigest()}}
            docs = DocsCLI(BattleConfig())
            with patch.object(docs, 'execute', return_value=manifest) as execute:
                self.assertEqual(docs.fetch('known-id')['content'], '正文')
                self.assertIn('word.fetch', execute.call_args.args[0])
                self.assertIn('--format', execute.call_args.args[0])
                path.write_text('changed')
                with self.assertRaisesRegex(TransportError, '哈希'):
                    docs.fetch('known-id')

    def test_created_document_requires_matching_real_id_and_url(self):
        with self.assertRaisesRegex(TransportError, 'URL'):
            DocsCLI.document({'docId': 'known'})
        with self.assertRaisesRegex(TransportError, '不匹配'):
            DocsCLI.document({'docId': 'known', 'openDocUrl': 'https://docs.corp.kuaishou.com/d/home/other'})

    def test_docs_uses_current_public_cli_and_leaves_sharing_defaults(self):
        docs = DocsCLI(BattleConfig())
        with patch.object(docs, 'execute', return_value={'ok': True}) as execute:
            docs.create('测试标题', Path('/tmp/fixture.md'))
            command = execute.call_args.args[0]
        self.assertEqual(command[:4], ['kcli', 'docs-cli', 'word', '+create'])
        self.assertNotIn('--link-share', command)
        self.assertNotIn('--classify-level', command)


if __name__ == '__main__':
    unittest.main()
