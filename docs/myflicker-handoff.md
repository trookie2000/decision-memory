# MyFlicker 接手说明

## 2026-11-13 最新结果：原六例已完成

用户本轮明确让 Codex 接续剩余工作，已替代下文历史记录中“仅由 MyFlicker 执行”的安排。S004～S009 当前 completed，6/6 均为 agent_review_complete。S008 经第二轮返修完成；S009 由原出题员工修正冲突稿后完成。两例各只提取一次，旧四例的文件、状态和评分保持原样。

六例共 28 条决策、8 条未决项；28 条决策均获真实员工肯定初评，人工确认仍待完成。原批次进程已经退出，不要再恢复、重提取或重新评分这六例。最新证据为 `runs/continuous-six-20261112/evaluation-summary.md` 和 `runs/takeover-20261113/final-verification.json`。下一步按 [提交前验证](submission-readiness.md) 补官方样本、人工复核和模式对照。

以下保留 2026-11-12 的历史交接，涉及 4/6、blocked、旧 PID 和执行方的陈述不代表当前状态。

交接日期：2026-11-12。此文件供本机 MyFlicker 的编码代理读取，接续实现和评测工作。

## 最新状态与接续任务（优先阅读）

### 本轮 MyFlicker 实际执行结果

请以 `docs/myflicker-next-run.md` 顶部和 `runs/continuous-six-20261112/evaluation-summary.md` 为准。凭证问题已解决，原 S004～S009 批次当前 4/6 完成：S004 保留原结果，S005、S006、S007 新完成，S006 经两轮修订。19 条 items 均获原员工肯定初评，仍待人工确认。

S008 审题及后续状态查询均无 Docs 回传；S009 在原目录独立推进后收到了两套冲突出题稿，校验拒绝，修正请求的 Mario 回执只有感叹号串、没有 messageKey，不能自动重发。两例均未提取。PID 31658 和 67259 均已退出。已有六例实际状态、失败回执、晚到结果核对和恢复边界全部落盘；不把未完成项伪装为完成，不再以缺少凭证为由停在 S005。

### 最新交接：执行方改由 MyFlicker

用户明确要求 MyFlicker 执行，当前 Codex 停止继续开发、启动和派发。请优先读取 `docs/myflicker-next-run.md`。批次当前 blocked，PID 70965 已退出；S004 已完成提取，评分派发明确报消息过大（82000），不是仍在正常等待。下方“批次正在运行”的内容属于此前状态，不能作为当前事实。MyFlicker 应先修复短通知加任务文档的派发方式，再保留已有数据恢复同一批次。

### 用户最新澄清：连续生成 case 并评测

用户已选择首批 6 个新 case。连续调度器已经实现，92 项测试通过，并完成一次独立代码复核及问题修复。真实批次已启动，目录为 `runs/continuous-six-20261112`，后台 PID 为 `70965`。模型、Mario 和 Docs 预检通过，正在处理 S004，之后自动依次处理至 S009。

现在不要再启动第二个批次，不要重跑 S003，也不要修改正在使用的配置或清单。使用 `.venv/bin/decision-memory series status --output runs/continuous-six-20261112` 查询实时状态；这条命令不需要模型密钥。停止和恢复命令见 `docs/coordinator.md`。后台进程已继承本次密钥环境，聊天结束不影响它继续运行。以下关于“尚未实现／数量待确认”的段落保留为本次变更前的背景，以本段实时运行记录为准。

用户预期启动一次后持续生成 case、审题返修、调用 CLI 提取并评分，完成一个后自动进入下一个。此前交接只要求继续单次验证，当前代码也只有单个 case 的 battle 状态机，没有创建下一个 case 的外层调度。单次检查完成后结束聊天没有满足这个预期。

连续评测设计见 `docs/superpowers/specs/2026-11-12-continuous-evaluation-design.md`。用户已确认首批 6 个，原先未实现的外层调度现已通过 `series start/resume/status/stop` 提供。

