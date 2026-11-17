# Evidence Audit Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 在完整需求 verified 提取中加入可主动开启、可离线重放的证据准入与候选覆盖审计。

**Architecture:** 复用来源索引、事件重放和字段复核。确定性审计独立于模型调用；调用层保存逐步回执，CLI 负责输出预留与报告。

**Tech Stack:** Python 3.11+ 标准库，unittest，本机 HTTP 夹具；不增加依赖。

**Spec:** `docs/superpowers/specs/2026-11-17-evidence-admission-coverage-design.md`

## Global Constraints

- 仅 `requirement extract --mode verified --evidence-audit v1` 接入；原路径仍生成 schema 2，新路径生成 schema 3。
- 正常两次模型调用，每步仅对完整有效 JSON 的协议错误修复一次；最多发起四次请求。
- 有效配置 `retries=0`，不修改 TOML。超时、断流等不确定结果停止并标 unknown。
- 字符坐标为 Source.text 内 Unicode 码点半开区间；多次匹配必须扩大引文。
- 候选最多 256 个；请求前超限即失败，不截断、降级或重发。
- 原始记录、gold、旧结果、Mario 评分保持不变；真实历史导入和模型质量试点属于下一步数据准备，当前不全量读取。
- 后台直接子进程执行，不打开 IDE 终端、浏览器、远控或操作键鼠。
- 所有提交前按用户 AGENTS 核对 Weaver，只修复可确认漏采，不重复投递未知回执。

## Review Focus

- 同行重复引文及 emoji、BOM、CRLF 必须保持可重建位置，不能选首个匹配。
- 短确认只能与先前 Agent 提议关联；未知角色不能升级成用户确认。
- 混合业务与排期使用焦点区间，non_reusable 必须检查全部当前引用，不能藏掉依赖。
- needs_review 与结构错误同时出现时先修结构，结构有效后保留草稿并退出 2。
- HTTP 错误、断流、无效 JSON、输出冲突和已有 run 目录不能产生隐式付费重试或成功输出。

## Task 1: 确定性定位、候选与准入校验

**Files:**
- Create: `decision_memory/evidence_spans.py`
- Create: `decision_memory/requirement_audit.py`
- Test: `tests/test_requirement_audit.py`

**Interfaces:**
- `locate_unique_quote(sample, reference) -> dict`，返回来源、行内 start/end、行摘要与文件摘要。
- `build_coverage_catalog(sample, policy='evidence-v1') -> list[dict]`，按 catalog 顺序、原始片段位置生成 U001 等编号。
- `validate_evidence_audit(value, compiled, grounding, sample, policy='evidence-v1') -> dict`，先校验完整结构，返回包含 needs_review 标记的规范化审计；不把待复核当结构异常。
- 规范化包含 schema_version=1、input_digest、policy、policy_digest、unit_catalog、evidence_spans、evidence_focus、admission_checks、user_attribution_checks、coverage_checks、audit_status。
- 模型仅提交 policy、evidence_focus、admission_checks、user_attribution_checks、coverage_checks；程序重建其余字段。保存结果重放要求全字段相等。
- coverage target：item_field 用 knowledge_key/field；history_event 用 event_id/field（失效用 field=note）；open_question 用 question_id；reason_check 用 source_id/quote。每个目标另带实际 evidence。
- coverage row 用 unit_id/disposition/semantic_coverage/targets/exclusion_reason/notes；排除原因限 schedule/format/wiring/chatter，其他处置 exclusion_reason=null。
- 显式归因检查用 knowledge_key/field/evidence/notes，须引用该字段实际 user 证据。

- [x] 写失败用例：重复引文、码点坐标、未知角色、先后倒置、短确认冒充直接规则、缺项、数值短引文 partial、焦点分离、全局排除冲突、历史/未决/未知目标、候选超限。
- [x] Run: `.venv/bin/python -m unittest discover -s tests -p test_requirement_audit.py -q`。Expected: 新模块缺失使新增用例失败。
- [x] 实现上述函数，语义 full 为模型判断，程序只检查关联和清单；需要复核标记在完整结构校验后产生。
- [x] 重跑同命令。Expected: 新增用例全部通过。
- [x] Weaver 核对后提交这组源码与测试。

