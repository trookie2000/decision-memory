# 本机 Mario 协调器

`decision-memory battle` 是可独立运行的有限时长协调程序。它调用现有的「命题7-出题人」和「命题7-评审人」，不在当前聊天里扮演员工，也不把开发任务转交全局 codex。

## 入口与授权范围

测试目标由本机 `battle.local.toml` 指定，群和两位员工均使用 KIM 数字 ID。运行时只使用用户已授权的目标；更换群或员工需要对应授权。当前会话沿用原测试群和原两位员工。

Mario MCP 通过本机 `http://127.0.0.1:38399/mcp` 连接。每个新进程实际读取工具和当前用户技能清单，选择 `kim-skill-assistant-cc/codex`。派发助手只发送原文及结构化 mention，不负责出题或评判。单 case 的默认配置为 `dispatch_context = "task"`，后续调用复用回执 `_meta.threadId`。连续批次使用 `operation`，每次派发采用独立上下文，将真实 threadId 绑定到具体操作。两种模式都不使用回复正文里可能混淆的群聊 ID。

Docs 默认使用公开的 `kcli docs-cli word +create/+fetch`，读取经过已安装的 docs-shuttle capture wrapper。不要手动传 token、恢复旧登录态或修改共享权限。先按 docs-shuttle 的官方预检规则确认依赖版本。复制 `battle.example.toml` 为 `battle.local.toml`（已被 git 忽略），填写测试群和两位员工的 KIM 数字 ID（不是 Mario 页面上的员工 ID）。`docs_cli` 指定 kcli 路径；旧适配器保留 `docs_cli=None` 的 Python 配置入口，正常新任务使用公开 CLI。

只读自检：

```bash
.venv/bin/decision-memory battle doctor --config battle.local.toml
```

可用 `--doc-id` 增加一份已知文档的实时读取检查。doctor 不发 KIM 消息、不创建远端文档；CLI 自身缓存和读取 capture 是本地产物。

## 一次启动完整循环

这里的 `battle` 只推进一个 case。需要自动创建后续 case 时，使用下方 `series` 命令。

```bash
.venv/bin/decision-memory battle start \
  --config battle.local.toml \
  --output runs/new-battle \
  --sample-id S004 --stage design \
  --brief-file docs/battle-brief.md \
  --clarification-file docs/battle-clarification.md \
  --model-config config.local.toml --mode baseline \
  --wait-seconds 3600
```

示例 brief 的标题含 S003，可为新样本另写说明；任务实际身份由 `--sample-id` 和随机 task_id 决定。`clarification-file` 是可选的、预先设计并在审题阶段才加入的合成验收条件，不是真实线上需求或隐藏答案。

程序依次创建出题文档、派发并等回传、创建审题文档、派发并等回传。审题 `needs_revision` 时把真实反馈和上一版完整材料交回出题人；每轮都使用新文档。最多 3 轮；仍未通过或出现争议时进入 `human_review`，不进入主评测。

通过后冻结输入与参考答案，独立启动 `decision-memory extract` 子进程。其输入参数只有当前阶段的 dialogue、artifact、stage 和模型配置；gold、brief 和评审意见不进入提取进程或模型提示词。原始预测保存成功并核验后，再调用真实评审人填写逐项评分，原始预测不修改。

不传 `--model-config` 时先完成出题审题，停在 `awaiting_model`。这是一种明确的部分完成状态，不会生成假预测或分数。

`wait-seconds` 限制本次循环在操作之间继续等待的时间，不中断已发出的网络操作；每个请求另有有限超时。值为 0 时只推进一步。程序是前台命令，不安装常驻服务、不修改开机项；shell 进程运行时可自动推进全部步骤，无需手工复制消息。

## 连续后台批次

在启动进程中配置 `config.local.toml` 指定的密钥环境变量后，运行：

```bash
.venv/bin/decision-memory series start \
  --config battle.local.toml \
  --model-config config.local.toml \
  --manifest examples/continuous-cases.json \
  --output runs/continuous-six
```

默认在独立后台进程运行，返回 PID、日志路径和输出目录。清单包含 S004 至 S009 共 6 个虚构场景；S003 及既有官方阶段材料保持原样。每个 case 执行出题、审题返修、独立提取和真实员工评分，结束后自动进入下一个。单个 case 最多 3 轮审题。超过轮数的 case 保留待人工状态，批次汇总单独展示，不计为通过。

