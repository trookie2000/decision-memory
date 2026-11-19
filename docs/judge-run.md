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

如果完整模型回复的 JSON 字符串含未转义的换行、回车或制表符，程序只补齐这三种字符的转义。字段实际字符不变，仍校验整份 JSON 和原文引文。调用回执的 `call.json_normalization` 记录策略与字符数，无需为这类问题重新调用模型。

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

`audit/source-inventory.json` 由 `evidence-first` 生成，按主题列出模型选取的原文编号。可用它核对提取遗漏是发生在原文定位还是最终合成。

`audit/field-review.json` 记录触发的字段疑点和原文字符区间。定点补引的原始回复为 `audit/02-review-2.json`，可合并的回复另存为 `audit/02-review-2.applied.json`，后者可用于回放完整答卷。

输出含本次业务原文，应留在获准的环境。公开仓库只提供实现、虚构样例和汇总。

使用 `run` 的原清单或样本包，可以离线核对答卷的输入版本和来源位置。`validate` 从答卷恢复对应阶段或完整需求，无需配置模型。

```bash
python3 -m decision_memory validate \
  --manifest examples/requirement-cache/manifest.json \
  --result runs/judge-example-001/samples/001/result.json
```

从 ZIP 运行的答卷使用同一份 `--dataset`。修改原材料后，已有答卷会因输入版本不符而无法通过校验。

## 模式与质量边界

默认 `grounded` 正常调用两次模型：先生成候选，再读取完整原文复核。模型选择整行编号，程序展开原文、位置和角色；结论、理由、边界分别核对必要实现符号。显式用户归因必须引用该字段中的真实用户来源。

