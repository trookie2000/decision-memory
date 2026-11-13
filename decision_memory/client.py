"""Bounded HTTP calls to a configured Chat Completions compatible gateway."""

import json
import http.client
import math
import os
import socket
import threading
import time
import tomllib
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from urllib.parse import urlsplit


class ModelError(RuntimeError):
    pass


class ModelOutputError(ValueError):
    pass


class NoRedirect(urllib.request.HTTPRedirectHandler):
    def redirect_request(self, request, file, code, message, headers, new_url):
        return None


@dataclass
class Config:
    endpoint: str
    model: str
    api_key_env: str = 'DECISION_MEMORY_API_KEY'
    headers_env: dict[str, str] = field(default_factory=dict)
    timeout: float = 180
    retries: int = 2
    max_tokens: int = 12000
    max_input_chars: int = 120000
    json_mode: bool = False
    stream: bool = False
    reasoning_effort: str | None = None
    temperature: float | None = None
    input_price_per_million: float | None = None
    output_price_per_million: float | None = None
    cost_unit: str = 'CNY'

    def __post_init__(self):
        url = urlsplit(self.endpoint)
        local = url.hostname in ('127.0.0.1', 'localhost', '::1')
        if not url.hostname or url.username or url.password or url.query or url.fragment:
            raise ValueError('endpoint 需要完整 URL，凭证应通过环境变量传入')
        if url.scheme != 'https' and not (url.scheme == 'http' and local):
            raise ValueError('模型接口需要 HTTPS，本机测试地址可使用 HTTP')
        if not isinstance(self.model, str) or not self.model.strip():
            raise ValueError('model 不能为空')
        if type(self.stream) is not bool:
            raise ValueError('stream 必须为布尔值')
        if self.reasoning_effort is not None and self.reasoning_effort not in (
                'none', 'minimal', 'low', 'medium', 'high', 'xhigh', 'max'):
            raise ValueError('reasoning_effort 需要接口支持的推理强度名称')
        for name, low, high in (('timeout', 1, 600), ('retries', 0, 3),
                                ('max_tokens', 1, 64000), ('max_input_chars', 100, 1_000_000)):
            value = getattr(self, name)
            if not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or not low <= value <= high:
                raise ValueError(f'{name} 必须在 {low} 至 {high} 之间')
        if any(not isinstance(getattr(self, name), int) for name in ('retries', 'max_tokens', 'max_input_chars')):
            raise ValueError('retries/max_tokens/max_input_chars 必须为整数')
        if not isinstance(self.api_key_env, str) or not isinstance(self.headers_env, dict):
            raise ValueError('api_key_env 和 headers_env 格式无效')
        if any(not isinstance(k, str) or not isinstance(v, str) or not k or not v for k, v in self.headers_env.items()):
            raise ValueError('headers_env 需要 HTTP 头名到环境变量名的映射')
        for name in ('input_price_per_million', 'output_price_per_million', 'temperature'):
            value = getattr(self, name)
            if value is not None and (not isinstance(value, (int, float)) or isinstance(value, bool) or not math.isfinite(value) or value < 0):
                raise ValueError(f'{name} 必须是非负有限数值')

    @classmethod
    def load(cls, path: Path):
        try:
            values = tomllib.loads(path.read_text(encoding='utf-8'))
            return cls(**values['model'])
        except (KeyError, TypeError, tomllib.TOMLDecodeError) as error:
            raise ValueError('配置格式错误，请对照 config.example.toml') from error

    def public(self):
        values = asdict(self)
        # Preserve signatures of saved non-streaming results from older versions.
        if not self.stream:
            values.pop('stream')
        if self.reasoning_effort is None:
            values.pop('reasoning_effort')
        return values