已实现的后台批次复用已有 Battle 和固定的两位员工、测试群。启动前检查凭证与连通性，整批及其提取子进程继承启动时的密钥环境。每个 case 独立保存状态、输入、参考、预测和员工回传。提供批次状态、停止和断点恢复入口，成功后自动创建下一个 case。保留未知发送不自动重发的约束，遇到阻塞时给出明确原因和恢复入口。

聊天会话可以结束，但已启动的后台批次应继续推进。验收时给出真实 PID、日志、状态文件，以及一个新 case 完成后自动开始下一个的证据。不要只修改文档或运行离线测试，就宣称持续评测已经启动。S003 与已准备的官方参考保持原样，连续批次使用新目录。仍禁止操作前台键盘鼠标或开启远程控制。

### 晚间 MyFlicker 接续结果（此前记录）

用户明确要求后台执行，不开启远程控制、不操作键盘鼠标。本轮遵守该边界，没有新增 Mario 派发、没有重跑 S003 出题或提取，也没有读取其他会话／日志寻找密钥。

S003 仍为 `agent_review_complete`。只读审计核对 53 份受保护产物，另核对 state／summary／report，共 56 份原有文件哈希未变。通用 `battle audit` 已补上对 `operator-recovery-*-confirmation.json` 及回执的只读核查，能正确报告 4 份普通回执、1 份用户确认后的恢复回执，共 5 个不同 messageKey。未知派发仍不自动重发。

评测口径已整理为 [docs/evaluation-protocol.md](evaluation-protocol.md)。新增 opt-in `review init --schema-version 2 --reference-policy ...` 和 `review apply`，分别评价 items 与 open_questions，并要求每条参考显式填写 full／partial／absent／null；不能仅凭 matches 算整条覆盖。默认 v1 与现有 battle 评分保持兼容，不自动迁移已完成运行。

S003 的旧 score.json 和 review.json 完全不变。旁路 v2 转录了已存真实员工的明确判定：决策条目 3/3、未知信息 1/1；原决策类参考检查点 4/4、未知检查点 1/1。该分类为事后口径整理，不是新提取结果、不是模型提升或正式 benchmark。s003-004 必须由 K002＋K003 联合支持；s003-005 是“2 秒理由未说明”，不应放进决策分母。详见：

- `runs/evaluation-audit-20261112-evening/report.md`、`review.v2.json`、`score.v2.json`。
- `runs/evaluation-audit-20261112-evening/verification.json`：原始文件未动及零新模型／员工调用的核查。
- `runs/evaluation-audit-20261112-evening/echo-differences.json`：员工回显 prediction 的 4 处差异，未采用回显替换预测。

仍需人工抽查 S003 语义：K001 的结论包含“每天全量”和“只处理实际修改”，但结论引用 D00027/A00005 不直接支持这两个限定；“其他后面再说”被解释为“其他缓存”也有歧义。保持原员工判定可追溯，不以新口径宣称人工通过。

官方阶段选择 `REQ-20261012-auction-heat-analysis`。已在 `runs/official-heat-analysis-20261112-evening/` 冻结 references.v2.json、reference-policy.v2.json、manifest.json 和源行 catalog：8 个决策检查点＋3 个阶段未决检查点，引文校验通过；单阶段读取只触碰对应两份输入，没有读取其他阶段或标注正文。阶段参考由本地投影产生，仍待人工确认。

本轮真实调用前仅检查当前环境变量布尔值，`WQ_API_KEY_present=false`。随后官方 baseline CLI 实跑退出码 2，报“缺少环境变量 WQ_API_KEY，请在本地配置模型凭证”，HTTP 请求数为 0、没有生成 prediction.json。失败回执为 `runs/official-heat-analysis-20261112-evening/extraction-receipt.json`。不要再调用 MyFlicker 非交互模型或询问开启远程控制，也不要到旧日志／会话中找密钥。需要用户将凭证安全注入启动官方提取的进程后才能继续。

