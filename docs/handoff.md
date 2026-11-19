# Decision Memory 交接说明

## 当前进度

2026-11-19。题面正式输入为单阶段。客观入围基准为识别质量 70%、准确率 85%、来源抽查 8/10。最新两份虚构单阶段的默认策略本地判卷为 20/20 严格覆盖、18/18 准确和 10/10 来源。两份三节点需求的原文优先策略为 19/20、27/27 和 10/10。正式成绩及评委 MyToken 环境待确认。 [提交要求与验收状态](submission-readiness.md)

工作分支是 `codex/decision-memory`，基线 commit 为 `802bd3921f3d`。最新代码、测试和文档在当前工作树，处于待提交状态。换机器时随代码一起交接工作树中的新文件，以及需要复现的输入、答卷和回执。Git 会忽略 `runs/` 和 `config.local.toml`，换机器后用获准的模型配置。[本轮方案与变更记录](solution.md)

## 当前实现

默认 `grounded` 正常调用模型两次。第一步从原始对话和产物提取条目，第二步读原文复核。每条知识包含结论、理由、适用边界和各字段的引用。单阶段只读取指定阶段，完整需求按清单顺序读取节点。评测参考由独立判卷读取，提取端只接收题目材料。[评委运行说明](judge-run.md)

完整需求可在 `[model]` 中设置 `grounded_strategy = "evidence-first"`。该策略先按主题登记规则、理由、边界和改写原文，再合成答卷。两份三节点需求的输入 token 少 34.3%。单阶段使用默认策略。最新全量回归 536 项通过，已核对八份对照答卷、16 条请求回执和本机重放。 [配对验收](experiment-report.md#原文优先提取配对验收)

复核缺少数值的自身引文时，如果同条目的另一已核查字段写明并引用了该数值，CLI 可用一次定点请求选择来源。程序只追加缺证字段的引用，再校验整份答卷。缺少可用来源时保存草稿。`audit/field-review.json` 将告知与确认、截止参照、排他范围等疑点定位到原句。审计线索供语义复核使用。缩短版复核属于实验候选。[补引实现](../decision_memory/literal_repair.py) · [字段审计实现](../decision_memory/focused_review.py)

## 上轮质量诊断

同题对照固定原文、初稿、模型配置和判卷参考。完整需求的缩短版复核曾因理由漏引“30 分钟”而校验失败。复用该答卷做一次真实补引，用时 4.664 秒，共 1,190 token。下面的完整需求候选分数来自补引后的盲评。[同题对照与回执](experiment-report.md#同一初稿的复核与定点补引)

| 本地盲评 | 严格覆盖 | 判为有效的条目 | 来源抽查 |
| --- | ---: | ---: | ---: |
| 单阶段，原复核 → 试验复核 | 7/10 → 8/10 | 9/12 → 10/12 | 8/10 → 8/10 |
| 完整需求，原复核 → 试验复核及补引 | 2/10 → 3/10 | 9/12 → 12/12 | 5/10 → 6/10 |

主要失分有两类。完整需求漏掉了部分跨节点规则。初稿还曾把“已告知馆长”写成“馆长已确认”，把“预定开门前”写成“开演前”，旧复核沿用了这些表述。程序回放已复现两个输入范围的答卷和报告，最近一次全量测试通过 532 项。真实补引证明缺证答卷可以恢复，最新默认版的语义质量仍待新样本验收。[实验报告](experiment-report.md#同一初稿的复核与定点补引)

公开四阶段已有两组消融。一组比较只看最终产物与读取对话和产物，另一组比较启用和关闭来源定位。实验报告保留各阶段的判分、用量、耗时和典型错例。[公开四阶段消融](experiment-report.md#2026-11-18-公开四阶段消融)

## 本机复现

在仓库根目录使用 Python 3.11 或更新版本。已有运行目录可直接离线验证，`validate` 使用同一份输入清单核对原文位置和答卷版本。

```bash
python3 -m unittest discover -s tests

python3 -m decision_memory validate \
  --unit requirement \
  --manifest runs/final-time-cue-20261119/inputs/requirement/manifest.json \
  --result runs/focused-review-pair-20261119/local-final-replay/requirement/samples/001/result.json
```

真实运行前，参考配置模板准备 `config.local.toml`。配置只填凭证对应的环境变量名，运行进程从环境取值。以下命令使用仓库自带的虚构需求，最多发起 3 条请求。每次使用一个尚未创建的输出目录。[通用配置模板](../config.example.toml) · [MyToken 配置模板](../config.mytoken.example.toml)

```bash
python3 -m decision_memory run \
  --manifest examples/requirement-cache/manifest.json \
  --scope requirement \
  --config config.local.toml \
  --max-http-attempts 3 \
  --output runs/handoff-smoke-001
```

先看输出目录内的 `report.html`，其中有整体状态和用量。打开 `samples/001/result.html` 可沿引用跳到原文。`samples/001/calls/` 保存请求和回执，`samples/001/audit/` 保存提取、复核和修复记录。结果和回执含原始材料，交接时使用获准的存储位置。[完整输出清单](judge-run.md#输出)

## 接手顺序

1. 在评委实际环境用默认策略跑一份单阶段材料。核对可用模型、MyToken 积分和异常回执。完整需求的补充运行使用原文优先策略。 [评委运行说明](judge-run.md)
2. 用统一样本、固定评分参考和来源抽查计算正式分数。阶段与需求分别报告，旧 Mario、离线复算和新模型判卷分别保留。 [实验报告](experiment-report.md#原文优先提取配对验收)
3. 提交方案、可运行代码、消融报告、复现说明及 AI 协助说明。企业文档按约定验收发布，GitHub 推送由用户执行。 [提交材料](submission-readiness.md) · [AI 协助说明](ai-assistance.md)
