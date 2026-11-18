# 评委本地运行

需要 Python 3.11 或更新版本，以及可用的 Chat Completions 网关。运行时只用标准库，无需 Mario、MyFlicker 或本地远控。

## 一条命令运行

在仓库根目录，复制 `config.example.toml` 或 `config.mytoken.example.toml`，填入获准使用的网关和模型。配置中只写环境变量名，凭证放入运行进程的环境。

```bash
python3 -m decision_memory run \
  --dataset /absolute/path/public-samples.zip \
  --scope stage \
  --config config.local.toml \
  --output runs/judge-stage-001
```

`--scope stage` 分别处理包内每个阶段，只读取该阶段的对话和最终产物。`--sample-id` 可选择一个阶段。程序按固定文件名识别输入，不读取 `评测标注` 正文。

完整需求使用 `--scope requirement`，按分析、设计顺序读取明确提供的全部节点。可用 `--requirement-id` 选定一个需求。完整需求的回顾性自测与单阶段比赛任务分开报告，不能将其结果混为同一口径。

自定义文件使用有序清单。虚构样例可直接运行：

```bash
python3 -m decision_memory run \
  --manifest examples/requirement-cache/manifest.json \
  --scope requirement \
  --config config.local.toml \
  --output runs/judge-example-001
```

无节点补充材料通过 `supplements` 声明，使用 `--scope requirement` 一起读取。来源标明补充材料类型，节点和顺序留空。逐节点运行使用没有全局补充材料的清单。格式见[完整需求提取](requirement-memory.md)。

## 输出

| 文件 | 用途 |
| --- | --- |
| `report.json` | 批次状态、版本、输入哈希、用量、失败和未启动项 |
| `report.md` | 可交接的运行文档 |
| `report.html` | 本地报告，可用浏览器打印为 PDF |
| `samples/001/result.json` | 结构化知识与字段证据 |
| `samples/001/result.md` | 单样本知识文档 |
| `samples/001/result.html` | 点击来源编号查看真实原文 |
| `samples/001/calls/` | 实际请求正文、完整响应、失败诊断和调用回执 |
| `samples/001/audit/` | grounded 的两个步骤及协议修复记录 |

输出含本次业务原文，应留在获准的环境。公开仓库只提供实现、虚构样例和汇总。

## 模式与质量边界

默认 `grounded` 正常调用两次模型：先生成候选，再读取完整原文复核。模型选择整行编号，程序展开原文、位置和角色；结论、理由、边界分别核对必要实现符号。显式用户归因必须引用该字段中的真实用户来源。

引用产物中纯文本箭头流程的正文时，程序可补入同节点、同文件、同 fenced 流程块的标题。额外引文在 JSON 的 `evidence_context` 中保留来源记录，文档中标为“程序补充的流程标题”。程序不修改知识文字，不从其他字段或普通邻近段落补证；完整语义仍由独立评审判断。

最新必要检查还包括字段自身的大写下划线常量、至少10的裸数值和明示时间量。只做精确字面及时间单位等价检查，不能代替语义验证。复核会检索“未说明”字段的可能依据、理由/边界的后续变更和问题是否已解决，不自动采用提示。

最终复核之后，程序按字段文字检索本次输入中罕见的精确中文三字片段，最多补入两行含用户发言的阶段对话或产物。每行至少覆盖两个此前未引用的锚点。无节点的补充 Markdown 可由模型用作背景证据，其来源留在输入中，程序只从阶段材料补引文和提示后续更新。JSON 的 `evidence_retrieval` 记录原选来源、补入来源和匹配锚点。Markdown 和 HTML 标为“程序检索补充引文”。保存校验会重放检索和核对证明，语义支持仍待独立确认。

每步最多修复一次完整 JSON 的结构或关联错误。非 JSON、截断、认证失败和断流均不自动重发。语义仍不确定时保存草稿，状态为 `needs_review`，不发布成功结果。两步模型判断不等于独立质量验收。

`--mode baseline`、`artifact-only` 和 `verified` 保留作对照。旧阶段 verified 正常调用三次；旧需求 verified 正常调用两次。新 `run` 对所有模式都禁用 HTTP 自动重试。旧 `batch` 的恢复及重试行为仍独立保留。

## 失败、预算和费用

输出目录必须尚不存在。重复运行会拒绝，不覆盖结果、不重发请求。

批次最多启动 32 次 HTTP；可用 `--max-http-attempts` 降低上限。已知输入达到 500,000 token、输出达到 100,000 token 后停止新请求，在途请求保留。单样本总预算为 2,700 秒。失败、修复和草稿均计入实际调用。

`unknown` 表示无法确认服务端是否完成，批次停止，后续项保持 `not_started`。认证或计费错误也停止队列。已有完整响应但格式失败的样本记为 `failed`，其余样本可继续。成功和失败并存时批次为 `partial`，CLI 返回码为 2；全部成功时为 0。

网关未返回完整用量时，汇总 token 为 `null`，已知部分另列。未配置单价时金额为 `null`。单价只用于估算，真实积分及失败计费以网关账单为准。

MyToken 模板的自定义头已通过本机真实 HTTP 夹具验证。真实 MyToken 入口、模型与积分尚未独立实测；需要使用你获准的网关说明填写，不能把模板地址当成服务。

## 独立评测

运行报告不读取标注，也不给自己计算准确率。质量判卷在输出冻结后另行执行，人工确认与正式成绩单列。`human_review` 为 `pending`，`official_score` 为 `null`。

旧的关键词阶段投影不是官方分配规则。当前完整输入自测保留原始 K/H 参考；阶段诊断同时保留分配不明和部分条款无依据的问题。正式阶段计分须以出题方完整契约为准。
