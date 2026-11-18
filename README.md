# Decision Memory

面向命题 07 的本地 CLI。数据集提取默认读取完整需求的有序节点和补充 Markdown。评委可显式选择单阶段或完整需求，输出有效决策、理由、适用边界、未决事项及逐字段证据。grounded 生成最终知识快照，事件模式保留更新和失效历史。

Python 3.11+。运行时仅使用标准库。评委 `run` 生成 JSON、Markdown、HTML 和调用回执，可供人工核查或下一阶段 Agent 读取。

评委可用 `python3 -m decision_memory run --dataset 样本.zip --scope stage --config 模型.toml --output 新目录`。
一次生成 JSON、Markdown、HTML 和真实调用回执；点击 HTML 来源可查看原文。完整需求改用 `--scope requirement`。
配置、失败处理和报告说明见 [评委本地运行](docs/judge-run.md)。运行报告只记录实际执行，质量在固定输出后独立判卷。

实验版本 `941ff4f` 的两个完整需求合计严格覆盖 24/32（75%）、条目准确 24/26（92.31%）、固定十条来源抽查 10/10，达到本地三门槛。另一份解释材料的迁移检查未通过。合并后的输入协议和提示需要重新做真实模型验收，正式单阶段判卷和人工确认仍待完成。

这次来源补充的成对实验保持正文和模型调用不变，严格覆盖从 23/32 提高到 24/32，未使既有条目的引用支持退化。早先单独判卷的 22/32 原件保留，不能把判卷差异都算成工程收益。各版本、消融、失败和完整成本见 [grounded 自测](docs/grounded-evaluation.md)，提交差距及后续优先级见 [提交前验收](docs/submission-readiness.md)。

## 历史接口与实验记录

2026-11-17 字段证据改造（工程 0.4.3）：新增主动运行的 `field verify`，使用独立原文回答修订实际答案，保留原预测、证据重绑、理由/边界修订及待复核队列。新增 `field freeze/run/status`，冻结同材料的前后对照并匿名判卷。全量 411 项测试及 157 份历史文件只读审计通过；真实模型预检尚缺凭证，新增请求为 0，不能宣称提分。用法、来源、额外调用成本和验收约束见 [字段证据验证](docs/field-evidence-quality.md)。