新 case 启动前先检查密钥，再做一次最小模型调用、Mario 技能发现和已有结果文档读取。预检失败时不会为新 case 发群消息或创建结果文档。恢复到已有真实预测的评分步骤时，可以先取回／完成评分，不为它发起模型探针或重新提取；自动进入下一个需要新模型的 case 时再做模型预检。密钥只存在于进程环境，后台进程及其提取子进程共同继承，不写入状态、日志或命令行参数。恢复新模型任务仍需向启动进程提供密钥。

```bash
.venv/bin/decision-memory series status --output runs/continuous-six
.venv/bin/decision-memory series stop --output runs/continuous-six
.venv/bin/decision-memory series resume --output runs/continuous-six
```

`status` 显示全部清单（含尚未启动的 case）、当前阶段、已结束数量、PID 和阻塞原因。`stop` 写入停止请求，当前有限时长操作返回后保存进度退出，不中断已发出的员工任务。`resume` 读取已有回传和状态，跳过已结束 case，不重复派发。模型提取明确失败且已修复原因时，可为 `series resume` 增加 `--retry-extraction`，保留此前失败记录；已有真实预测（例如本轮 S004）禁止再次提取。

默认每次运行最多 7200 秒，可用 `series start --max-seconds` 设置 1 至 86400 秒。达到时限后保存进度，恢复时继续原批次。未知发送、未知创建、权限或模型错误会标记 `blocked` 并保留具体原因；不会靠重复发消息或重复付费调用强行继续。

批次目录中的 `series-state.json`、`manifest.json` 和模型配置摘要用于恢复核对；`cases/<编号>/` 保存完整单 case 记录；`summary.json` 与 `report.md` 为实时汇总，`background.log` 记录推进事件。清单和模型配置改变后应使用新目录。聊天会话结束不影响后台进程；关闭电脑或终止进程后需重新恢复，没有安装开机自启或远程控制服务。

## 整需求的六例对照

新清单 [requirement-cases.json](../examples/requirement-cases.json) 包含 6 个完全虚构的需求说明。它们是出题要求，尚未生成的试题和参考不能算作评测结果。每个 case 使用 `stage=requirement`，可包含 analysis、design、coding 或 testing 节点。

先确认现有两位员工能够在原群独立接单。`battle doctor` 只检查工具、技能和文档读取，不能证明员工执行正常。发送助手完成、收到消息回执或返回不透明卡片，均不能代替员工成功回传。健康检查未确认时不要启动正式批次。

```bash
.venv/bin/decision-memory series start \
  --config battle.local.toml \
  --model-config config.local.toml \
  --manifest examples/requirement-cases.json \
  --mode baseline --max-seconds 14400 \
  --output runs/requirement-six-new
```

整需求批次按两遍运行。prepare 依次完成 6 例出题、审题及必要修订；全部通过后写入 `frozen-batch.json`，再进入 evaluate。争议题保留人工确认状态并阻止整批提取。冻结记录绑定原文、参考分类、模型配置和实现文件；更换代码或配置需要新实验目录。

出题人回传 `requirement.json`、`gold.json` 和公共身份块。原始节点正文独立保存到 `inputs/<编号>/nodes/`，清单保存为 `manifest.json`。gold 将 `decision` 与 `uncertainty` 分开；证据必须准确指向指定节点的同一原文行。早期问题后来解决时，不算最终未知参考。

evaluate 对同一组原文执行两种方法。whole 使用需求级算法和给定 mode；per_node 使用旧算法逐节点 baseline，然后原样拼接，仅重编 ID 和来源编号。比较过程不删除重复、过期答案或旧未决项，不替 baseline 改答案。逐节点算法入口可读取编码、测试节点，原单阶段 CLI 枚举保持不变。

比较结果和各方法的原始预测均保留。评分任务只显示随机且持久的 A/B 标签，按 v2 分别填写条目、未决项和参考覆盖判定。每种方法都有自己的视图摘要，整体任务还绑定原始比较包。隐藏方法名只减少标签偏好，不能证明模型评审等同人工盲评。

```bash
.venv/bin/decision-memory series status --output runs/requirement-six-new
.venv/bin/decision-memory series stop --output runs/requirement-six-new
.venv/bin/decision-memory series resume --output runs/requirement-six-new
```

status 分开返回已冻结数、已结束数和完整评分数。主覆盖率只计 full，未知信息单列。整批没有完整评分时，总体质量比率为空；费用单价未配置时也为空。已评分样本的成本汇总不包含费用未知的失败调用。