该轮原计划接下来只运行官方阶段：先核对 manifest 的输入／参考／配置摘要未变，使用该目录尚不存在的 prediction.json 作为输出；预测成功后创建独立 v2 评测表并按原有两位员工协作边界核查。`prepare_official.py` 已执行，会拒绝重复准备，不要删除失败记录绕过保护。S003 不需要 resume 或 `--retry-extraction`。当前工作顺序以上方“用户最新澄清”为准。

该轮完整自动测试 78 项通过，`git diff --check` 通过。未提交、未推送。下面保留更早的接续状态和首次交接背景，当前任务以上方“用户最新澄清”为准。

### Kimi 恢复完成时的交接背景

截至本次恢复结束，通用 battle 协调器已经实现，S003 已完成出题、审题、返修、再审、独立提取和真实员工评分。状态为 `agent_review_complete`。下面保留首次交接背景，涉及“未实现 battle”“尚无提取结果”的内容均是历史状态，不要据此重新开发或重复运行 S003。

S003 第 7 次 baseline 使用本地 `config.local.toml` 中的新部门 Kimi 接入点，HTTP 调用成功。原始预测有 3 条决策和 1 条未说明事项，输入 2725 tokens，输出 1238 tokens，耗时 36.098 秒。密钥仅用于当次后台进程，没有写入项目文件。新一轮模型调用前检查当前进程的 `WQ_API_KEY` 是否非空，只报告布尔值；不要搜索其他会话或日志中的密钥。

首次 scorer 派发只返回感叹号串，没有消息编号。用户确认群中未收到后，Codex 用新的派发会话补发了同一任务，取得真实 messageKey 和员工 Docs 回传。原助手线程为 `949762667311796280`，操作恢复的派发线程为 `949831253946548265`。恢复记录位于 `operations/score-a07/operator-recovery-01-*`，原失败回执保留。该恢复通过一次性脚本执行，通用协调器仍保持未知发送不自动重发。

当前证据均在 `/Users/tianxupu/Pugna/decision-memory` 下：

- `runs/mario-battle-20261112-S003/summary.json` 和 `report.md`：最终运行状态。
- `runs/mario-battle-20261112-S003/extraction/attempt-07/prediction.json` 和 `prediction.md`：CLI 原始输出。
- `runs/mario-battle-20261112-S003/scoring/review.json` 和 `score.json`：员工 judgments 与程序计分。
- `runs/mario-battle-20261112-S003/operations/score-a07/response.md`：真实评分员工回传。
- `runs/wanqing-kimi-20261112/final-verification.json`：53 份文件、5 项员工回传和恢复回执的最终核验。
- `docs/experiment-report.md`：最新实测结果及限制。

程序统计 3/3 条正确、3/3 条引用支持，参考匹配 4/5。第 5 条参考对应的“2 秒理由未说明”已在 open_questions 中，评审正文也判其正确，但现有程序不计入这一类匹配。不要把 80% 直接说成模型漏提 20%。评审额外回显的 prediction 副本存在改动；本地原始预测未改，程序只采用 judgments，仍需人工抽查语义和评分口径。

接续时先核对上述证据，整理参考条目粒度和 open_questions 的评测口径，再完成一个官方阶段的真实提取与逐条核查。不要重新出题或重跑 S003，不要为已完成任务加 `--retry-extraction`。后续再按既有方案扩大到官方四阶段及三个模式对比，保留 Agent 初评与人工确认的区别。

MyFlicker 项目为 `2118968`，原会话为 `753893e84e614888ae35138b533065ad`。通过 `flick session meta` 确认上一轮运行已 completed。该会话不会因外部进程更新配置而自动再启动，需要用户在会话中发起下一轮任务。远程控制状态仍为 false；使用当前会话的本地命令、Kimi 接口和 Mario MCP 不需要开启远程控制。用户要求所有操作在后台完成，禁止操作其键盘鼠标。

## 目标与角色

