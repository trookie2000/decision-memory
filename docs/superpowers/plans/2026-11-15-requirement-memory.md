# Requirement Memory Implementation Plan

> **For agentic workers:** Use superpowers:executing-plans in this session. Each task follows test-driven development. The user approved the preceding direction and asked to continue; no new approval pause is needed for this implementation.

**Goal:** 处理完整需求的有序节点，输出当前知识与变更依据，并拒绝矛盾或不可溯源的模型评分。

**Architecture:** 保留阶段提取器。新增需求输入读取、显式事件重放和需求提取流程；复用字段引文校验、HTTP 客户端与文件保存。评分端只读原始预测及实际来源。

**Tech Stack:** Python 3.11，标准库，unittest，现有 ModelClient。

**Spec:** `docs/superpowers/specs/2026-11-15-requirement-memory-design.md`

## Global Constraints

- Python 3.11，标准库依赖；保持现有阶段接口与历史结果可读。
- 不生成缺失时间戳；按节点顺序判断先后。
- 不覆盖已有输出，不把标注交给提取器，不改变既有实验记录。
- 清单最多 64 个节点；单份材料最多 2 MB；整个需求最多 8 MB。
- 不操作用户键鼠；内部样本、凭证和运行记录不进入公开仓库。

## Review Focus

- 旧产物中的决定在后续节点被推翻，不能因为来源是产物就永远有效。
- 两个范围相近的规则必须能同时存在，不能只按词语相似度删除。
- 节点内可引用前面的材料，不能引用未来材料；清单顺序变化要使旧结果失配。
- 未决提议不能覆盖现行规则，失效历史不能重新混入当前知识。
- 模型评分中的支持列表、匹配列表和引用必须互相对应，不能凭标签直接计分。

---

### Task 1: 加固评分判断与计分口径

**Files:** Modify `decision_memory/official_eval.py`, `tests/test_optimizations.py`; create `tests/test_official_eval_integrity.py`.

**Interfaces:** 保留 `validate_judgment(payload, item_ids, positive_ids)` 与 `metrics(judgments, sample_size=10, seed=7)`；后续任务使用新的严格校验。`recall` 与 `recall_strict` 表示完整覆盖，新增 `recall_weighted` 仅作辅助。

- [x] 写失败测试：不存在的支持条目、无效支持、重复 ID、单向 matches、空说明均报错；9 full 加 1 partial 的主识别率为 0.9，辅助值为 0.95。
- [x] 运行 `python -m unittest discover -s tests -p 'test_official_eval_integrity.py' -v`，确认真实缺口使测试失败。
- [x] 实现双向评分一致性校验，更新报告中的主指标和辅助指标说明。
- [x] 运行新增测试和完整套件，预期全部通过。先保留代码于本轮修改，提交统一经过 Weaver 核查。

### Task 2: 读取有序需求与稳定来源

**Files:** Create `decision_memory/requirements.py`, `tests/test_requirement_sources.py`; modify `decision_memory/sources.py`.

**Interfaces:** `load_requirement_manifest(path: Path) -> RequirementSample`；`discover_requirements(path: Path, requirement_id: str | None = None) -> list[RequirementSample]`。`RequirementSample` 兼容 Sample 的指纹、catalog、file_hashes 和 prompt_text，并提供节点元数据及 `node_order(source_id)`。

- [x] 写失败测试：同一行号在不同节点保持唯一；编码节点可读取；只读指定材料；拒绝路径与符号链接越界、重复节点和未知字段；节点重排改变指纹。
- [x] 运行 `python -m unittest discover -s tests -p 'test_requirement_sources.py' -v`，预期因缺少需求读取器失败。
- [x] 抽出通用行索引，保持原阶段指纹不变；实现清单读取、节点合并和官方数据集按需求筛选。
- [x] 运行来源测试及完整套件，预期全部通过。

### Task 3: 重放知识变更与需求提取

**Files:** Create `decision_memory/requirement_memory.py`, `decision_memory/requirement_pipeline.py`, `tests/test_requirement_memory.py`, `tests/test_requirement_pipeline.py`.

**Interfaces:** `compile_memory(payload: dict, sample: RequirementSample) -> dict` 返回规范事件、当前 items、未决项、索引、历史及关系；`extract_requirement(sample, client, mode='verified') -> dict` 返回可保存结果；`validate_requirement_saved(result, sample, client=None, mode=None)` 离线重放验证结果。

- [x] 写失败测试：新增后更新只输出新版；旧产物可失效；合并只增加证据；不同 key 的规则共存；未决项独立；拒绝错前驱、未来引文和合并改正文；重新计算摘要也不能掩盖重放不一致。
- [x] 运行 `python -m unittest discover -s tests -p 'test_requirement_*.py' -v`，确认新增行为尚未实现。
- [x] 实现四种操作的确定性重放；新增需求专用提示词。verified 做一次提取、一次原文复核，格式修复使用既有有限重试。
- [x] 用排队模型回复验证重放、模式引用隔离和真实调用计数，不把测试回复冒充模型效果。运行完整套件，预期全部通过。

### Task 4: CLI、评分来源核查与可复现示例

**Files:** Modify `decision_memory/cli.py`, `decision_memory/storage.py`, `decision_memory/official_eval.py`, `README.md`；create `tests/test_requirement_cli.py`, `examples/requirement-cache/*`, `docs/requirement-memory.md`。

**Interfaces:** `requirement inspect/extract/validate` 接受清单或数据集选定需求；复用 `save_result` 和 `validate_saved`，需求结果声明 `scope='requirement'`、`requirement_id`。official score 在发送评分请求前验证原始预测，并附实际来源。

- [x] 写失败测试：本地 HTTP 服务下，需求 CLI 输出 JSON/Markdown，并可离线验证；篡改预测、旧产物被推翻和 artifact-only 越界引用被拒；阶段调用保持兼容。
- [x] 写失败测试：评分器在模型调用前拒绝伪造引文、混合粒度和不完整阶段；评分提示词含真实原文；需求 ID 不被字符串截断。
- [x] 运行对应测试，预期失败。
- [x] 实现命令与保存、评分校验，补虚构示例和两种输入的运行说明。真实模型调用仅在有可用授权凭证时另建目录执行。
- [x] 运行完整套件、CLI 示例和 `git diff --check`。完成独立整体验证，修复重要问题；提交前核对 Weaver 回执，保留实际限制。

## 执行记录

本轮起点为 `01294d7`，完整套件 140 项通过。当前工作区干净，沿用 `codex/decision-memory` 分支，以维持本机会话与修改分支的归属一致。用户已要求继续实施，上述文档落实此前确认的方向。

实现后完成独立代码复核，补上整批评分粒度一致性、读取过程中的总量上限、异常评分 ID 校验和评分前参考完整性检查。每个缺口均先复现再修复。最终完整回归 179 项通过。真实模型效果、正式成绩和 Weaver 后端送达状态另行记录，不以测试通过代替。
