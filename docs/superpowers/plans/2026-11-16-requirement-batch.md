# Requirement Batch Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session. User has approved the described next step; continue inline and use one fresh final reviewer. Tests follow RED→GREEN. Audit Weaver before committing; keep private run evidence out of Git.

**Goal:** 让现有 Mario 员工准备 6 个整需求，冻结后对同一原文的逐节点和整需求提取做分轨评测。

**Architecture:** 扩展 Battle/Series 的 requirement 分支，复用原派发与恢复保护。独立模块处理整需求回传、比较结果和冻结门槛，复用 v2 评测。

**Tech Stack:** Python 3.11+，标准库，现有 ModelClient/Mario MCP/Docs CLI。

**Spec:** `docs/superpowers/specs/2026-11-16-requirement-batch-design.md`

## Global Constraints

- 原测试群、原两位员工；后台执行，不操作键鼠，不启用远控。
- 保留旧阶段协议、旧 v1 分数与所有旧实验。新增 `stage=requirement`。
- 原文最多 64 节点、每份 2 MB、总量 8 MB；gold、brief、审题反馈不进入提取请求。
- 新批次 6 例全部通过审题并冻结，再开始模型提取；争议和未知发送不算完成。
- 只重编比较视图的 ID 和来源，不改写或去重原始预测内容。
- 评分使用 v2，未知信息单列；人工待确认，正式分数为空。

## Review Focus

- Battle 直接 resume 不能绕过批次冻结门槛，改变模型配置或代码后也不能偷换实验版本。
- 旧节点的未决项后来得到确认时，机械拼接仍原样保留，由评分指出过期；程序不能替 baseline 改答案。
- 同一行出现在多个节点时，gold 的 node_id 与 source 必须真正对应指定材料。
- 两种方法的预测摘要、匿名评分视图及参考分类都应绑定；不能只验证其中一种方法。
- 部分调用成功后崩溃不能自动重复付费；完整已保存比较包可校验接回。

### Task 1: 整需求回传协议与来源

**Files:** Create `decision_memory/requirement_protocol.py`, `tests/test_requirement_protocol.py`; modify `decision_memory/battle_protocol.py`, `decision_memory/requirements.py`.

**Interfaces:** `read_requirement_manifest(path) -> (requirement_id, node_tuples)`；`parse_requirement(payload, references, sample_id) -> dict`；`reference_policy(references) -> dict`。输出 normalized requirement、references 和 sample，旧 parse_document 不变，requirement 分支使用新解析器。

- [x] 写测试：多节点正文与 decision/uncertainty 参考校验、错误节点引文、未来已解决未知项仅由评审判断、重复节点和超限、旧协议兼容。
- [x] 运行 `python -m unittest discover -s tests -p 'test_requirement_protocol.py' -v`，确认缺少整需求协议而失败。
- [x] 实现原文读取复用、严格节点参考校验与员工提示协议。
- [x] 运行重点与完整套件，确认通过。

### Task 2: 同材料比较与离线绑定

**Files:** Create `decision_memory/comparison.py`, `tests/test_requirement_comparison.py`; modify `decision_memory/cli.py`.

**Interfaces:** `run_comparison(sample, nodes, config, mode, output) -> dict`；`validate_comparison(bundle, sample, nodes, root, config=None) -> dict`；CLI `requirement compare` 输出新目录中的 prediction.json、whole.json 和 node 原始结果。

- [x] 写真实 HTTP/CLI 测试：两节点正常 3 次调用，gold 未进入请求，旧规则在拼接中保留，来源转换不改字段，输出不可覆盖，原始或比较视图改动报错。
- [x] 运行对应测试，预期缺少比较模块或命令而失败。
- [x] 实现模型隔离调用、原始结果保存、机械拼接、随机持久 A/B 标签与摘要校验；未知/失败调用不自动重做。
- [x] 运行重点与完整套件，确认通过。

### Task 3: Battle/Series 冻结和 v2 评分

**Files:** Modify `decision_memory/coordinator.py`, `decision_memory/series.py`, `decision_memory/cli.py`; create `decision_memory/batch_gate.py`, `tests/test_requirement_battle.py`, `tests/test_requirement_series.py`.

**Interfaces:** Battle 接受 stage=requirement，调用比较子进程；作者回传保存普通需求清单，评分导入每个匿名视图的 v2 判定。Series requirement 两遍运行并写 frozen-batch.json；`verify_batch_gate` 在每次提取和直接恢复前核查所有输入、分类、模型配置、提示词与代码摘要。

- [x] 写测试：两轮返修后冻结、引用和策略隔离、评分双视图绑定、离线 audit、防重发、全部 case 冻结后才提取、争议停止、恢复绕过和版本变化拒绝。
- [x] 运行对应测试，确认缺少 requirement 支持而失败。
- [x] 实现状态机分支和批次门槛，保持旧阶段行为；更新运行报告。
- [x] 运行重点与完整套件，确认通过。

### Task 4: 新批次与真实验收

**Files:** Create `examples/requirement-cases.json`; modify `docs/coordinator.md`, `docs/requirement-memory.md`, `README.md`；本地记录写新 runs 目录。

- [x] 固定 6 个新虚构场景，保留旧任务；更新示例和不覆盖的恢复方法。
- [x] 完成一次独立全改动审查，修复重要问题并回归；核对 Weaver 后提交。
- [x] 用公共工具检查真实 Docs/Mario/模型调用；明确失败或未知不重发。
- [ ] 预检和最小员工健康检查均通过后运行 6 例；若平台阻塞，保存真实证据及批次状态并如实报告，不能声称已经完成评测。
- [ ] 汇总实际质量、成本和待人工项。只发布已验证的结论。

## 起点

本轮从 e2ce6c2 开始，沿用 codex/decision-memory 分支及现有依赖，用户已授权按上述流程推进。

## 2026-11-16 验证状态

工程接入和四项复核修复已通过 204 项测试。已使用真实模型对既有虚构三节点示例完成同材料比较，并离线核验原始文件和摘要；这不是新六例的质量评测，没有语义分数。

新六例已创建本地可恢复批次，准备 0/6、评分 0/6，尚未向员工派发。两位员工的一次性文字健康检查均收到 custom 卡片，当前公开接口无法读取正文，未取得预期文字确认；只读 Mario 查询也报告群历史读取权限被拒。后台浏览器 host 不可用。保留阻塞证据，不代替员工编题、评分或重发未知请求。公共 Docs 读取通过，新的公共创建入口尚未在真实任务中验证。
