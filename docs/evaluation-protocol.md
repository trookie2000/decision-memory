# 决策与未知信息的评测口径

本协议是本地核查口径，不是比赛官方计分。人工确认和正式成绩始终与 Agent 初评分开。

## 为什么新增 v2

S003 的原始预测有 3 个 items 和 1 个 open_question。旧程序仅为 items 接收 judgments，因此统计参考匹配 4/5；真实评审正文却已经对 open_questions 中的“2 秒理由未说明”给出正确、引用支持的明确判定。旧 80% 不能解释为模型漏提 20%。

另外，参考 s003-004 包含重试参数和上限后处理，分别由 K002、K003 表达。匹配同一个参考 ID 只能说明有关联，不能单凭 K002 的前半部分就宣称整条参考已覆盖。

为此新增显式选择的评测表 `schema_version=2`。旧表／旧分数、S003 原始预测和已冻结参考不变。`battle` 的旧有评分协议仍使用 v1，不会恢复一个已完成任务来迁移分数；v2 用新的文件和独立 review 命令。

## 两类输出与两类参考

| 对象 | 核查内容 | 不应做什么 |
|---|---|---|
| items | 最终有效决定及其理由、边界是否正确；每个字段给出的引文是否足够 | 用“原文其他地方大概提到过”代替字段引用支持 |
| open_questions | 未决、冲突、明确未说明的信息是否被如实保留 | 把已确认数值的理由未说明，解释为该数值本身未确认 |
| decision 参考 | 有效决定的结论／理由／边界检查点 | 强迫模型按照参考数量拆分输出，或把未知项凑进准确率分母 |
| uncertainty 参考 | 重要未知信息或阶段待确认边界 | 通过 open_questions 提高有效决定的覆盖率 |

未知理由也可以在已有 item.reason 中保持“未说明”，无需强行新增 open_question；必须明确指出该检查点对应的字段，且不能把未知事实当作有效 conclusion。Q001、Q002 等编号只在评测表里按原数组位置生成，不写入原始预测。

## 显式覆盖判定

每个预测输出分别填写 correct、citation_support、matches、notes。未审保持 null。引用存在性由程序验证；引用是否足以支持结论、理由、边界，是不同的语义判断。

每个参考另填写 reference_judgments：

- full：支持的原始预测字段合起来覆盖完整参考及必要条件。
- partial：仅部分覆盖，例如只有重试次数，没有上限后的处理。
- absent：预测未覆盖该参考。
- null：尚未判断，不能当作未覆盖或通过。

`supported_by` 必须指向正确、引用支持且明确 matches 该参考的输出。item 链接必须指定 conclusion、reason 或 boundary；open_question 链接必须指向真实 Q 编号。一个复合参考可由多个输出联合支持，分母中仍只计一个参考。

只有 full 才计入覆盖率分子；partial、absent 和 pending 单独计数。不自动从 matches 推导 full。相关预测及参考尚未审完时，覆盖率保持 null。决策和未知信息分别报告，不提供一个混合的“总 recall”。

## 冻结与防止事后改答案

`reference_policy` 包含固定参考摘要以及每个参考的 decision／uncertainty 分类、理由。新样本应在提取前固定分类与参考；原始材料、参考、政策、模型配置以 manifest 记录版本。

评测表完整性摘要包括原始 items、open_questions、参考、政策和预测摘要。`review apply` 只接收 judgments，不接受预测副本；改动这些固定内容会拒绝计分。输出路径必须不存在，不覆盖旧评分。

S003 的 v2 结果是根据已存真实评分作的回溯口径整理，不是重新测试，更不是模型质量提升。旁路策略写明 `posthoc_metric_reconciliation` 和非正式 benchmark，原 v1 的 4/5 留存。主会话转录出来的支持指针不冒充新的员工回复。

## CLI

准备分类政策（references_digest 用 `decision_memory.evaluation.digest` 计算，不能随手填写）：

```json
{
  "schema_version": 1,
  "references_digest": "真实 references 数组的规范 JSON 摘要",
  "assignments": [
    {"reference_id": "R1", "kind": "decision", "rationale": "最终有效决定"},
    {"reference_id": "R2", "kind": "uncertainty", "rationale": "本阶段理由未说明"}
  ]
}
```

在已有原始预测基础上创建新的表，不调用模型：

```bash
.venv/bin/decision-memory review init --schema-version 2 --review-kind agent \
  --result /absolute/path/prediction.json \
  --references /absolute/path/references.json \
  --reference-policy /absolute/path/reference-policy.json \
  --output /absolute/path/worksheet.v2.json
```

导入明确判定并生成新文件：

```bash
.venv/bin/decision-memory review apply \
  --worksheet /absolute/path/worksheet.v2.json \
  --judgments /absolute/path/judgments.json \
  --open-question-judgments /absolute/path/open-question-judgments.json \
  --reference-judgments /absolute/path/reference-judgments.json \
  --output /absolute/path/review.v2.json
.venv/bin/decision-memory review score --worksheet /absolute/path/review.v2.json
```

参考完整覆盖示例：

```json
[
  {
    "reference_id": "R1",
    "coverage": "full",
    "supported_by": [
      {"target": "item", "id": "K001", "field": "conclusion"},
      {"target": "item", "id": "K002", "field": "conclusion"}
    ],
    "notes": "两个原始输出分别覆盖参考前半和后半，合起来完整。"
  },
  {
    "reference_id": "R2",
    "coverage": "full",
    "supported_by": [{"target": "open_question", "id": "Q001"}],
    "notes": "原始未决项如实保留未说明的信息。"
  }
]
```

导入已有员工按文本评分的 open_questions 时，`align_question_judgments` 只接受原预测文本的唯一逐字匹配，不做模糊对齐，不根据“无遗漏”等摘要替代逐条判定。没有明确评分的项保留 null。

## 官方阶段投影规则

仅以当前两份输入可见的事实为必命中参考。同一事实的 K/H 别名合并；不要把需求分析中未知的 Redis 结构、批量接口条数、弱一致降级等下一阶段设计当成本阶段必须提取的事实。

参考可以包含多个密切相关检查条件，但应说明 full 所需条件。已有接口兼容约束与新增接口细节仍待确认可能同时成立，不能为消除表面矛盾擅自扩大成“永远不许新增接口”。

没有真实模型预测时，输入、引文和隔离检查可以完成，但正确率／覆盖率保持未知。不得用当前会话编写的答案或 fixture 代替独立提取结果。
