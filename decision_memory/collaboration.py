"""Real Mario MCP and public Docs CLI transports (no employee emulation)."""

import hashlib
import json
import re
import subprocess
import time
import tomllib
import urllib.request
from dataclasses import asdict, dataclass
from pathlib import Path
from urllib.parse import urlsplit

from .client import NoRedirect

GROUP_ID = '7419479552888854'
AUTHOR_ID = '10120718088572'
REVIEWER_ID = '10664440173316'
KIM_SKILL = 'kim-skill-assistant-cc/codex'
MAX_RESPONSE = 8_000_000


class TransportError(RuntimeError):
    """A failed request; a mutating request may already have taken effect."""


@dataclass
class BattleConfig:
    mcp_url: str = 'http://127.0.0.1:38399/mcp'
    ks_cli: str = 'ks-cli'
    node: str = 'node'
    capture_script: str = str(Path.home() / '.myflicker/internal/skills/docs-shuttle/references/docs-shared/scripts/docs-shared-capture.js')
    group_id: str = GROUP_ID
    author_id: str = AUTHOR_ID
    reviewer_id: str = REVIEWER_ID
    request_timeout: int = 150
    docs_timeout: int = 45
    worker_timeout: int = 600
    poll_interval: int = 10
    max_rounds: int = 3
    dispatch_context: str = 'task'
    # Conservative project policy, deliberately NOT a KIM API limit claim.
    notification_max_bytes: int = 4000
    # Use the current public Docs CLI. None is an explicit legacy adapter opt-in.
    docs_cli: str | None = 'kcli'

    def __post_init__(self):
        url = urlsplit(self.mcp_url)
        if (url.scheme != 'http' or url.hostname not in ('127.0.0.1', 'localhost', '::1')
                or url.username or url.password or url.query or url.fragment):
            raise ValueError('Mario MCP 必须是无凭证的本机 HTTP 地址')
        # Targets come from the operator's own config; only their shape is checked here.
        for name in ('group_id', 'author_id', 'reviewer_id'):
            value = getattr(self, name)
            if not isinstance(value, str) or not value.isdigit():
                raise ValueError(f'{name} 必须是 KIM 数字 ID')
        if self.author_id == self.reviewer_id:
            raise ValueError('出题人与评审人必须是两位不同的员工')
        if self.dispatch_context not in ('task', 'operation'):
            raise ValueError('dispatch_context 必须为 task 或 operation')
        for name, low, high in (('request_timeout', 1, 300), ('docs_timeout', 1, 120),
                                ('worker_timeout', 1, 3600), ('poll_interval', 1, 60), ('max_rounds', 1, 3),
                                ('notification_max_bytes', 1500, 60000)):
            value = getattr(self, name)
            if type(value) is not int or not low <= value <= high:
                raise ValueError(f'{name} 必须为 {low} 到 {high} 的整数')
        for name in ('ks_cli', 'node', 'capture_script'):
            if not isinstance(getattr(self, name), str) or not getattr(self, name).strip():
                raise ValueError(f'{name} 不能为空')
        if self.docs_cli is not None and (not isinstance(self.docs_cli, str) or not self.docs_cli.strip()):
            raise ValueError('docs_cli 必须是公开 CLI 路径或 None')

    @classmethod
    def load(cls, path):
        try:
            return cls(**tomllib.loads(Path(path).read_text(encoding='utf-8'))['battle'])
        except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
            raise ValueError('协调器配置格式错误，请对照 battle.example.toml') from error

    def public(self):
        return asdict(self)


def rpc_value(value, request_id):
    if not isinstance(value, dict) or value.get('id') != request_id:
        return None
    if 'error' in value:
        raise TransportError('Mario JSON-RPC 错误：' + json.dumps(value['error'], ensure_ascii=False)[:1200])
    if not isinstance(value.get('result'), dict):
        raise TransportError('Mario JSON-RPC 缺少对象 result')
    return value['result']


def parse_sse(text, request_id):
    """Handle multiline SSE data and ignore notifications/unrelated request IDs."""
    for event in re.split(r'\r?\n\r?\n', text):
        data = '\n'.join(line[5:].lstrip(' ') for line in event.splitlines() if line.startswith('data:'))
        if not data or data == '[DONE]':
            continue
        try:
            result = rpc_value(json.loads(data), request_id)
        except json.JSONDecodeError as error:
            raise TransportError('Mario SSE data 不是完整 JSON') from error
        if result is not None:
            return result
    return None


