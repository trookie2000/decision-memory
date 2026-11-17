# 需求级证据复核修复计划

> **For agentic workers:** 使用 superpowers:executing-plans，在当前会话实现并验证。用户已要求直接修复。

**Goal:** 检查理由引文的复核是否完成，并保留具体参数理由未知。

**Architecture:** 复用需求级 verified 的第二次调用，增加绑定当前知识的字段判定与原文明示未知清单。纯校验模块负责来源、完整性和保存后重放；语义判断保持模型复核的属性。

**Tech Stack:** Python 3.11+，unittest，无新增依赖。

**Spec:** [修复设计](../specs/2026-11-17-requirement-grounding-design.md)。

## 全局约束

- 正常 verified 两次调用，其他模式一次；现有每步最多修复一次。
- 旧结果、gold、Mario 评分保持原样；新结果 schema 2，兼容旧 schema 1。
- 只用源材料，未知不升级为已确认决定。
- 后台进程直接执行，不使用 IDE 终端、键鼠或远程控制。
- 沿用当前专用分支，保持本轮 Weaver 归属；提交前核对采集。

## Review Focus

- 当前知识最新事件是 merge 时，字段复核仍绑定实际有效文字。
- 原因未知后来已解决或原规则已取消，要求更晚节点依据。
- artifact-only 不接触对话来源，baseline 不冒充语义复核完成。
- 新结果删掉复核数据后重算文件摘要，离线验证仍拒绝。
- 模型返回 unsupported、重复或缺少判定时，不能保存为成功结果。

## Task 1：结构化复核及持久化

**Files:** 新建 `decision_memory/requirement_grounding.py`、`tests/test_requirement_grounding.py`；修改 `requirement_pipeline.py`、`storage.py`、`batch_gate.py` 和需求级测试。

**Interfaces:** `reason_obligations(sample)` 只收集原文明示未知。`validate_grounding_review(value, compiled, sample)` 校验字段清单和理由未知处理，并生成当前知识摘要绑定。`extract_requirement` 保存 schema 2 和复核结果；`validate_requirement_saved` 重算校验。

- [x] 写失败测试：旧 verified 接受没有字段判定的回复，新协议应拒绝。
- [x] 写脱敏输入与受控回复：修复不支持理由的引文，保留具体参数理由未知；断言真实重放输出及两次调用。
- [x] 增加缺项、重复、unsupported、越序、当前 merge、旧结果和篡改场景。
- [x] 运行定向测试，记录实际失败。
- [x] 实现最小协议及需求级流程集成；不改阶段提取和评分。
- [x] 运行定向测试，确认通过。

## Task 2：说明和完整验证

**Files:** 修改 `docs/requirement-memory.md`，本地修复验证记录保存到新 runs 目录。

**Interfaces:** 使用 Task 1 的结果字段，说明模型判定与程序检查的区别。

- [x] 补充复核协议、模式边界、调用预算和旧结果兼容说明。
- [x] 运行完整 unittest 测试及源码 diff 检查。
- [x] 检查原运行文件哈希未变，离线校验六例旧原始预测；不恢复旧冻结批次。
- [x] 用独立审查检查整份改动；修复实质问题并补失败回归。
- [x] 提交前运行 Weaver 预览，仅对可确认的本轮漏采使用官方恢复；报告实际状态。