def read_stream_completion(response, timeout):
    """Assemble text SSE chunks without accepting a partial/model-tool response."""
    if 'text/event-stream' not in response.headers.get('Content-Type', ''):
        raise ModelOutputError('流式接口未返回 text/event-stream')
    content, pending = [], []
    usage, finish, done = {}, None, False
    size = 0
    started = time.monotonic()

    def consume():
        nonlocal usage, finish, done
        data = b'\n'.join(pending)
        pending.clear()
        if not data:
            return
        if data == b'[DONE]':
            done = True
            return
        try:
            event = json.loads(data)
        except (ValueError, UnicodeDecodeError) as error:
            raise ModelOutputError('模型流式响应不是有效 JSON 事件') from error
        if not isinstance(event, dict) or not isinstance(event.get('choices', []), list):
            raise ModelOutputError('模型流式事件不符合 Chat Completions 协议')
        if event.get('error'):
            raise ModelError('模型流式接口返回错误')
        if event.get('usage') is not None:
            if not isinstance(event['usage'], dict):
                raise ModelOutputError('模型 usage 不符合 Chat Completions JSON 协议')
            usage = event['usage']
        for choice in event.get('choices', []):
            if not isinstance(choice, dict):
                raise ModelOutputError('模型流式 choice 格式无效')
            delta = choice.get('delta') or {}
            if not isinstance(delta, dict):
                raise ModelOutputError('模型流式 delta 格式无效')
            if delta.get('refusal') or delta.get('tool_calls') or delta.get('function_call'):
                raise ModelError('模型未正常完成文本输出，请检查网关完成状态或拒绝原因')
            text = delta.get('content')
            if text is not None:
                if not isinstance(text, str):
                    raise ModelOutputError('模型 content 必须为文本')
                content.append(text)
            if choice.get('finish_reason') is not None:
                if finish is not None and finish != choice['finish_reason']:
                    raise ModelOutputError('模型流式完成状态不一致')
                finish = choice['finish_reason']

    # A socket idle timeout does not bound a peer dripping an unfinished line.
    # Shut down the owned urllib socket at the absolute deadline to unblock even
    # an in-progress buffered/chunked read (CPython's HTTP/HTTPS response layout).
    stream_socket = getattr(getattr(getattr(response, 'fp', None), 'raw', None), '_sock', None)
    if stream_socket is None:
        raise ModelError('流式接口无法设置总时限，请检查 HTTP 传输配置')
    expired = threading.Event()

    def expire():
        expired.set()
        try:
            stream_socket.shutdown(socket.SHUT_RDWR)
        except OSError:
            pass  # A just-completed response may already have closed its socket.

    timer = threading.Timer(timeout, expire)
    timer.daemon = True
    timer.start()
    try:
        while line := response.readline(8_000_001):
            size += len(line)
            if size > 8_000_000:
                raise ModelError('模型响应超过 8 MB 上限')
            if expired.is_set() or time.monotonic() - started > timeout:
                raise TimeoutError('模型流式响应超过总时限')
            if line.startswith(b'data:'):
                pending.append(line[5:].lstrip(b' ').rstrip(b'\r\n'))
            elif not line.strip():
                consume()
                if done:
                    break
        if expired.is_set() or time.monotonic() - started > timeout:
            raise TimeoutError('模型流式响应超过总时限')
        if not done:
            raise http.client.IncompleteRead(b'')
        if finish is None:
            raise ModelError('模型流式响应缺少完整结束状态，不能采纳部分输出')
        return {'choices': [{'message': {'content': ''.join(content)}, 'finish_reason': finish}], 'usage': usage}
    except (OSError, http.client.HTTPException) as error:
        if expired.is_set():
            raise TimeoutError('模型流式响应超过总时限') from error
        raise
    finally:
        timer.cancel()


def parse_json(text: str) -> dict:
    text = text.strip()
    if text.startswith('```') and text.endswith('```'):
        text = '\n'.join(text.splitlines()[1:-1])
    try:
        result = json.loads(text)
    except json.JSONDecodeError as error:
        raise ModelOutputError('模型未返回完整 JSON，请缩小输出或提高 max_tokens') from error
    if not isinstance(result, dict):
        raise ModelOutputError('模型 JSON 顶层必须是对象')
    return result


def http_error_message(error, sensitive_values) -> str:
    hints = {
        401: '请检查 API Key 是否有效',
        402: '请核对接入点所属项目的余额、额度和计费状态，具体以网关原因为准',
        403: '请检查 API Key 对该项目及模型接入点的调用权限',
        429: '接口限流，请稍后重试或检查调用配额',
    }
    message = f'模型接口 HTTP {error.code}，' + hints.get(error.code, '请检查接口配置或服务状态')

    def safe_detail(value):
        if not isinstance(value, (str, int)) or isinstance(value, bool):
            return ''
        text = str(value)
        for secret in sorted(set(sensitive_values), key=len, reverse=True):
            if secret:
                text = text.replace(secret, '[REDACTED]')
        text = ''.join(char if char.isprintable() else ' ' for char in text)
        return ' '.join(text.split())[:500]

    payload = {}
    try:
        raw = error.read(16_385)
        if len(raw) <= 16_384:
            payload = json.loads(raw)
    except (ValueError, OSError, http.client.HTTPException):
        pass
    details = []
    request_id = error.headers.get('x-request-id') or error.headers.get('request-id')
    if isinstance(payload, dict):
        reason = payload.get('error')
        reason = reason if isinstance(reason, dict) else payload
        for key in ('code', 'type', 'message'):
            value = safe_detail(reason.get(key))
            if value:
                details.append(f'{key}={value}')
        request_id = request_id or payload.get('request_id') or payload.get('requestId') or reason.get('request_id')
    if details:
        message += '；网关 ' + '，'.join(details)[:700]
    if request_id := safe_detail(request_id):
        message += '；request_id=' + request_id[:128]
    return message