## Task 2: 持久调用、CLI、保存与离线重放

**Files:**
- Create: `decision_memory/requirement_audit_run.py`
- Create: `decision_memory/requirement_audit_report.py`
- Modify: `decision_memory/requirement_pipeline.py`
- Modify: `decision_memory/cli.py`
- Modify: `decision_memory/client.py`
- Modify: `decision_memory/storage.py`
- Modify: `decision_memory/batch_gate.py`
- Test: `tests/test_requirement_audit_cli.py`

**Interfaces:**
- `extract_requirement(sample, client, mode='verified', *, evidence_audit=None, run_dir=None)`；原参数行为保持兼容。
- `extract_audited_requirement(sample, client, run_dir) -> dict`，仅由 verified v1 调用；记录请求和有效配置摘要及 transport_policy。
- run 目录为输出同名 `result.run/`；预留后拒绝再次 extract。每次请求保存 started、完整模型 JSON、validation 状态及最终 run 状态。
- `AuditNeedsReview(RuntimeError)` 在结构完整且仍待复核时退出；`draft.json` 与错误记录留在 run 目录，不发布 result.json。
- `render_audit_html(result, sample) -> str`，转义原文，自包含报告展示知识、候选处置、历史、未决与区间；生成 `<output-stem>.audit.html`。
- schema 3 离线验证重建审计；schema 1、2 仍按原协议读取，不接受冒充旧 schema 的审计数据。
- ModelClient 为调用记录增加 transport_status，不保存头部或凭证；旧重试策略不改变，新路径覆盖 retries=0。

- [x] 写失败的真实本机 HTTP 集成用例：成功两次、结构修复、混合错误、needs_review、不完整 JSON、HTTP 429/503、断流、已有 run、模式冲突、超限前零请求、二次请求超预算、离线篡改、HTML 转义、旧模式兼容。
- [x] Run: `.venv/bin/python -m unittest discover -s tests -p test_requirement_audit_cli.py -q`。Expected: 开关/接口尚未支持，新增用例失败。
- [x] 实现调用与保存，所有响应先存后验；异常分类不进入 checked_call 的广义 ValueError 重试。
- [x] 重跑同命令。Expected: 所有集成用例通过，HTTP 尝试数与回执一致。
- [x] Weaver 核对后提交源码与集成测试。

## Task 3: 使用说明与整体验证

**Files:**
- Modify: `docs/requirement-memory.md`
- Modify: 本计划的完成复选框。
- Create: 本计划专用忽略目录中的验证和评审记录。

**Interfaces:** 交付可复制命令、输出说明、明确失败状态和程序/模型责任。

- [x] 更新说明：主动开关、schema 3、草稿/检查点、拒绝自动重试、语义判定限制及真实数据试点步骤。
- [x] Run: `.venv/bin/python -m unittest discover -s tests -q`。Expected: 完整测试全部通过。
- [x] Run: `git diff --check`。Expected: 无空格错误。
- [x] 独立评审整个本轮差异，修复重要问题并以失败回归验证；不发起付费模型回归、不复跑执行未知的 T03。
- [x] Weaver 核对后提交说明及计划。实际测试、采集和推送状态据实交付。

## 验证记录

2026-11-17：基线 226 项通过；最终完整回归 263 项通过，耗时 79.351 s。新增用例包含 20 项确定性审计和 17 项本机 HTTP CLI 集成测试。

独立评审发现的 Content-Length 短读、逐调用用量丢失均已复现并修复。另补了结构修复清除待复核标记的失败回归。三个问题均观察到 RED→GREEN，随后完整套件通过。

本轮未执行付费模型试点，未读取历史会话正文。真实需求上的分数收益仍待固定参考对照，不能从测试数量推断。
