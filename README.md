# Decision Memory

面向命题 07 的本地 CLI。按阶段或完整需求的有序节点，输出当前有效决策、理由、适用边界、未决事项及逐字段原文证据。grounded 输出最终知识快照，原事件模式另保留更新和失效历史。

Python 3.11+。运行时仅使用标准库。评委 `run` 生成 JSON、Markdown、HTML 和调用回执，可供人工核查或下一阶段 Agent 读取。

评委可用 `python3 -m decision_memory run --dataset 样本.zip --scope stage --config 模型.toml --output 新目录`。
一次生成 JSON、Markdown、HTML 和真实调用回执；点击 HTML 来源可查看原文。完整需求改用 `--scope requirement`。
新入口的 grounded 模式正在独立验收，模型复核不代表正式质量分。配置、失败处理和报告说明见 [评委本地运行](docs/judge-run.md)。

评委入口和报告已实现，质量验收尚未通过。最近一轮完整输入自测严格覆盖 22/32，未达到 70%；新保留材料也暴露了理由和最终状态的问题。最新修复的 338 项本地测试通过，真实质量需另验，不能借用第一轮的较高成绩。
九个未启动任务已完成有限续跑，没有重发此前两次执行未知的请求。各版本、来源消融、单阶段诊断和新材料分别记录在 [grounded 自测](docs/grounded-evaluation.md) 与 [方案](docs/solution.md)。

## 历史接口与实验记录

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

本机已将题面附件保存到 `data/public-samples.zip`，该目录被 Git 忽略。其他机器可从命题文档下载同名自测 ZIP，或传入解压后的目录。程序识别每个需求下 `上下文/需求确认记录.md` 与 `产物/需求分析.md`，以及 `上下文/技术设计确认记录.md` 与 `产物/技术方案.md`。每对文件形成一条输入。题面附件请从内部材料获取。

## 配置模型网关

复制 `config.example.toml` 为 `config.local.toml`，填写已获准使用的完整 Chat Completions URL 与模型名。环境变量名写入配置，密钥保存在当前进程环境中。

```bash
cp config.example.toml config.local.toml
# 在本地编辑 config.local.toml。
# 将密钥设置到配置中 api_key_env 指定的环境变量。
```

接口需要接受 `model`、`messages`、`stream` 和 `max_tokens`。默认使用非流式响应，读取 `choices[0].message.content`。正常完成时 `finish_reason` 必须为 `stop`；过滤、拒绝、工具调用和缺少结束状态均会返回错误。

网关支持 SSE 时，可在模型配置中设置 `stream = true`。客户端收齐完整文本和结束标记后才校验、保存结果，断流按有限次数重试。`timeout` 同时限制流式读取的总时长，可按需要提高到 600 秒。`reasoning_effort` 默认为不发送，取值须由所用接口支持；例如本机 Kimi 官方样本实测使用 `"low"`。推理强度参数的模型差异可参考 [Moonshot 接口校验说明](https://github.com/MoonshotAI/Kimi-Vendor-Verifier/blob/main/tests/k3_features/test_thinking_effort.py)。

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

`json_mode` 默认关闭，确认网关支持后可开启。`temperature` 默认不发送。费用单价可配置为每百万输入和输出 token 的价格，未配置时 `estimated_cost` 为 `null`。估算按照标准输入/输出价格计算，缓存优惠、平台积分和失败调用计费以网关账单为准。

## 提取和比较

处理完整需求，先运行虚构示例的离线检查。提取、校验及数据集用法见 [完整需求提取](docs/requirement-memory.md)。

```bash
python3 -m decision_memory requirement inspect \
  --manifest examples/requirement-cache/manifest.json
```

只运行一个官方阶段，不读取其他阶段正文或评测标注：

```bash
.venv/bin/decision-memory extract --dataset data/public-samples.zip \
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

批量处理官方格式样本。

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
| baseline | 当前阶段对话与最终产物，一次生成条目 | 1 |
| verified | 识别决策变化、生成条目、全文复核 | 3 |
| artifact-only | 只向模型提供最终产物 | 1 |

每个步骤的格式或引用错误最多修复一次。每次 HTTP 请求默认最多重试两次，最多三个尝试。认证错误立即失败。默认每次请求超时 180 秒，输入上限 120,000 字符，输出上限 12,000 tokens。长材料超过上限时返回明确错误，可调整配置或按完整阶段拆分。

## 结果与恢复

每条结果包含 `conclusion`、`reason`、`boundary` 三个字段，每个字段有 `text` 和 `evidence`。证据包含原文编号、逐字引文、源文件、行号和对话消息编号。理由或边界无依据时为“未说明”。`open_questions` 单独记录未决事项。

verified 模式还输出 `decisions`，保留 `active`、`superseded`、`pending`、`conflict` 状态。`review_notes` 记录模型复核意见。`checks` 区分逐字引用校验、模型复核和人工评审。引用存在性校验与语义支持度需要分别核对。

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