class ModelClient:
    def __init__(self, config: Config):
        self.config = config
        self.calls = []
        self.opener = urllib.request.build_opener(NoRedirect)

    def headers(self):
        headers = {'Content-Type': 'application/json',
                   'Accept': 'text/event-stream' if self.config.stream else 'application/json'}
        names = dict(self.config.headers_env)
        if self.config.api_key_env:
            names['Authorization'] = self.config.api_key_env
        for header, variable in names.items():
            value = os.environ.get(variable)
            if not value:
                raise ModelError(f'缺少环境变量 {variable}，请在本地配置模型凭证')
            if '\n' in value or '\r' in value:
                raise ModelError(f'环境变量 {variable} 不能包含换行')
            if header == 'Authorization' and self.config.api_key_env:
                value = 'Bearer ' + value
            headers[header] = value
        return headers

    def complete(self, system: str, user: str) -> dict:
        if len(system) + len(user) > self.config.max_input_chars:
            raise ModelError('输入超过 max_input_chars，请按完整阶段拆分或调整上限')
        body = {'model': self.config.model, 'stream': self.config.stream, 'max_tokens': self.config.max_tokens,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        if self.config.json_mode:
            body['response_format'] = {'type': 'json_object'}
        if self.config.temperature is not None:
            body['temperature'] = self.config.temperature
        if self.config.reasoning_effort is not None:
            body['reasoning_effort'] = self.config.reasoning_effort
        headers = self.headers()
        sensitive_values = [value for name, value in headers.items() if name.lower() not in ('content-type', 'accept')]
        sensitive_values += [value.removeprefix('Bearer ') for value in sensitive_values]
        request = urllib.request.Request(self.config.endpoint, data=json.dumps(body).encode(), headers=headers, method='POST')
        call = {'http_attempts': 0, 'prompt_tokens': None, 'completion_tokens': None, 'seconds': 0, 'status': 'failed'}
        self.calls.append(call)
        started = time.monotonic()
        try:
            for attempt in range(self.config.retries + 1):
                call['http_attempts'] += 1
                try:
                    with self.opener.open(request, timeout=self.config.timeout) as response:
                        if self.config.stream:
                            envelope = read_stream_completion(response, self.config.timeout)
                        else:
                            data = response.read(8_000_001)
                    if not self.config.stream and len(data) > 8_000_000:
                        raise ModelError('模型响应超过 8 MB 上限')
                    break
                except urllib.error.HTTPError as error:
                    code = error.code
                    retry_after = error.headers.get('Retry-After', '')
                    try:
                        if code not in (408, 429, 500, 502, 503, 504) or attempt == self.config.retries:
                            raise ModelError(http_error_message(error, sensitive_values)) from None
                    finally:
                        error.close()
                    delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                    time.sleep(min(delay, 10))
                except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                    if attempt == self.config.retries:
                        raise ModelError('模型接口连接失败、响应中断或超时') from None
                    time.sleep(2 ** attempt)
            try:
                if not self.config.stream:
                    envelope = json.loads(data)
                choice = envelope['choices'][0]
                content = choice['message']['content']
                usage = envelope.get('usage') or {}
                if not isinstance(usage, dict):
                    raise ModelOutputError('模型 usage 不符合 Chat Completions JSON 协议')
                for name in ('prompt_tokens', 'completion_tokens'):
                    value = usage.get(name)
                    if isinstance(value, int) and not isinstance(value, bool) and value >= 0:
                        call[name] = value
                if choice.get('finish_reason') == 'length':
                    raise ModelOutputError('输出被 max_tokens 截断，请精简条目或提高上限')
                if choice.get('finish_reason') != 'stop' or choice['message'].get('refusal'):
                    raise ModelError('模型未正常完成文本输出，请检查网关完成状态或拒绝原因')
                if not isinstance(content, str):
                    raise ModelOutputError('模型 content 必须为文本')
            except (KeyError, IndexError, TypeError, json.JSONDecodeError, UnicodeDecodeError):
                raise ModelOutputError('模型响应不符合 Chat Completions JSON 协议') from None
            result = parse_json(content)
            call['status'] = 'success'
            return result
        finally:
            call['seconds'] = round(time.monotonic() - started, 3)

    def metrics(self):
        totals = {}
        for name in ('prompt_tokens', 'completion_tokens'):
            values = [call[name] for call in self.calls]
            totals[name] = sum(values) if values and all(value is not None for value in values) else None
        cost = None
        prices = (self.config.input_price_per_million, self.config.output_price_per_million)
        if all(value is not None for value in (*prices, *totals.values())):
            cost = (totals['prompt_tokens'] * prices[0] + totals['completion_tokens'] * prices[1]) / 1_000_000
        return {**totals, 'calls': len(self.calls), 'http_attempts': sum(call['http_attempts'] for call in self.calls),
                'seconds': round(sum(call['seconds'] for call in self.calls), 3),
                'estimated_cost': cost, 'cost_unit': self.config.cost_unit if cost is not None else None,
                'usage_complete': bool(self.calls) and all(call['prompt_tokens'] is not None and call['completion_tokens'] is not None for call in self.calls)}
