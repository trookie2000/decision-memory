"""Durable, finite background batches of real Mario collaboration cases."""

import fcntl
import json
import os
import re
import subprocess
import sys
import time
from dataclasses import replace
from pathlib import Path

from .client import Config, ModelClient, ModelError
from .collaboration import BattleConfig, DocsCLI, MarioMCP, TransportError
from .coordinator import Battle, PAUSED, file_digest, run_lock
from .evaluation import digest
from .pipeline import MODES
from .sources import STAGES
from .storage import read_json, write_json, write_text


def preflight(model_path, battle_config):
    """Fail before remote writes; credentials are never returned or serialized."""
    config = Config.load(model_path)
    ModelClient(config).headers()
    client = ModelClient(replace(config, max_tokens=512, retries=0, timeout=min(config.timeout, 60)))
    answer = client.complete('只返回 JSON 对象。', '请只返回 {"ok": true}。')
    if answer.get('ok') is not True:
        raise ModelError('模型连通性检查未返回预期 JSON')
    capabilities = MarioMCP(battle_config).discover()
    # Existing authorized result document: read-only check, never change it.
    document = DocsCLI(battle_config).fetch('fcACkjsTkappn83888dk-kzM_')
    return {'model': config.model, 'model_probe': 'passed', 'metrics': client.metrics(),
            'mario_skill_id': capabilities['skill_id'], 'docs_read': bool(document['content'])}