你是 decision-memory 项目的开发者兼调度者。用户希望由 MyFlicker 接替当前 Codex 会话，读取本地代码、执行命令、修改程序并验证结果。

最终目标是完成[命题 07](https://docs.corp.kuaishou.com/d/home/fcABTQPLkKC9sABNQdKh0_0YG)：根据一个研发阶段的原始对话和最终产物，输出最终生效的决定、理由、适用边界及原文依据。

用户已确定的协作方式：必须调用 Mario 中已创建的“命题7-出题人”和“命题7-评审人”，在指定 KIM 群展示各自回复，由本机程序自动派发和收取结果，再接上 decision-memory 的真实提取与评测。

请直接执行必要的开发和验证，不要只给计划、展示没有执行的命令，或在一个模型会话里模拟两位员工。开发和调度在 MyFlicker 当前环境中完成，无需把任务再次交给全局 codex 命令。

## 先读取这些材料

项目目录：`/Users/tianxupu/Pugna/decision-memory`。

1. 全局和仓库说明文件（如存在），然后读 `README.md`。
2. 最新[方案设计与验证文档](https://docs.corp.kuaishou.com/d/home/fcAAzKpiPx5sJ2P6GJHC5g_uH)。当前是六节正文、两张插图，不要重写成评分细则。其本地正文在 `runs/evaluation-plan-20261112/illustrated.md`。
3. `docs/mario-kim-multiround.md`：已完成的实测与调用方式。
4. `runs/mario-kim-probe-20261112/complete_round2.py`：自动交接的临时脚本，作为协议和流程参考。
5. `decision_memory/cli.py`、`client.py`、`pipeline.py`、`sources.py`、`validation.py`、`evaluation.py`，以及相关测试。

交接时分支是 `codex/decision-memory`，提交为 `dada0a7`。`docs/mario-kim-multiround.md` 尚未跟踪，本交接文件也是新文件。先看当前 git 状态，保留现有修改。本轮无需提交或推送。

## 首次交接时的状态（历史记录）

- CLI 已有 inspect、extract、batch、validate，以及 review init／score。
- 提取模式有 artifact-only、baseline、verified。verified 包含决策变化识别、条目生成和全文复核。
- 原文索引、逐字段引文存在性校验、批量恢复和人工评测表已有实现。上次完整测试记录是 33 项通过；修改后需重新运行相关测试。
- 官方样本位于 `data/public-samples.zip`，包含两个需求、四个阶段输入。
- 两位真实 Mario 员工已在测试群完成一次两轮往返：出题、评审、修订、再评审。
- 第二轮最后一段已经自动执行：读取出题文档 → 发消息给真实评审人 → 等待评审文档 → 保存本地材料。
- 这些材料通过了 CLI 的输入格式检查。尚未用它们完成 CLI 模型提取和正式语义评分。
- 当前没有可从首轮运行到结束的通用 battle 命令，也没有后台常驻协调器。

不要直接重跑 `complete_round2.py`。它绑定旧任务、旧结果文档和旧输出目录，并主动拒绝重复派发。不要删除旧结果来绕过保护，应基于它实现新的通用流程。

## 已验证的 Mario 与 KIM 入口

| 配置 | 实测值 |
| --- | --- |
| 测试群 | `7419479552888854` |
| 命题7-出题人 KIM 机器人 ID | `10120718088572` |
| 命题7-评审人 KIM 机器人 ID | `10664440173316` |
| Mario MCP 地址 | `http://127.0.0.1:38399/mcp` |
| ks-cli | `/Users/tianxupu/.local/bin/ks-cli` |

交接前又做了一次只读检查：MCP 可连接，tools/list 返回 `mario_list_skills` 和 `mario_run_skill_task`。这不代表 MyFlicker 新会话已经获得相同能力，需要在新会话中实际验证。

MCP 使用 HTTP JSON-RPC。先 initialize，再 notifications/initialized；支持 JSON 或 SSE 响应。若返回 Mcp-Session-Id，后续请求带上。已有临时脚本的 Mario 类包含可参考的实现，导入模块不会执行 main；不要调用其 main。

调用步骤：

1. 读取 tools/list，调用 `mario_list_skills`。
2. 选择当前用户的 `kim-skill-assistant-cc/codex` 技能，使用返回的 skillId。
3. 调用 `mario_run_skill_task`，arguments 包含 skillId、instruction，以及续接任务时的 threadId。
4. instruction 明确要求向指定测试群发送原文，并真实结构化 @ 指定员工。派发助手只发消息，出题或判分交给目标员工。
5. 保存 messageKey、任务回执和返回的 threadId。后续属于同一协调任务的技能调用复用该 threadId。

历史技能 ID 是 `F78-f1AS46CzQ02AuOo1Q`，仅作查证，通用实现要重新发现。历史协调器线程是 `949696846958960653`，新任务不要直接续写这个旧线程。

已成功触发员工的结构化 @ 形式是 `<@=userId(10120718088572)=>` 或 `<@=userId(10664440173316)=>`。单纯拼接普通 @ 文本不能作为触发成功的依据。实测入口在 KIM 显示为“田旭浦通过 Mario 发送”。

该 MCP 运行技能时使用的 self-agent 是派发助手，不是出题人或评审人。不能把 self-agent 的回答当成两位员工的回答，也不能假定工具支持任意 agentId 参数。

## 结果怎么收回来

当前 KIM 技能不支持读取群历史。两位群内员工也没有可用的 handoff_external_agent。本方案通过指定 Docs 文档收取完整结果，无需重新研究机器人互相 @ 或打开员工 Handoff。

每轮由协调器创建一个新的结果文档，将 URL 与任务编号、样本编号、轮次、角色、输入版本一起发给员工。员工在 KIM 输出自己的回复，并将完整结果写入该文档。最后写入只属于本轮的完成标记。

程序读取文档，验证任务身份和完成标记，再保存结果并决定下一步。审题通过、需要修订和待人工确认要有明确状态。最多三轮，超过轮数停止。消息发送成功仅代表已派发，不能当作员工完成。

实时读取文档优先使用：

```bash
/Users/tianxupu/.local/bin/ks-cli doc word fetch \
  --doc-id <本轮文档ID> --format raw --json
```

返回的 content 是正文。实测中 search markdown 的搜索索引在写入后仍可能返回空内容；新实现不要只靠搜索索引判断员工是否完成。

创建结果文档可参考 ks-cli doc word create，写入由员工现有 Docs 技能完成。先查看命令帮助。保持用户已有权限，不修改文档共享范围。

若 CLI 在新环境中报告认证失败，先检查 MyFlicker／KIM 是否运行及该会话的凭证支持，返回实际错误。上次 Codex 环境的部分企业文档命令借用了 IDE 终端；这不是流程必须依赖 IDEA，MyFlicker 应先在自己的本机工具环境验证。

## 必须区分的两种模型调用

MyFlicker 当前聊天模型用于你编写代码和执行调度任务。它不会自动成为 `decision_memory/client.py` 使用的 HTTP 提取接口。

当前 ModelClient 使用 `config.local.toml` 中的网关配置。历史真实请求曾返回 HTTP 402，网关说明项目未配置服务树节点。这个问题在本轮 Mario 协作测试中没有解决，需以新的单样本实跑核验状态。

用户希望后续使用 MyFlicker 的能力。若要让 CLI 提取也复用其模型，需要先核对 MyFlicker 官方支持的非交互调用或接口方式，再实现适配，不能仅凭界面能选模型就认定 CLI 已能调用。

交接时当前 shell PATH 中有 ks-cli、codex，没有 myflicker、mfcli 或 claude 全局命令。MyFlicker 界面内的 Claude Code 模式与全局命令是否存在是两回事，不要直接假定可以运行 claude -p。凭证留在本地，不在聊天或报告中打印密钥。

## 首次交接时的执行顺序（已完成部分勿重做）

### 1. 先验证新会话的实际能力

在项目目录真实执行以下命令，并报告退出码和简短结果：

```bash
pwd
.venv/bin/decision-memory --help
.venv/bin/decision-memory inspect --dataset data/public-samples.zip
```

然后只读检查 Mario MCP 的工具清单和技能清单，使用 ks-cli 读取方案文档。不需要为这些检查发测试消息。

### 2. 实现完整协调器

将已验证的派发和文档回传实现为可独立运行的程序，结合现有 CLI 决定命令形式。配置使用现有两位员工及指定测试群。

流程：创建出题结果文档 → 派发出题 → 等待结果 → 创建评审文档 → 派发审题 → 等待评审。通过则固定样本；需修订则将真实意见交回出题人；最多三轮；争议保留为待人工确认。

补齐唯一任务编号、完成标记、持久化状态、有限超时、原始结果保存和恢复。发送超时后不得直接重发，先等待原结果或核对回执。每轮使用新文档和新输出位置，保留已有测试记录。

用有意义的测试覆盖状态推进、错误与超时、重复派发防护、恢复和答案隔离，再做一次真实双员工往返。用户已明确授权在这个测试群调用这两位员工；不要向其他群或个人发送消息。

### 3. 接上 CLI 提取和评分

先用一个官方阶段检查真实提取入口。如果接口仍不可用，报告具体阻塞，同时继续完成不依赖该入口的协调器和评测适配。

样本审定后，只将 dialogue.md 和 artifact.md 交给 CLI。gold.json 和评审意见留在评分侧。保存 CLI 原始预测，再让评审人填写正确性、引用支持度、匹配的参考 ID 和理由，不允许先修改预测再计分。

现有 review init／score 可复用。references 是带唯一 id 的 JSON 数组；judgments 有 correct、citation_support、matches、notes。未审条目保持 null。官方标注要按阶段整理，合并重复事实，不把跨阶段不可见结论作为必须命中项。

### 4. 报告实际验证范围

先交付一个官方样本的真实提取与人工核查，以及一个合成样本从出题到评测报告的自动运行，再扩大到官方四阶段和首批六个合成阶段，对比三个已有模式。

区分“派发成功”“员工完成”“程序校验通过”“Agent 初评通过”“人工确认”。没有真实模型结果时，不报告准确率或覆盖率。人工复核和真实数据缺失都如实说明。

## 可直接查证的文件与历史结果

- `runs/mario-kim-probe-20261112/summary.json`：review_status=passed、local_input_check=passed，model_extraction_run=false。
- `runs/mario-kim-probe-20261112/complete-round2.log`：自动段的执行记录。
- `runs/mario-kim-probe-20261112/reviewer-r2-dispatch-receipt.json`：发送回执。
- `runs/mario-kim-probe-20261112/S002/dialogue.md` 与 artifact.md：员工生成的两份输入。
- `runs/mario-kim-probe-20261112/reviewer-v2-doc.md`：员工评审原文。其“第6轮用户澄清”定位有误，实际是第 7 条消息、文件第 15 行。
- [历史出题结果](https://docs.corp.kuaishou.com/d/home/fcAAA0-ASnuhWOVmsqcSgoesh)与[历史评审结果](https://docs.corp.kuaishou.com/d/home/fcAAktjcbItAmCehUZrOezxZM)：仅供查证，新任务不覆盖。

data、runs、.venv 和 config.local.toml 被 Git 忽略，当前同一台电脑上有这些文件，不会自动出现在新的 GitHub 克隆中。

写代码优先使用 apply_patch 或明确静态路径。按当前工具与仓库要求记录代码贡献；提交前核对实际代理的 Weaver 采集，不把 MyFlicker 新修改归入旧 Codex 事件。若后续需要改方案文档，先读最新正文与图片结构，保留已嵌入的两张图。