2026-11-17 补充材料整合（工程 0.4.2）：事件策略也默认读取需求目录中的额外 Markdown，显式清单可通过 `supplements` 声明。背景事件不编造节点，只允许新增无冲突事实、合并证据或补齐未说明字段，不能据此改写已确认内容。默认提取与两版可选审计均接入材料身份和保存校验。含全局补充文件的整需求/逐节点 `compare` 暂在调用前拒绝，避免两侧使用不同材料。全量 376 项测试及 157 份历史文件只读审计通过，没有真实模型新分数；详见 [完整需求说明](docs/requirement-memory.md#无节点补充材料的事件)。

2026-11-17 输入修复（工程 0.4.1）：grounded 现在通过共用输入描述识别目录、ZIP、显式清单和单阶段，保留真实节点与补充材料类型；两条数据集 grounded 命令读取相同目录时发送相同材料。新增 grounded 逐字来源协议 v2，v1 结果仍可离线校验，不能直接复用为新版本运行。此处的 grounded 协议 v2 与实验开关 `--evidence-audit v2` 是两个不同协议。全量 351 项测试通过，本轮没有真实模型新分数。

2026-11-17 合并更新：保留目录式需求输入及显式清单、记忆事件、按需证据审计两条流程，兼容旧四文件员工回传与新匿名 A/B 评分。整合后 338 项自动测试通过；G002～G004 的 157 份历史文件重新通过只读审计。此结果证明工程兼容性，不代表合并版本已有真实模型新分数。

2026-11-17，工程版本 0.4.0 增加材料类型与阶段顺序分离、跨节点失效检查、可重放决策历史和程序核查队列。数据集入口默认按需求合并，旧单阶段用法需显式传 `--unit stage`。实现范围、验证和尚未完成的优化见 [输入与状态改造记录](docs/input-state-upgrade.md)。本轮尚未运行真实模型或新增独立评分，历史分数不能视为新版本成绩。
新增 `requirement inspect/extract/validate/compare`，可通过清单读取分析、设计、编码等节点，或组合现有数据集中的分析与设计材料。需求级 `verified` 正常调用 2 次模型；旧阶段流程保持原行为。整需求协调器已接入原员工出题与 v2 分轨评分，批次先冻结所有题目，再比较整需求与逐节点方法。工程验证与真实模型效果分别记录，操作说明见 [完整需求提取](docs/requirement-memory.md) 和 [整需求六例对照](docs/coordinator.md#整需求的六例对照)。

2026-11-17 新增主动开启的 `--evidence-audit v2`：来源编号由程序展开，复核前提供字段线索，并校验字段自身引文中的实现符号。297 项自动回归通过。有限真实对照中，v2 的 3 个任务尚无正式发布结果，继续保留为实验功能，默认流程不变。独立判卷、失败及用量见 [v2 验收记录](docs/evidence-audit-v2-evaluation.md)。

2026-11-13，原 S004～S009 批次已完成 6/6。两位真实 Mario 员工完成出题、审题和评分，CLI 独立调用 Kimi 提取。S006、S008 经两轮修订。共保存 28 条决策和 8 条未决项；28 条决策均获员工肯定初评，人工确认仍待完成。逐例记录见本机 `runs/continuous-six-20261112/evaluation-summary.md`。

后续已完成官方附件四阶段的真实 baseline 提取，保存 36 条决策和 10 条未决项。四份输出均通过输入版本、保存完整性和逐字引用校验，语义评审仍需核查。实测配置和范围见 [官方样本记录](docs/official-evaluation.md)。

流式接收和推理强度配置已补齐，123 项自动测试通过。旧六例的 310 份受保护文件仍通过离线审计，S003 也未改动。工程测试、员工初评和正式比赛成绩分别记录。提交前还需人工语义核查、补齐阶段参考和三个模式对照，见 [提交前验证](docs/submission-readiness.md)。

连续生成和评测使用 `series start/resume/status/stop`。它启动独立后台进程，完成一个 case 后自动进入下一个。启动前检查模型、Mario 和 Docs；遇到未知发送或无效回传时保存状态并停止。原六例已经结束，不需再次恢复。新批次与阻塞恢复见 [连续批次说明](docs/coordinator.md#连续后台批次)。

大任务使用独立 Docs 输入文档和短 KIM 通知，回传时校验任务与输入摘要。明确的 `MESSAGE_TOO_LARGE_TO_SEND` 拒绝可用 `battle recover-delivery` 恢复。已有预测可继续评分；新增模型提取需要启动进程继承本地 `WQ_API_KEY`。

CLI 已实现三个提取模式、逐字段证据校验、有限重试、批量恢复和评测表。协调器支持最多三轮返修、持久状态和预测与参考隔离。员工调用使用指定测试群与现有两位员工；群内回复尚未独立回读，发送回执与员工 Docs 结果分别保存。

历史接口错误、实际恢复过程与评分限制见 [实验报告](docs/experiment-report.md)。运行这些命令无需开启 MyFlicker 远程控制。

## 安装与离线检查

在仓库根目录运行，无需安装即可使用。

```bash
python3 -m decision_memory --help
python3 -m unittest discover -s tests -v
python3 -m decision_memory inspect --dataset data/public-samples.zip
```

如需 `decision-memory` 命令，可安装到项目独立环境。

```bash
uv venv --python python3 .venv
uv pip install --python .venv/bin/python -e .
.venv/bin/decision-memory --help
```

本机已将题面附件保存到 `data/public-samples.zip`，该目录被 Git 忽略。其他机器可从命题文档下载自测 ZIP，或传入解压目录。默认每个需求形成一条输入；读取其 `上下文/` 与 `产物/` 下所有 Markdown 文件，并跳过评测标注目录。已知的需求分析和技术设计文件建立阶段顺序，额外文件标为补充材料且 `node=null`，包括放在产物目录的额外文件。未知文件不根据名称或编号推断时间。单阶段模式仅读取对应的已知文件对。

## 配置模型网关

复制 `config.example.toml` 为 `config.local.toml`，填写已获准使用的完整 Chat Completions URL 与模型名。环境变量名写入配置，密钥保存在当前进程环境中。

```bash
cp config.example.toml config.local.toml
# 在本地编辑 config.local.toml。
# 将密钥设置到配置中 api_key_env 指定的环境变量。
```

接口需要接受 `model`、`messages`、`stream` 和 `max_tokens`。默认使用非流式响应，读取 `choices[0].message.content`。正常完成时 `finish_reason` 必须为 `stop`；过滤、拒绝、工具调用和缺少结束状态均会返回错误。

网关支持 SSE 时，可在模型配置中设置 `stream = true`。客户端收齐完整文本和结束标记后才校验、保存结果。新 `run` 和 grounded 禁用 HTTP 自动重试，断流保留未知执行回执并停止后续请求。`timeout` 同时限制连接、响应头和正文的总时长。`reasoning_effort` 默认为不发送，取值须由所用接口支持；例如本机 Kimi 官方样本实测使用 `"low"`。推理强度参数的模型差异可参考 [Moonshot 接口校验说明](https://github.com/MoonshotAI/Kimi-Vendor-Verifier/blob/main/tests/k3_features/test_thinking_effort.py)。

支持 `Authorization: Bearer` 或 `headers_env` 指定的自定义请求头。下面填写的是环境变量名。

```toml
[model]
endpoint = "https://your-approved-gateway.example/chat/completions"
model = "your-model-name"
api_key_env = ""
headers_env = { "x-token" = "MYTOKEN_APP_TOKEN", "x-username" = "MYTOKEN_USERNAME" }
```

Mytoken 相关接入文档展示了自定义请求头的 Chat Completions 接口。本项目需要使用自身获准的入口和凭证，具体可用模型以该入口为准。旧万擎项目曾因服务树配置缺失返回 HTTP 402，后续已切换至另一个获准使用的部门项目并验证真实提取。网关调用方法请参考组织内部接入说明。

HTTP 错误会显示网关返回的错误码、说明和请求 ID，并屏蔽配置中的凭证。HTTP 402 的具体原因以网关说明为准。修正配置后，可使用 `batch --resume` 重试失败样本。

新配置模板开启 `json_mode`，若网关不支持 `response_format=json_object` 则关闭。模板给出 600 秒、32,000 输出 token、1,000,000 输入字符和零 HTTP 重试的建议边界，须适配实际模型限制；输入字符上限不等于模型上下文 token 容量。`temperature` 默认不发送。费用单价可配置为每百万输入和输出 token 的价格，未配置时 `estimated_cost` 为 `null`。缓存优惠、平台积分和失败计费以网关账单为准。

## 提取和比较

处理完整需求，先运行虚构示例的离线检查。提取、校验及数据集用法见 [完整需求提取](docs/requirement-memory.md)。

```bash
python3 -m decision_memory requirement inspect \
  --manifest examples/requirement-cache/manifest.json
```

只运行一个官方阶段，不读取其他阶段正文或评测标注：

```bash
.venv/bin/decision-memory extract --dataset data/public-samples.zip \
  --unit stage \
  --sample-id REQ-20261012-auction-heat-analysis \
  --config config.local.toml --mode baseline --output runs/official-one/result.json
```

单个阶段可使用任意 UTF-8 Markdown 文件。

```bash
python3 -m decision_memory extract \
  --dialogue /absolute/path/dialogue.md \
  --artifact /absolute/path/design.md \
  --stage design \
  --config config.local.toml \
  --mode verified \
  --output runs/example/result.json
```

批量处理官方格式样本，每个需求输出一份结果。旧阶段批次使用 `--unit stage`，已保存结果的 `validate` 会按结果中的阶段类型自动选择输入。

```bash
python3 -m decision_memory batch --dataset data/public-samples.zip \
  --config config.local.toml --mode baseline --output runs/baseline
python3 -m decision_memory batch --dataset data/public-samples.zip \
  --config config.local.toml --mode verified --output runs/verified
python3 -m decision_memory batch --dataset data/public-samples.zip \
  --config config.local.toml --mode artifact-only --output runs/artifact-only
```

| 模式 | 输入及处理 | 常规模型调用数 |
|---|---|---:|
| baseline | 需求全部材料，一次生成条目 | 1 |
| verified | 识别决策变化、生成条目、全文复核 | 3 |
| artifact-only | 只提供产物目录材料，额外文件仍标为 supplement | 1 |

旧接口每个步骤的格式或引用错误最多修复一次。未显式配置的旧客户端缺省为两次 HTTP 重试、180 秒、120,000 输入字符和 12,000 输出 token；复制的新模板使用上一节的建议值。新 `run` 及 grounded 无论配置如何都关闭 HTTP 自动重试。长材料超过配置上限时明确失败，不静默截断；可调整获准配置或按真实输入范围拆分。

## 结果与恢复

每条结果包含 `conclusion`、`reason`、`boundary` 三个字段，每个字段有 `text` 和 `evidence`。证据包含原文编号、逐字引文、源文件、行号和对话消息编号。理由或边界无依据时为“未说明”。`open_questions` 单独记录未决事项。

verified 模式还输出 `decisions`，保留 `active`、`superseded`、`pending`、`conflict` 状态。`review_notes` 记录模型复核意见。`checks` 区分逐字引用校验、模型复核和人工评审。引用存在性校验与语义支持度需要分别核对。

需求级 verified 结果还包含 `input_documents`、`decision_history` 和 `review_queue`。后续明确决定可以替换早期产物；代码仅在同议题、同适用范围且有后续用户/阶段产物依据时执行失效处理，保留原记录与替换依据。材料冲突或未经证实的替换进入程序核查队列，`checks.requires_review=true`；原文明示且尚未解决的业务待定事项才属于 `open_questions`。语义议题归并仍由模型完成。可另行运行 `field verify` 验证字段；默认提取未自动开启，完整跨需求记忆库仍未实现。

grounded 为最终知识快照策略，两次正常模型调用，使用 `--mode grounded` 显式开启。顶层 `extract --dataset` 和 `requirement extract --dataset` 均读取完整需求目录及额外 Markdown；清单使用 `requirement extract --manifest`，读取明确列出的节点对及 `supplements`。结果记录 `input_contract`，绑定范围、节点、材料类型和原始来源编号；它不输出事件历史。`requirement inspect --dataset ... --mode grounded` 可离线核对该策略实际输入。完整用法与边界见 [需求输入说明](docs/requirement-memory.md#grounded-最终快照输入)。

单样本成功后原子写入 JSON，并生成 Markdown。批量运行在 `summary.json` 逐条记录成功、失败和复用情况，单条失败后继续处理其余样本，任一失败时退出码为 2。每个样本的 `metrics` 保留调用数、HTTP 尝试数、耗时和 token 用量。网关未返回用量时保留 `null`。

```bash
python3 -m decision_memory batch --dataset data/public-samples.zip \
  --config config.local.toml --mode verified --output runs/verified --resume
python3 -m decision_memory validate --dataset data/public-samples.zip \
  --result runs/verified/REQ-20261012-auction-heat-design.json
```

resume 只复用原始文件哈希、输入指纹、配置、模式、提示词版本及完整性校验都匹配的结果，缺失结果会重跑。BOM 和换行字节参与原始文件哈希。已有 Markdown 会与 JSON 的确定性渲染结果核对，缺失时补建，被改动时保留该文件并报错。变化后的输入、配置或交接文本请使用新输出目录。复用项带 `status=reused`，其 `metrics` 是原始运行记录，本次新增消耗只统计新运行项。输出目录有运行锁，强制终止留下 `.run.lock` 时，确认原进程已停止后清理该锁再继续。

## 人工评测

S003 的旧 `recall=0.8` 只计 items 的参考匹配，不能解释为漏提 20%；原 open_questions 已保留“2 秒理由未说明”。现增加可选 `review init --schema-version 2 --reference-policy ...` 与 `review apply`：分别评价决策和未知信息，并显式标注参考的 full／partial／absent／未审。旧表与协调器旧评分不自动迁移。详见 [评测口径](docs/evaluation-protocol.md)。

根据官方标注为各阶段整理独立的参考条目 JSON 数组。每条使用唯一 `id` 与 `text`，示例为 `[{"id":"R1","text":"需要覆盖的有效决策与边界"}]`。跨阶段标注应核对当前输入是否含有证据后再纳入。

```bash
python3 -m decision_memory review init \
  --result runs/verified/REQ-20261012-auction-heat-design.json \
  --references /absolute/path/heat-design-references.json \
  --output runs/heat-design-review.json
python3 -m decision_memory review score \
  --worksheet runs/heat-design-review.json
```

编辑评测表的 `judgments`，逐条填写 `correct`、`citation_support` 为 `true` 或 `false`，在 `matches` 填写其覆盖的参考 ID。保留未审条目的 `null`。`correct` 需要同时检查有效状态、理由、边界及泛化程度。引用支持度需要判断引用内容足以支撑全部字段。

`precision_reviewed` 为已审条目的准确率。`citation_support_reviewed` 为已审引用的语义支持比例。全部条目和引用审完且提供参考集后，`recall` 才返回覆盖率，同一参考 ID 仅计一次。未审条目数单独显示，正式评分字段始终留空，具体比赛指标以出题方口径为准。

## 文件

- `decision_memory/` 包含来源索引、模型适配、提取、校验、持久化、评测和 CLI。
- `tests/` 包含标准库单测、实际本机 HTTP 请求和 CLI 子进程回归。
- `docs/experiment-report.md` 记录当前测量范围及待测项目。
- `docs/ai-assistance.md` 记录 AI 协助情况。
- `docs/superpowers/specs/2026-11-09-decision-memory-design.md` 记录方案。
