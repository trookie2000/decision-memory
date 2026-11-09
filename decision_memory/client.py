"""Bounded HTTP calls to a configured Chat Completions compatible gateway."""

import json
import http.client
import math
import os
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
        return asdict(self)


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


class ModelClient:
    def __init__(self, config: Config):
        self.config = config
        self.calls = []
        self.opener = urllib.request.build_opener(NoRedirect)

    def headers(self):
        headers = {'Content-Type': 'application/json', 'Accept': 'application/json'}
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
        body = {'model': self.config.model, 'stream': False, 'max_tokens': self.config.max_tokens,
                'messages': [{'role': 'system', 'content': system}, {'role': 'user', 'content': user}]}
        if self.config.json_mode:
            body['response_format'] = {'type': 'json_object'}
        if self.config.temperature is not None:
            body['temperature'] = self.config.temperature
        request = urllib.request.Request(self.config.endpoint, data=json.dumps(body).encode(), headers=self.headers(), method='POST')
        call = {'http_attempts': 0, 'prompt_tokens': None, 'completion_tokens': None, 'seconds': 0, 'status': 'failed'}
        self.calls.append(call)
        started = time.monotonic()
        try:
            for attempt in range(self.config.retries + 1):
                call['http_attempts'] += 1
                try:
                    with self.opener.open(request, timeout=self.config.timeout) as response:
                        data = response.read(8_000_001)
                    if len(data) > 8_000_000:
                        raise ModelError('模型响应超过 8 MB 上限')
                    break
                except urllib.error.HTTPError as error:
                    code = error.code
                    retry_after = error.headers.get('Retry-After', '')
                    error.close()
                    if code not in (408, 429, 500, 502, 503, 504) or attempt == self.config.retries:
                        raise ModelError(f'模型接口 HTTP {code}，请检查模型权限、接口配置或限流情况') from None
                    delay = float(retry_after) if retry_after.isdigit() else 2 ** attempt
                    time.sleep(min(delay, 10))
                except (urllib.error.URLError, TimeoutError, ConnectionError, http.client.HTTPException):
                    if attempt == self.config.retries:
                        raise ModelError('模型接口连接失败、响应中断或超时') from None
                    time.sleep(2 ** attempt)
            try:
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
