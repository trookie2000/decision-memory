# Source ID Grounding Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 通过程序展开来源编号、字段检索和实现符号校验，提高需求知识的可追溯性并完成有限独立验收。

**Architecture:** v2 编解码器复用现有事件、未知和 evidence-v1 校验；独立的字段检索模块只提供原文线索及必要的符号检查。新增 v2 调度与 schema 4，不改变旧提取路径。

**Tech Stack:** Python 3.11+、标准库、unittest、本地 HTTP fixture。

**Spec:** `docs/superpowers/specs/2026-11-17-source-id-grounding-design.md`

## Global Constraints

- 后台 Node 子进程执行，不用终端 UI、键鼠、远控、Mario 或 MyFlicker。
- 来源只来自 sample.catalog，参考不进入预测；内部材料和凭证不上传公开仓库。
- 保持全部旧模式；v2 正常两次模型调用，最多四次请求，HTTP retries=0。
- 不通过模糊匹配、删除未知或放宽角色/覆盖门槛换取成功。

## Review Focus

- 编号不存在、对象式引用和模型伪造 gap 身份须拒绝，Task 1 覆盖。
- 整行展开必须保留真实角色与时序，不能把检索建议当确认，Task 1/2 覆盖。
- 大量候选、嵌套错误、跨节点来源与待复核标记保持原预算和拒绝行为，Task 2 覆盖。
- 简单 schema 降级、删除符号证明或缩短引文不能靠外层重哈希绕过，Task 2 覆盖。
- 保留材料须在预测前独立冻结，任何未知请求不得恢复为新任务，Task 3 覆盖。

### Task 1: 编号证据与字段线索

**Files:** 新建 `decision_memory/evidence_ids.py`、`decision_memory/field_grounding.py`；新建相应测试文件。

**Interfaces:** `source_packet(sample) -> str`、`gap_catalog(sample) -> list`、`decode_event_stream(payload, sample) -> dict`、`decode_review_reply(payload, sample) -> tuple`、`encode_event_stream(compiled, sample) -> dict`；`field_evidence_candidates(compiled, sample, limit=6) -> list`、`validate_literal_grounding(compiled) -> dict`。

- [ ] 写失败测试并实际观察：整行展开、全部事件类型、gap 绑定、无损列式还原、对象/重复/假编号拒绝、检索不越未来节点、字段自身符号缺失、普通术语不误拦。
- [ ] 实现编号协议与有界检索，不修改原输入对象或原材料。
- [ ] 目标测试及完整测试集通过，核对采集后提交。

### Task 2: v2 调度、CLI 和离线证明

**Files:** 新建 `decision_memory/requirement_audit_v2.py`、`tests/test_requirement_audit_v2_cli.py`；修改 requirement_pipeline、requirement_audit_run、cli、storage 及使用说明。

**Interfaces:** `extract_audited_requirement_v2(sample, client, run_dir)`；旧 `requirement_result` 接口新增可选 audit_version/literal_grounding，默认行为不变；AuditRun 可接收独立 prompt_hash。

- [ ] 写本地 HTTP 失败测试并观察：两次正常请求发布 schema 4、来源由程序填写、符号缺失修复、needs_review 不发布、输入/证明篡改、四请求上限、无效 JSON/断流/认证回显不重发。
- [ ] 实现 v2 提示和调度，串接已有校验；完整来源表只传一次，建议和原文均不得包含 gold。
- [ ] 离线 validate 和 HTML 处理 schema 4；原 v1 结果及阶段流程回归。
- [ ] 目标测试及完整测试集通过，核对采集后提交。

### Task 3: 保留材料、真实对照和交付

**Files:** 私有试验记录在 `runs/semantic-grounding-eval-20261117/`；公开使用说明及本计划。

- [ ] 隔离整理并独立复核两份保留需求，冻结输入、参考和预算。
- [ ] 发起一次新上下文代码评审，修复重要问题并运行完整测试。
- [ ] 冻结实现和相同模型配置，开发集回放及保留需求成对运行；总 HTTP 上限 20，并发上限 2，按 spec 停止条件执行。
- [ ] 隔离判卷全部可用结果，复算质量、未知、覆盖、可用性和成本；依预先门槛作发布取舍。
- [ ] 更新文档、核对采集、提交并尝试非交互推送；无法认证时保留源码包和可复现验收，不假报发布成功。
