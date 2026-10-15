# Task

完成阶段一代码盘点后，按用户补充的质量目标推进阶段二：评测切点吞字/截断和短时重复表达提炼，并把计划与合成评测基线写入 GitHub PR #3。

# Repo changes

GitHub 草稿 PR #3 新增 `docs/NATURALNESS_EVALUATION.md`，定义合成评测标签、两类问题轴、记录格式和当前代码能力边界。更新 `docs/SELF_ITERATION_PLAN.md`，明确阶段二目标及下一步。未改动生产代码。原工作区的 `tmp/review_reports/LATEST_CODEX_REPORT.md` 已有用户本地改动，未覆盖；本次只更新 PR 分支上的对应公开报告。

# Runtime changes

只读检查 `scripts/refine_plans_v2.py`、`scripts/project_scheduler.py` 和重复审查逻辑。确认精修已有局部词级 ASR + 20ms RMS dBFS 边界风险调整；相似度检查主要用于文本/方案间重复，尚无证据表明可发现连续口播中的近义重复并提炼。未读取或处理真实媒体、原始字幕或产品资料。

# Validation

文档内容与现有实现静态对照：完成。项目 doctor、测试、preflight、Git diff/敏感信息扫描：本轮未运行（仅文档评测规范，本轮未提交本地工作树）。上一阶段临时副本结果：基线候选 275 项中 273 项通过、2 项为工作簿工具依赖版本不匹配；本地未提交改动叠加副本 284 项通过。此结果不证明自然度质量。阶段二合成用例的规则执行验证尚未做。

# External actions

MiMo called: no。Resolve connected: no。export performed: no。更新 GitHub 草稿 PR #3，未合并。

# Blockers

没有固定自然度真实样片及人工标签，无法判断真实音频的漏检、误报或剪辑自然度。RMS 只能作风险信号；词级 ASR 也可能与可听边界错位。需要先在隔离副本中验证合成边界用例，再判断真实样本是否必要。

# Git state

GitHub 分支 `codex/self-iteration-plan-clean`，PR #3 草稿；最新计划更新提交 `afc6fcffe02db7b78bd2bbd60d491814200a3325`。PR 路径包含计划、合成评测文档和脱敏报告。本地分支 `codex/repository-baseline`，HEAD `b994e42d3bc0983aea440cafa8ac711a421ddc68`；用户已有工作区改动保持原状，本轮没有本地提交。
