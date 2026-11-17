# Output Protocol Reliability Implementation Plan

> **For agentic workers:** REQUIRED SUB-SKILL: Use superpowers:executing-plans to implement this plan task-by-task. Steps use checkbox (`- [ ]`) syntax for tracking.

**Goal:** 修复无依据字段的格式兼容和严格 JSON 失败诊断，并以真实需求验证输出可靠性。

**Architecture:** 在字段校验器内做可重放的窄范围规范化。模型客户端提供安全解析诊断，审计运行负责本地持久化及准确的阶段状态。保持证据门槛和请求预算。

**Tech Stack:** Python 3.11+、标准库、unittest、本地 HTTP fixture。

**Spec:** `docs/superpowers/specs/2026-11-17-output-protocol-reliability-design.md`

## Global Constraints

- 全部命令由后台 Node 子进程执行，不操作终端 UI、键鼠或远控。
- 当前开发分支内实施；源代码用 apply_patch 修改。
- 不修改旧试点及 Mario 评分；内部材料与响应不推送公开仓库。
- 解析失败或未知执行不自动重发；保留 needs_review/partial/unclear。

## Review Focus

- 非空证据、结论、复合未知不得借标点规范化绕过证据要求，Task 1 覆盖。
- 规范化后的事件必须再次重放一致，伪造记录必须拒绝，Task 1 覆盖。
- 凭证被模型回显时，诊断、日志和本地内容不得保存凭证，Task 2 覆盖。
- 完整但无效的输出和网络中断必须分开记录，Task 2 覆盖。
- JSON 截断、围栏和传输失败仍只发原有请求数，Task 2 覆盖。

### Task 1: 可重放的未知标记规范化

**Files:** `decision_memory/validation.py`；`tests/test_validation.py`；`tests/test_requirement_memory.py`。

**Interfaces:** `validate_field(field, sample, location, required) -> dict` 保持签名；返回字段可包含经过校验的 `protocol_normalization={rule, original_text}`。

- [ ] 写失败测试：无证据的 reason/boundary 的两个标点形式得到规范文本和原文记录；输入对象不变；结论、问题、带证据占位、复合未知和伪造记录被拒绝。
- [ ] 运行目标测试，确认因缺少规范化而失败。
- [ ] 实现窄范围规范化及记录验证，增加需求事件重放一致测试。
- [ ] 目标测试及完整测试集通过，核对 Weaver 后提交 Task 1。

### Task 2: 安全解析诊断与准确回执

**Files:** `decision_memory/client.py`；`decision_memory/requirement_audit_run.py`；`decision_memory/storage.py`；`tests/test_requirement_audit_cli.py`；`docs/requirement-memory.md`。

**Interfaces:** `ModelOutputError` 增加可选 `response_text` 和 `diagnostic`；调用记录只携带安全诊断。复用 `storage.write_text(path, text)` 的 0600 原子写入保存本地内容。审计步骤新增 `failure_phase`、可选 `raw_response_file` 和诊断摘要。

- [ ] 写本地 HTTP 失败测试：严格 JSON 错误保存安全内容及解析位置，步骤为 response_received/parse，只有一次请求；截断、网络未知、HTTP 错误仍拒绝且不重发；认证值回显被脱敏；结构错误逐次记录。
- [ ] 运行目标测试，确认诊断与状态断言失败。
- [ ] 实现异常诊断、本地安全写入和步骤分类，保持严格解析与预算；更新使用说明。
- [ ] 目标测试及完整测试集通过，核对 Weaver 后提交 Task 2。

### Task 3: 独立评审与有限真实复测

**Files:** 仅本地忽略的运行产物，必要修复对应生产文件及回归测试。

- [ ] 按完整 diff 发起一次隔离上下文代码评审，处理重要问题并执行完整测试。
- [ ] 固定一例开发集的新旧实现、相同 JSON Mode 配置、原材料与参考；最多各 4 次逻辑请求，并发不超过 2。
- [ ] 网关配置拒绝、权限/计费异常或传输未知时停止队列，不重发。
- [ ] 实际预测经独立语义判卷后汇总发布率、质量、引用支持、调用和 token；金额仅在配置单价时计算。