class MarioMCP:
    def __init__(self, config):
        self.config = config
        self.headers = {'Content-Type': 'application/json', 'Accept': 'application/json, text/event-stream'}
        self.sequence = 0
        self.opener = urllib.request.build_opener(NoRedirect)
        self.rpc('initialize', {'protocolVersion': '2024-11-05', 'capabilities': {},
                               'clientInfo': {'name': 'decision-memory-coordinator', 'version': '0.2.0'}})
        self.rpc('notifications/initialized', notification=True)

    def rpc(self, method, params=None, notification=False):
        self.sequence += 1
        body = {'jsonrpc': '2.0', 'method': method}
        if params is not None:
            body['params'] = params
        if not notification:
            body['id'] = self.sequence
        request = urllib.request.Request(self.config.mcp_url, data=json.dumps(body).encode(),
                                         headers=self.headers, method='POST')
        # Deliberately no retries: a timed-out tools/call may have sent a message.
        with self.opener.open(request, timeout=self.config.request_timeout) as response:
            if session := response.headers.get('Mcp-Session-Id'):
                self.headers['Mcp-Session-Id'] = session
            if notification:
                return {}
            if 'text/event-stream' in response.headers.get('Content-Type', ''):
                chunks, size = [], 0
                deadline = time.monotonic() + self.config.request_timeout
                while line := response.readline(MAX_RESPONSE + 1):
                    if time.monotonic() >= deadline:
                        raise TimeoutError('Mario SSE 总等待超过 request_timeout；不自动重发')
                    size += len(line)
                    if size > MAX_RESPONSE:
                        raise TransportError('Mario 响应超过 8 MB 上限')
                    chunks.append(line)
                    if not line.strip():
                        value = parse_sse(b''.join(chunks).decode('utf-8'), self.sequence)
                        if value is not None:
                            return value
                        chunks = []
                value = parse_sse(b''.join(chunks).decode('utf-8'), self.sequence)
            else:
                raw = response.read(MAX_RESPONSE + 1)
                if len(raw) > MAX_RESPONSE:
                    raise TransportError('Mario 响应超过 8 MB 上限')
                try:
                    value = rpc_value(json.loads(raw), self.sequence)
                except (ValueError, UnicodeDecodeError) as error:
                    raise TransportError('Mario 响应不是有效 JSON') from error
        if value is None:
            raise TransportError('Mario 未返回对应 request id 的结果')
        return value

    def discover(self):
        tools, cursor = [], None
        for _ in range(20):
            page = self.rpc('tools/list', {'cursor': cursor} if cursor else {})
            tools.extend(page.get('tools', []))
            cursor = page.get('nextCursor')
            if not cursor:
                break
        else:
            raise TransportError('Mario tools/list 分页超过上限')
        names = {item.get('name') for item in tools}
        if not {'mario_list_skills', 'mario_run_skill_task'} <= names:
            raise TransportError('Mario 缺少 mario_list_skills 或 mario_run_skill_task')
        result = self.rpc('tools/call', {'name': 'mario_list_skills', 'arguments': {}})
        if result.get('isError'):
            raise TransportError('Mario mario_list_skills 返回 isError=true')
        skills = result.get('_meta', {}).get('skills', [])
        candidates = [skill for skill in skills if skill.get('skillName') == KIM_SKILL
                      and skill.get('source', 'user') == 'user']
        if len(candidates) != 1 or not candidates[0].get('skillId'):
            raise TransportError('当前用户没有唯一的 ' + KIM_SKILL + ' 技能')
        return {'tools': tools, 'skills': skills, 'skill_id': candidates[0]['skillId']}

    def dispatch(self, skill_id, thread_id, employee_id, message):
        if employee_id not in (self.config.author_id, self.config.reviewer_id):
            raise ValueError('目标员工不在已授权范围')
        if len(message.encode('utf-8')) + 64 > self.config.notification_max_bytes:
            raise ValueError('KIM 通知超过本地保护限制；使用 Docs 任务输入，尚未发送')
        instruction = (f'你的唯一动作：向已授权测试群 {self.config.group_id} 发送一条消息，'
                       f'真实结构化 @ userId={employee_id}。严格逐字传递下列正文。'
                       '你只是派发助手，不是出题人或评审人，不得回答题目、生成答案或写结果文档。'
                       '禁止发到其他群或私聊；不要重复发送。返回真实 messageKey、工具回执和 threadId。\n正文：\n'
                       + f'<@=userId({employee_id})=>\n' + message)
        arguments = {'skillId': skill_id, 'instruction': instruction}
        if thread_id:
            arguments['threadId'] = thread_id
        return self.rpc('tools/call', {'name': 'mario_run_skill_task', 'arguments': arguments})


def receipt_details(receipt):
    if not isinstance(receipt, dict):
        raise TransportError('Mario 派发回执必须为对象')
    meta = receipt.get('_meta', {})
    content = receipt.get('content', [])
    if (not isinstance(meta, dict) or not isinstance(content, list)
            or any(not isinstance(block, dict) or
                   (block.get('type') == 'text' and not isinstance(block.get('text', ''), str)) for block in content)):
        raise TransportError('Mario 派发回执的 _meta/content 结构无效，保留原回执核对')
    texts = '\n'.join(block.get('text', '') for block in content if block.get('type') == 'text')
    # These are the dispatch assistant's receipts, not employee answers.
    return {'thread_id': meta.get('threadId'), 'task_id': meta.get('taskId'),
            'task_status': meta.get('taskStatus'), 'is_error': bool(receipt.get('isError')),
            'message_keys': sorted(set(re.findall(r'\bOMSG_[A-Za-z0-9_+/=-]+', texts)))}


