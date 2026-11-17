# Submission Readiness Implementation Plan

> REQUIRED: execute task-by-task with superpowers:executing-plans. User opted out of approval questions and authorized completion; do not ask again for routine choices.

**Goal:** 修正可复用知识与证据质量，形成官方开发集的真实验收及可独立运行的评委包。

**Spec:** `docs/superpowers/specs/2026-11-17-submission-readiness-design.md`

**Architecture:** 独立判卷固定旧输出，范围审计单列；新增 grounded 最终快照模式，明确阶段/需求输入，程序绑定真实整行证据，两次正常调用；评委 run 命令与离线报告不读取标注。

## Constraints

- 所有工具操作在后台 Node 子进程执行；禁用终端界面、键鼠、远控和 CUA。
- 不再由 Agent push 或登录 GitHub。只做本地提交，给用户推送命令。
- 不调用 Mario、MyFlicker 或发群消息；保留其原评分。
- 凭证只来自用户明确提供的值，进程内存注入；公开仓库不含业务材料、标注或凭证。
- 模型预算与未知停止严格按 spec；不把不可用输出当零分或高精度。

## Review Focus

- 新阶段模式与旧三模式、整需求命令的兼容及签名绑定。
- 伪造编号、字段借证、用户归因、完整未知与部分未知；不得靠空白字段或删除规则过关。
- 评委入口不读取标注；输出 HTML 转义、目录覆盖、断流不重发及敏感信息脱敏。
- 判卷身份、固定参考、各阶段隔离、来源十条抽样和成本分母。
- 新代码、真实预测、已有对照版本分别报告，未达标不宣传通过。

### Task 1: 补齐固定官方结果的独立判卷

**Files:** 私有 `runs/submission-readiness-20261117/`，公共差距说明在现有 docs。

**Interfaces:** 每阶段匿名 packet、官方 K/H/P 参考、source-catalog；每模式完整官方口径 judgment 与逐字段诊断，旧 v2 初始表保留。

- [x] 核对 12 份旧预测及原材料，冻结匿名输入、参考、版本和摘要。
- [x] 四个阶段分别独立判卷，不看未来阶段、其他评分或映射。
- [x] 用现有 official_eval 校验主判断，独立记录未决项；汇总指标，标明 37 个阶段位置仅为本地投影诊断，另保留独立范围审计。

### Task 2: 显式范围的 grounded 质量模式

**Files:** 新建阶段来源与复核模块、测试；修改 pipeline/CLI/storage 的最小接口；保留旧提示作为对照。

**Interfaces:** `extract_grounded(sample, client, run_dir)`，阶段和完整需求的最终知识快照；整行来源编号编解码、逐字段必要证明、离线回放校验；旧接口继续可用。

- [ ] 写失败测试：编号展开、所有无效编号定位、两步修复预算、用户归因、字段自身符号、未知、旧模式和整需求误路由。
- [ ] 实现来源绑定和语义复核；无依据的泛化收窄，保留已知理由和明确未知。
- [ ] 目标及全量测试通过，核对 Weaver 后本地提交。

### Task 3: 评委本地运行入口与自动报告

**Files:** run 调度、离线 HTML/Markdown 渲染、CLI、MyToken 配置示例及真实 HTTP 测试。

**Interfaces:** `run --dataset --config --output`；结果与运行报告分开，评分只在单独评测侧。

- [ ] 写真实本地 HTTP 失败测试：标准 ZIP、多样本、部分失败、未知停止、覆盖保护、鉴权脱敏、HTML 转义、用量未知。
- [ ] 实现一次运行生成 JSON/Markdown/HTML 和批次记录；不增加常规模型调用。
- [ ] 验证本地样例及可复现包；目标与全量测试通过后本地提交。

### Task 4: 真实验收、消融与交付

**Files:** 私有冻结实验及报告；公开方案、实验汇总、评委说明和 AI 协助记录。

- [ ] 冻结当前实现与统一配置，有限对照及新保留材料验收；逐次调用可核实。
- [ ] 独立判卷，按三门槛修复并复测；已见材料重新归入开发。
- [ ] 补来源机制消融与成本分析；不能用格式通过或草稿代替质量。
- [ ] 一次新上下文代码评审，重要问题 RED→GREEN，全量测试及材料检查通过。
- [ ] 本地提交并打包；报告本地自测范围、未决项和手动推送命令，不执行 push。