在配置的 `[model]` 中加入 `grounded_strategy = "evidence-first"` 可用原文优先提取。第一步登记来源，第二步使用完整原文和来源清单合成。命令和输出定义一致，`validate` 根据保存的策略与提示摘要核对结果。用 `grounded_strategy = "draft-review"` 可显式指定正文初稿复核。两种策略的本地比较见 [实验报告](experiment-report.md#原文优先提取配对验收)。

比赛的单阶段输入使用默认策略。完整需求补充验收可用原文优先策略。两套配置使用同一命令，每次指定新的输出目录。新封存组的完整需求严格覆盖持平，原文优先少用 34.3% 输入 token。 [配对验收](experiment-report.md#原文优先提取配对验收)

`grounded_strategy = "witness-review"` 在第二次复核标出同条目的证据缺口。每个提示给出可以回查的原文编号，模型需核对规则是否生效并为该字段选择证据。用量统计方式、答卷格式和引用校验与默认策略一致，策略另有提示摘要，便于同题比对。

具名 Markdown 和普通 `用户：`、`User:` 发言在模型输入中有独立的角色和轮次。复核优先找本条已有引文、同轮近邻的理由与范围，并为后续变更单独保留候选。原文文件、行号和历史证据校验格式保持原样。代表性回归及实跑结果见 [独立盲测](experiment-report.md#发言轮与证据检索的独立盲测)。

引用产物中纯文本箭头流程的正文时，程序可补入同节点、同文件、同 fenced 流程块的标题。额外引文在 JSON 的 `evidence_context` 中保留来源记录，文档中标为“程序补充的流程标题”。程序不修改知识文字，不从其他字段或普通邻近段落补证；完整语义仍由独立评审判断。

字段自身的引文须包含相应的大写下划线常量、至少 10 的裸数值和明示时长。明示时长按单位归一化后的量匹配，例如 10 分钟与 600 秒等价，`14 calendar days` 可支持 `14 天`。裸数或工作日均不能充作自然日的证据。历史答卷按保存时的规则回放。数值所属对象和适用条件由语义复核判断。复核会检索“未说明”字段的依据、理由和边界的后续变更，以及未决问题是否已解决。

跨语言字段可得到最多两条相关原文产物候选，模型核对各行是否支持字段当前的表述。复核同时检查原文明确的并列触发、例外和部分修订后仍生效的范围。候选与原有逐字引用校验使用同一来源表。

发现明确条件、量词疑点或沿用旧定义时，将线索写入该样本的 `audit/scope-review.json`。记录可按源编号和字符偏移回查。第二次复核只接收其中的明确时限、范围疑点、一次性操作、混合状态和变更确认线索。工作日、工作小时使用各自的计量单位，“下一个工作日开门”作为起算事件核对。变更已生效的理由须引用能支持该结论的原文。历史答卷保留生成时的校验规则。

最终复核之后，程序按字段文字检索本次输入中罕见的精确中文三字片段，最多补入两行含用户发言的阶段对话或产物。每行至少覆盖两个此前未引用的锚点。无节点的补充 Markdown 可由模型用作背景证据，其来源留在输入中，程序只从阶段材料补引文和提示后续更新。JSON 的 `evidence_retrieval` 记录原选来源、补入来源和匹配锚点。Markdown 和 HTML 标为“程序检索补充引文”。保存校验会重放检索和核对证明，语义支持仍待独立确认。

每步最多修复一次完整 JSON 的结构或关联错误。非 JSON、截断、认证失败和断流均不自动重发。语义仍不确定时保存草稿，状态为 `needs_review`，不发布成功结果。两步模型判断不等于独立质量验收。

如果复核新增了数值或时限却缺少该字段的来源，现有的一次修复会列出相关原文候选。程序根据本字段的原文用词和缺失值筛选候选。候选只供模型核对，真实引文仍按字段校验。调整到另一日、次日和按时长等待一天分别核对。

复核兼容网关返回的单层 `response` 对象。原始响应保存在审计目录。内层仍须通过同一组字段、引文和待复核校验，带有额外兄弟字段的响应进入原有校验错误流程。一次真实完整需求的原始失败已通过离线重放恢复，生成 12 条知识和 2 个未决事项，新增模型调用为零。

用户归因缺少当前字段的用户原文时，修复请求会列出缺证的字段位置，例如 `items[2].reason` 或 `open_questions[1]`。模型在同一次修复中逐个补证，或按原文修正归因。

`--mode baseline`、`artifact-only` 和 `verified` 保留作对照。旧阶段 verified 正常调用三次；旧需求 verified 正常调用两次。新 `run` 对所有模式都禁用 HTTP 自动重试。旧 `batch` 的恢复及重试行为仍独立保留。

## 失败、预算和费用

输出目录必须尚不存在。重复运行会拒绝，不覆盖结果、不重发请求。

批次最多启动 32 次 HTTP；可用 `--max-http-attempts` 降低上限。已知输入达到 500,000 token、输出达到 100,000 token 后停止新请求，在途请求保留。单样本总预算为 2,700 秒。失败、修复和草稿均计入实际调用。

`unknown` 表示无法确认服务端是否完成，批次停止，后续项保持 `not_started`。认证或计费错误也停止队列。已有完整响应但格式失败的样本记为 `failed`，其余样本可继续。成功和失败并存时批次为 `partial`，CLI 返回码为 2；全部成功时为 0。

数值缺证时，如果同条目另一已核查字段有可核验的同一数值和原文，grounded 使用既有的一次修复机会请求补引。合并只追加指定字段的引文。缺少候选时保留原草稿及拒绝原因供复核。

网关未返回完整用量时，汇总 token 为 `null`，已知部分另列。未配置单价时金额为 `null`。单价只用于估算，真实积分及失败计费以网关账单为准。

MyToken 用于查看额度和实际积分明细。运行时在模板填写获准模型网关的完整 Chat Completions URL、模型及鉴权头。自定义头、认证或计费错误后的停止和回执脱敏均已通过本机 HTTP 验证。评委环境的真实入口、可用模型和扣费仍待联调。获准万擎网关已处理阶段和完整需求输入，真实用量保存在回执中。 [Token 积分使用手册](https://docs.corp.kuaishou.com/d/home/fcACT310dd-bjJU5Fi4qifj14) · [截止规则验收](experiment-report.md#截止规则的独立验收)

## 独立评测

运行报告不读取标注，也不给自己计算准确率。质量判卷在输出冻结后另行执行，人工确认与正式成绩单列。`human_review` 为 `pending`，`official_score` 为 `null`。

旧的关键词阶段投影不是官方分配规则。当前完整输入自测保留原始 K/H 参考；阶段诊断同时保留分配不明和部分条款无依据的问题。正式阶段计分须以出题方完整契约为准。

R3 打包入口已从原始 ZIP 和节点清单复现八份历史答卷，覆盖 `--scope stage` 和 `--scope requirement`。解包后使用 `run` 生成 JSON、Markdown 和 HTML，再用 `validate` 核对输入版本与原文坐标。回放仅向本机发送 HTTP 请求，并在请求内容逐项相符时返回已存档的模型回复。真实评测时使用获准网关和评委提供的输入，回放记录见[实验报告](experiment-report.md#打包-cli-的请求级回放)。

在公开 ZIP 副本中替换评测标注，并在历史节点清单副本旁增放未声明的参考文件后，八个案例的提取请求、答卷和补证请求准备稿逐项一致。独立判卷阶段按冻结的参考和已保存答卷另行评估。

## 离线候选复算

本地实验包 `runs/fullscore-definition-binding-v8/decision-memory-candidate-local.zip` 保留 R3 的运行入口，另附 `CANDIDATE.md`。解包后用 `python3 -m decision_memory_candidate project` 读取同一阶段或完整需求的原始输入和 R3 答卷，生成 `candidate.json` 与可点击原文的 `candidate.html`。可用 `verify` 按相同输入重建两份文件。复算新增模型调用为 0，候选质量仍待新封存材料和正式评委评测。

分句候选包 `runs/fullscore-clause-relations-v9/decision-memory-clause-candidate-local.zip` 提供 `CLAUSE-CANDIDATE.md` 和 `python3 -m decision_memory_clause_candidate project`，另生成可导入企业文档的 `candidate.md`。在全新解包环境中已复现单阶段和完整需求共八份真实答卷。新封存材料和评委网关仍待真实运行，当前用于本地复算。

工作区中的 `runs/source-review-windows-20261119/inspect_windows.py` 可将同一份原始输入和答卷复算为待核对的对话窗口。使用 `project` 生成单独的 JSON，使用 `verify` 从原文重放。入口接受同一 `--dataset` 或 `--manifest`、`--scope`、`--result` 和单独的 `--output` 文件路径。八份历史真实答卷已核验。输出供查看可能漏掉的原文，知识正文按真实运行结果保留。

证据复核候选包 `runs/window-evidence-repair-20261119/decision-memory-window-review-local.zip` 提供 `WINDOW-REVIEW.md`。解包后用 `python3 -m review_cli` 准备、应用或核验同一原始答卷的候选。获准网关的可选 `review` 最多新增一次模型请求，独立记录用量及失败。成功预览包含 JSON、Markdown、HTML 和费用记录，保存在 `preview/`。本机 HTTP 已验证正常、失配、断流和预算预检，真实评委环境待联调。