def cli_error(value):
    error = value.get('error', {}) if isinstance(value, dict) else {}
    if isinstance(error, dict):
        return '；'.join(f'{key}={error[key]}' for key in ('type', 'subtype', 'code', 'source', 'stage', 'message', 'hint')
                        if error.get(key))[:2000]
    return str(error)[:1000]


def cli_payload(value):
    if not isinstance(value, dict):
        raise TransportError('Docs CLI 未返回 JSON 对象')
    if value.get('ok') is False or value.get('success') is False or value.get('error'):
        raise TransportError('Docs CLI：' + cli_error(value))
    value = value.get('data', value)
    if not isinstance(value, dict):
        raise TransportError('Docs CLI data 不是对象')
    return value


class DocsCLI:
    def __init__(self, config):
        self.config = config

    def execute(self, arguments):
        result = subprocess.run(arguments, capture_output=True, text=True, timeout=self.config.docs_timeout)
        try:
            value = json.loads(result.stdout)
        except ValueError as error:
            raise TransportError(f'Docs CLI exit={result.returncode}，输出不是 JSON：'
                                 + result.stderr[:1500]) from error
        if result.returncode:
            raise TransportError(f'Docs CLI exit={result.returncode}：' + cli_error(value) + result.stderr[:500])
        return value

    def create(self, title, content_path):
        # Do not set link-share, classify-level, folder-id, or permissions.
        command = ([self.config.docs_cli, 'docs-cli', 'word', '+create'] if self.config.docs_cli else
                   [self.config.ks_cli, 'doc', 'word', 'create'])
        return self.execute(command + ['--title', title, '--content-file', str(Path(content_path).resolve())]
                            + ([] if self.config.docs_cli else ['--json']))

    @staticmethod
    def document(receipt):
        value = cli_payload(receipt)
        value = value.get('result', value)
        doc_id = value.get('docId')
        url = value.get('openDocUrl') or value.get('docUrl') or value.get('url')
        if not isinstance(doc_id, str) or not re.fullmatch(r'[A-Za-z0-9_-]+', doc_id):
            raise TransportError('Docs 创建回执缺少有效 docId；不得自动重建')
        if not isinstance(url, str):
            raise TransportError('Docs 创建回执缺少实际 URL；需只读核对后接回，不能猜测或重建')
        target = urlsplit(url)
        if (target.scheme != 'https' or target.netloc != 'docs.corp.kuaishou.com'
                or target.path.rstrip('/').split('/')[-1] != doc_id):
            raise TransportError('Docs 回执 URL 与 docId 不匹配')
        return {'doc_id': doc_id, 'url': url}

    def fetch(self, doc_id):
        command = ([self.config.docs_cli, 'docs-cli', 'word', '+fetch'] if self.config.docs_cli else
                   [self.config.ks_cli, 'doc', 'word', 'fetch'])
        manifest = self.execute([self.config.node, self.config.capture_script, '--command-kind', 'word.fetch', '--',
                                 *command, '--doc-id', doc_id, '--format', 'raw']
                                + ([] if self.config.docs_cli else ['--json']))
        capture = manifest.get('capture')
        if not isinstance(capture, dict):
            raise TransportError('Docs capture：' + cli_error(manifest))
        path = Path(capture['stdoutPath'])
        if not path.is_absolute() or path.stat().st_size > MAX_RESPONSE:
            raise TransportError('Docs capture 路径或响应大小无效')
        raw = path.read_bytes()
        if hashlib.sha256(raw).hexdigest() != capture.get('stdoutSha256'):
            raise TransportError('Docs capture 原文哈希不匹配')
        if capture.get('exitCode') != 0 or manifest.get('ok') is not True:
            try:
                detail = cli_error(json.loads(raw))
            except ValueError:
                detail = '读取失败，详情见 capture 文件'
            if stderr_path := capture.get('stderrPath'):
                stderr = Path(stderr_path).read_text(encoding='utf-8', errors='replace')[:1500]
                # Keep the concrete first error line; do not replay stderr as commands.
                if stderr.strip():
                    detail += '；' + stderr.strip().splitlines()[0][:500]
            raise TransportError(f'Docs fetch exit={capture.get("exitCode")}：{detail}；capture={path}')
        try:
            body = cli_payload(json.loads(raw))
            content = body.get('content')
        except json.JSONDecodeError:
            content = raw.decode('utf-8')
        if not isinstance(content, str):
            raise TransportError('Docs fetch 缺少正文 content')
        return {'content': content, 'capture': capture}