class Series:
    def __init__(self, root, *, battle_dependencies=None, preflight=None, sleep=None):
        self.root = Path(root).resolve()
        self.state = read_json(self.root / 'series-state.json')
        if (self.state.get('kind') != 'series' or self.state.get('schema') != 1
                or self.state.get('state_digest') != digest({k: v for k, v in self.state.items() if k != 'state_digest'})):
            raise ValueError('批次状态已改动或版本不支持')
        # Control commands use the protected snapshot even if execution inputs break.
        self.cases = self.state['cases']
        self.config = BattleConfig(**self.state['battle_config'])
        self.model_path = Path(self.state['model_config'])
        self.dependencies = battle_dependencies or {}
        self.preflight = preflight or globals()['preflight']
        self.sleep = sleep or time.sleep

    @classmethod
    def create(cls, root, battle_config, model_config, cases, mode='baseline', max_seconds=7200, **dependencies):
        if not isinstance(cases, list) or not 1 <= len(cases) <= 100:
            raise ValueError('批次需要 1 到 100 个 case')
        ids = set()
        for case in cases:
            if (not isinstance(case, dict) or not isinstance(case.get('sample_id'), str)
                    or not re.fullmatch(r'[A-Za-z0-9][A-Za-z0-9_-]{0,79}', case['sample_id'])
                    or case['sample_id'] in ids or case.get('stage') not in STAGES
                    or not isinstance(case.get('brief'), str) or not 1 <= len(case['brief'].strip()) <= 20000
                    or not isinstance(case.get('clarification', ''), str)
                    or len(case.get('clarification', '')) > 20000):
                raise ValueError('case 编号、阶段、要求或修订条件无效')
            ids.add(case['sample_id'])
        if mode not in MODES or type(max_seconds) is not int or not 1 <= max_seconds <= 86400:
            raise ValueError('模式或批次时限无效（1 到 86400 秒）')
        model_config = Path(model_config).resolve()
        Config.load(model_config)
        root = Path(root).resolve()
        if root.exists() and any(root.iterdir()):
            raise ValueError('批次目录已有内容，请使用 series resume')
        root.mkdir(parents=True, exist_ok=True)
        write_json(root / 'manifest.json', cases)
        state = {'kind': 'series', 'schema': 1, 'status': 'created', 'created_at': time.time(),
                 'battle_config': replace(battle_config, dispatch_context='operation').public(),
                 'model_config': str(model_config), 'model_config_digest': file_digest(model_config),
                 'manifest_digest': file_digest(root / 'manifest.json'), 'mode': mode,
                 'cases': json.loads(json.dumps(cases)),
                 'max_seconds': max_seconds, 'index': 0, 'records': [], 'last_error': None,
                 'preflight_attempts': 0}
        state['state_digest'] = digest(state)
        write_json(root / 'series-state.json', state)
        series = cls(root, **dependencies)
        series.report()
        return series

    def save(self):
        self.state['updated_at'] = time.time()
        self.state['state_digest'] = digest({k: v for k, v in self.state.items() if k != 'state_digest'})
        write_json(self.root / 'series-state.json', self.state, overwrite=True)

    def verify_inputs(self):
        if (file_digest(self.root / 'manifest.json') != self.state['manifest_digest']
                or read_json(self.root / 'manifest.json') != self.cases):
            raise ValueError('批次清单已被改动')
        if file_digest(self.model_path) != self.state['model_config_digest']:
            raise ValueError('批次模型配置已被改动，请恢复原配置或使用新批次')

    def summary(self):
        process = read_json(self.root / 'process.json') if (self.root / 'process.json').exists() else {}
        pid = self.state.get('pid') or process.get('pid')
        alive = False
        if pid and self.state['status'] in ('created', 'running'):
            try:
                os.kill(pid, 0)
                alive = True
            except ProcessLookupError:
                pass
        records = {record['sample_id']: record for record in self.state['records']}
        all_cases = [records.get(case['sample_id'], {'sample_id': case['sample_id'], 'phase': 'not_started',
                      'status': 'pending', 'score': None, 'output': str(self.root / 'cases' / case['sample_id']),
                      'last_error': None}) for case in self.cases]
        return {'status': self.state['status'], 'total': len(self.cases), 'finished': self.state['index'],
                'current_case': self.cases[self.state['index']]['sample_id'] if self.state['index'] < len(self.cases) else None,
                'pid': pid, 'process_running': alive, 'stop_requested': (self.root / 'STOP').exists(),
                'last_error': self.state.get('last_error'), 'cases': all_cases,
                'output': str(self.root), 'log': str(self.root / 'background.log'),
                'human_review': 'pending', 'official_score': None}

    def report(self):
        summary = self.summary()
        write_json(self.root / 'summary.json', summary, overwrite=True)
        lines = ['# 连续评测批次', '', f'状态：{summary["status"]}；已结束 {summary["finished"]}/{summary["total"]} 个 case。',
                 '', '以下为合成样本的 Agent 初评，人工确认与正式比赛成绩待核对。', '',
                 '| Case | 状态 | 阶段 | 已评分条目 | 正确条目 | 结果 |', '|---|---|---|---:|---:|---|']
        for record in summary['cases']:
            score = record.get('score') or {}
            sample = record['sample_id']
            lines.append(f'| {sample} | {record["status"]} | {record["phase"]} | '
                         f'{score.get("reviewed_items", "")} | {score.get("correct_items", "")} | '
                         f'[运行记录](cases/{sample}/report.md) |')
        if summary['last_error']:
            lines.extend(['', '阻塞原因：' + summary['last_error']])
        write_text(self.root / 'report.md', '\n'.join(lines) + '\n', overwrite=True)
        return summary

    def needs_model_preflight(self):
        """Existing predictions may finish scoring without another model request.

        This exemption ends before creating the next case or retrying extraction.
        """
        if self.state['index'] >= len(self.cases):
            return False
        folder = self.root / 'cases' / self.cases[self.state['index']]['sample_id']
        if not (folder / 'state.json').exists():
            return True
        battle = Battle(folder, **self.dependencies)
        return battle.state['phase'] not in ('scorer', 'done') and battle.state['status'] != 'human_review'

    def request_stop(self):
        with (self.root / '.launch.lock').open('a') as lock:
            fcntl.flock(lock, fcntl.LOCK_EX)
            write_text(self.root / 'STOP', 'stop requested\n', overwrite=True)
        return {'stop_requested': True, 'output': str(self.root)}

    def launch(self, resume=False, retry_extraction=False):
        if self.state['status'] == 'completed':
            return self.summary()
        # The launcher mutex is separate from the child controller's run mutex.
        with (self.root / '.launch.lock').open('a') as lock:
            try:
                fcntl.flock(lock, fcntl.LOCK_EX | fcntl.LOCK_NB)
            except BlockingIOError:
                raise ValueError('批次启动器正在运行') from None
            with run_lock(self.root):
                pass
            self.verify_inputs()
            if self.needs_model_preflight():
                ModelClient(Config.load(self.model_path)).headers()
            if resume:
                (self.root / 'STOP').unlink(missing_ok=True)
            command = [sys.executable, '-m', 'decision_memory', 'series', 'run', '--output', str(self.root)]
            # The parent consumed the old stop under the launcher mutex. The child
            # must preserve any NEW stop requested after launch.
            if retry_extraction:
                command.append('--retry-extraction')
            with (self.root / 'background.log').open('a', encoding='utf-8') as log:
                child = subprocess.Popen(command, stdin=subprocess.DEVNULL, stdout=log, stderr=subprocess.STDOUT,
                                         start_new_session=True, cwd=Path(__file__).resolve().parent.parent)
            process = {'pid': child.pid, 'started_at': time.time()}
            write_json(self.root / 'process.json', process, overwrite=True)
            return {**self.summary(), 'pid': child.pid, 'launch': 'started'}

    def run(self, resume=False, retry_extraction=False):
        with run_lock(self.root):
            if read_json(self.root / 'series-state.json') != self.state:
                raise ValueError('批次状态已由其他进程更新，请重新读取')
            if self.state['status'] == 'completed':
                return self.report()
            if resume:
                with (self.root / '.launch.lock').open('a') as lock:
                    fcntl.flock(lock, fcntl.LOCK_EX)
                    (self.root / 'STOP').unlink(missing_ok=True)
            self.state['pid'] = os.getpid()
            if (self.root / 'STOP').exists():
                self.state['status'] = 'stopped'
                self.save()
                return self.report()
            self.state['status'] = 'running'
            self.state['last_error'] = None
            self.save()
            try:
                self.verify_inputs()
                model_checked = False
                end = time.monotonic() + self.state['max_seconds']
                while self.state['index'] < len(self.cases):
                    if (self.root / 'STOP').exists():
                        self.state['status'] = 'stopped'
                        break
                    if time.monotonic() >= end:
                        self.state['status'] = 'paused_time_limit'
                        break
                    self.verify_inputs()
                    case = self.cases[self.state['index']]
                    if not model_checked and self.needs_model_preflight():
                        print(json.dumps({'event': 'series_case_preflight', 'sample_id': case['sample_id'],
                                          'index': self.state['index'] + 1, 'total': len(self.cases)}, ensure_ascii=False), flush=True)
                        self.state['preflight_attempts'] += 1
                        self.save()
                        try:
                            checked = self.preflight(self.model_path, self.config)
                        except (OSError, ValueError, ModelError, TransportError, subprocess.SubprocessError) as error:
                            record = {'sample_id': case['sample_id'], 'phase': 'preflight', 'status': 'blocked',
                                      'score': None, 'output': str(self.root / 'cases' / case['sample_id']),
                                      'last_error': str(error)}
                            if len(self.state['records']) <= self.state['index']:
                                self.state['records'].append(record)
                            else:
                                self.state['records'][self.state['index']] = record
                            raise
                        write_json(self.root / f'preflight-{self.state["preflight_attempts"]:03d}.json', checked)
                        model_checked = True
                    folder = (self.root / 'cases' / case['sample_id']).resolve()
                    if not folder.is_relative_to(self.root):
                        raise ValueError('case 输出路径越界')
                    if (folder / 'state.json').exists():
                        battle = Battle(folder, **self.dependencies)
                    else:
                        battle = Battle.create(folder, self.config, case['sample_id'], case['stage'], case['brief'],
                                               clarification=case.get('clarification', ''), model_config=self.model_path,
                                               mode=self.state['mode'], **self.dependencies)
                    explicit_retry = retry_extraction and battle.state['status'] in ('extraction_failed', 'extraction_unknown')
                    value = battle.run(0, retry_extraction=explicit_retry)
                    retry_extraction = False
                    record = {k: value.get(k) for k in ('sample_id', 'phase', 'status', 'score', 'output', 'last_error')}
                    if len(self.state['records']) <= self.state['index']:
                        self.state['records'].append(record)
                    else:
                        self.state['records'][self.state['index']] = record
                    print(json.dumps({'event': 'series_progress', 'index': self.state['index'] + 1,
                                      'total': len(self.cases), **record}, ensure_ascii=False), flush=True)
                    if value['status'] in ('agent_review_complete', 'agent_review_pending', 'human_review'):
                        self.state['index'] += 1
                        print(json.dumps({'event': 'series_case_finished', 'sample_id': case['sample_id'],
                                          'finished': self.state['index'],
                                          'next_case': (self.cases[self.state['index']]['sample_id']
                                                        if self.state['index'] < len(self.cases) else None)}, ensure_ascii=False), flush=True)
                    elif any(op.get('dispatch') == 'unknown' and not op.get('done')
                             for op in battle.state['operations'].values()):
                        self.state.update(status='blocked', last_error=f'{case["sample_id"]} 派发结果未知；保留原回执，核对后恢复，禁止自动重发')
                    elif value['status'] in PAUSED:
                        self.state.update(status='blocked', last_error=value.get('last_error') or f'{case["sample_id"]}: {value["status"]}')
                    self.save()
                    self.report()
                    if self.state['status'] == 'blocked':
                        break
                    if value['status'] == 'waiting':
                        self.sleep(self.config.poll_interval)
                if self.state['index'] == len(self.cases):
                    self.state['status'] = 'completed'
            except (OSError, ValueError, ModelError, TransportError, subprocess.SubprocessError) as error:
                self.state['status'] = 'blocked'
                self.state['last_error'] = str(error)
            self.save()
            return self.report()
