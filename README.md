# Decision Memory

面向命题 07 的本地 CLI。读取一个研发阶段的完整对话和最终产物，输出最终有效决策、理由、适用边界及逐字段原文证据。

Python 3.11+。运行时仅使用标准库。默认输出 JSON 和 Markdown，可供下一阶段 Agent 读取。

## 当前验证范围

已实现三个提取模式、精确证据校验、有限重试、批处理恢复、人工评测表及本地 HTTP 集成测试。官方 ZIP 中的四组阶段输入已完成读取检查。

截至 2026-11-11，33 项自动测试通过。真实样本调用已到达万擎网关，但网关因项目未配置服务树节点而返回 HTTP 402，尚未生成提取结果。语义准确率和费用仍待测量。离线 HTTP 测试使用固定响应验证程序行为，实际提取质量需要另行核查。

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

本机已将题面附件保存到 `data/public-samples.zip`，该目录被 Git 忽略。其他机器可从命题文档下载同名自测 ZIP，或传入解压后的目录。程序识别每个需求下 `上下文/需求确认记录.md` 与 `产物/需求分析.md`，以及 `上下文/技术设计确认记录.md` 与 `产物/技术方案.md`。每对文件形成一条输入。 [题面与附件]
## 配置模型网关

复制 `config.example.toml` 为 `config.local.toml`，填写已获准使用的完整 Chat Completions URL 与模型名。环境变量名写入配置，密钥保存在当前进程环境中。

```bash
cp config.example.toml config.local.toml
# 在本地编辑 config.local.toml。
# 将密钥设置到配置中 api_key_env 指定的环境变量。
```

接口需要接受 `model`、`messages`、`stream=false` 和 `max_tokens`，返回 `choices[0].message.content`，且正常完成时 `finish_reason` 为 `stop`。过滤、拒绝、工具调用和缺少完成状态均会返回明确错误。支持 `Authorization: Bearer` 或 `headers_env` 指定的自定义请求头。自定义头的示例如下，填写的是环境变量名。

```toml
[model]
endpoint = "https://your-approved-gateway.example/chat/completions"
model = "your-model-name"
api_key_env = ""
headers_env = { "x-token" = "MYTOKEN_APP_TOKEN", "x-username" = "MYTOKEN_USERNAME" }
```

Mytoken 相关接入文档展示了自定义请求头的 Chat Completions 接口。本项目需要使用自身获准的入口和凭证，具体可用模型以该入口为准。当前万擎调用需要先补齐项目的服务目录和成本归属部门，再验证完整提取流程。 [网关接入示例]

HTTP 错误会显示网关返回的错误码、说明和请求 ID，并屏蔽配置中的凭证。HTTP 402 的具体原因以网关说明为准。修正配置后，可使用 `batch --resume` 重试失败样本。

`json_mode` 默认关闭，确认网关支持后可开启。`temperature` 默认不发送。费用单价可配置为每百万输入和输出 token 的价格，未配置时 `estimated_cost` 为 `null`。估算按照标准输入/输出价格计算，缓存优惠、平台积分和失败调用计费以网关账单为准。

## 提取和比较

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