已有完整预测在恢复时只校验和接回。失败或未知调用不会自动重跑付费步骤；确认原因后，显式 `--retry-extraction` 开始新尝试，原失败和部分成功结果仍保留。不要删除输出或修改旧状态来“重新开始”。

旧阶段 battle/series 继续使用原 v1 协议，旧六例无需恢复，也不将旧分数迁移成新指标。

## 恢复与异常

```bash
.venv/bin/decision-memory battle status --output runs/new-battle
.venv/bin/decision-memory battle audit --output runs/new-battle --report runs/new-battle/audit.json
.venv/bin/decision-memory battle resume --output runs/new-battle --wait-seconds 3600
```

输出目录不可重复 start。原始材料、请求、回执、解析结果、冻结文件和预测都有哈希核对；修改过的文件会拒绝复用。`state.json` 原子写入。运行使用内核 `flock`，进程退出后锁自动释放，不能删除锁文件来并发启动。

`audit` 完全离线，重放每份已完成文档的身份、完成标记、结构和引文校验，核对冻结输入／参考摘要，并在有预测时重算评分绑定。它不需要 Mario、Docs 或模型在线，也不把程序校验视为人工确认。

审计现在也核对已存的 `operator-recovery-*-confirmation.json` 和对应回执：必须有用户明确未送达确认、原操作身份／文档／taskId／threadId 一致，恢复回执带真实消息编号；跨线程需明确记录。普通回执与操作恢复回执分开计数。这只是对既有事实的只读核查，不授权自动重发，也不改协调器未知发送保护。

每次创建文档／派发消息前先持久化「已经开始尝试」状态。若超时或进程中断，可能远端已经生效，**不得自动重发**。已有回执会接回，已有结果文档继续读取；超时后 `resume` 仍可接受匹配的迟到结果，不重置本轮 deadline 来无限等待。

创建结果未知、没有可恢复回执时，使用经人工核对的真实回执接回。不要删除旧输出、猜文档 ID 或新建一个替代：

```bash
.venv/bin/decision-memory battle reconcile --output runs/new-battle \
  --operation r01-author --create-receipt /absolute/path/actual-create-receipt.json
.venv/bin/decision-memory battle reconcile --output runs/new-battle \
  --operation r01-author --dispatch-receipt /absolute/path/actual-dispatch-receipt.json
```

创建回执里的文档必须回读到本操作的 assignment 或完整结果；派发回执须含真实 threadId/messageKey。接回回执另存，保留原错误回执。没有 threadId 时不会另开线程继续发消息。

模型失败后不自动重复收费请求。修复本机凭证／配置后显式开始新尝试，保留旧错误：

```bash
.venv/bin/decision-memory battle resume --output runs/new-battle \
  --retry-extraction --wait-seconds 3600
```

在 `awaiting_model` 状态可增加 `--model-config /absolute/path/config.toml`。已有预测不能更换配置重新计分；比较其他模式请使用独立的输出位置。

退出码：完整 Agent 评分为 0；输入错误、接口阻塞、超时、争议或未完成审查为 2；本次时间窗口结束但任务仍可继续等待为 3。0 也不代表人工确认或正式比赛成绩。status 作为只读查询成功时返回 0。

## 回传协议

### 大任务使用 Docs 输入与短通知

完整任务超过本地 `notification_max_bytes` 保护限制（默认 4000 UTF-8 字节，预留结构化 mention 空间）时，程序将完整任务要求、对话、产物、参考和原始预测保存到独立的 Docs 任务输入文档。此值是项目自行选择的保守策略，不是 KIM 平台上限；不要根据回执中的估计推断平台规则。

输入文档明确标为“待执行任务输入（不是员工评分结果）”，`task.json` 原样保留 request 和 payload，结果回传文档另建／沿用，不能用输入文档冒充员工结果。KIM 仅发简短通知、真实结构化 @、任务身份、输入链接、结果链接及任务摘要。

发送前使用 Docs 官方读取包装器回读并逐字段核对任务 JSON。`delivery-binding.json` 绑定 input_version、prediction_digest、Docs 输入及结果 ID、完整任务摘要、通知摘要、完成标记。员工 `result.json` 必须返回同一 task_input_digest；原有任务身份及完成标记仍校验。只采纳员工 judgments，不改写原始预测或迁移 v1/v2 评分。

新增文件位于操作目录下（恢复时位于 `delivery-recovery-01/`）：task-packet.json、task-input.md、task-input-create-receipt.json、task-input-readback.md、notification.md、delivery-binding.json。创建任务输入文档前先写入尝试状态；创建结果未知时进入 input_create_unknown，绝不自动重建。

### 明确体积拒绝的恢复

仅当当前未完成操作的真实回执明确报告 `82000 / MESSAGE_TOO_LARGE_TO_SEND` 且无 messageKey，用户明确要求修复并恢复时，才使用：

```bash
.venv/bin/decision-memory battle recover-delivery \
  --output runs/continuous-six-20261112/cases/S004 \
  --operation score-a01 --reason '用户已授权恢复明确的体积拒绝'
.venv/bin/decision-memory series resume --output runs/continuous-six-20261112
```

第一条命令只核对迟到结果并准备恢复，不发送消息。它要求结果文档仍是该任务的等待占位；若已有合法完成结果就收取，若内容处于其他状态则停止。旧状态、请求、失败回执和原文件哈希均保留，新恢复在独立目录保存，沿用原操作 threadId、任务身份、预测摘要和结果文档。

普通 resume 执行一次新的短通知并保存实际回执。已开始发送、网络超时、无消息编号但无明确拒绝、只有感叹号等情形不能使用该恢复，不会因重复执行命令再次发送。再次收到拒绝或未知结果时停止，不能循环重试。

结果文档包含 `## result.json` 的 JSON 代码块，严格匹配 task_id、sample_id、role、round、input_version。出题结果再包含 dialogue.md、artifact.md、gold.json；审题给出 `passed / needs_revision / human_review` 及非空 feedback；评分额外匹配预测 digest，并只返回 judgments.json。

最后一行的完成标记绑定完整任务身份和随机 nonce。正文里提到标记、标记后还有内容、错误轮次或身份，都不会视为完成。Docs 会转义代码块外的下划线／竖线，程序仅对最后的协议行做对应反转义，不改写源材料和引文。

引文存在性由程序核对，语义支持度由评审给出。当前 Mario KIM 技能不支持群历史读取，因此派发助手回执与员工 Docs 回传分开记录；不能据 `taskStatus=completed` 宣称员工已经完成，也不能将派发助手文字视为员工答案。员工明确收到在测试群展示自己的回复及文档链接的要求；若没有额外 UI／历史接口核对，应如实标记群内展示未独立回读。

## 文件和评分边界

```text
state.json / summary.json / report.md / events.jsonl
operations/r01-author/placeholder.md, create-receipt.json, request.md,
                     dispatch-receipt.json, response.md, result.json, payload.json
operations/r01-reviewer/…
operations/r02-author/…
inputs/S004/dialogue.md, artifact.md
frozen.json
scoring/references.json, worksheet.json, review.json, score.json
extraction/attempt-01/cli-receipt.json, prediction.json, prediction.md
operations/score-a01/…
```

`response.md` 保留从 Docs 实时读回的正文；读取 capture 路径也记在操作状态中。capture 本身由官方 wrapper 管理，可能到期清理，不能依赖其路径长期存在。业务正文已单独按用户要求持久保存。`events.jsonl` 为诊断日志；可恢复事实以带完整性检查的 state、请求和回执为准。

评分只接受与原始预测逐条对应的 judgments：correct、citation_support、matches、notes。未审保持 null。新评测表同时固定预测和参考答案的摘要；参考答案被改动就拒绝计分。自动评审使用 `review_kind=agent`，结果明确保留 `human_review=pending` 和 `official_score=null`。

这一既有 battle 协议仍按 v1 处理，只计 items。需要分开检查 open_questions 与复合参考完整度时，使用独立的 v2 review 文件，见 [评测口径](evaluation-protocol.md)；不要重跑已完成任务或覆盖原评分来迁移。

官方参考可在不调用模型的情况下先核对：

```bash
.venv/bin/decision-memory review check-references \
  --dataset data/public-samples.zip --sample-id REQ-20261012-auction-heat-analysis \
  --references /absolute/path/references.json
```

该命令要求每条参考有唯一 id、text 和逐字 evidence（source 为 dialogue/artifact），只证明当前阶段确有这些引文，不证明语义正确。官方跨阶段 K/H 标注需要先按当前两份材料投影、去重，不能把下一阶段的实现要求作为当前阶段必须命中项。
