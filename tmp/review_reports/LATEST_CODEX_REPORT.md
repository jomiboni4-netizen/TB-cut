# Task
保存用户最新的修正优先级与 3–5 轮上限，并整理自适应人物检测算法及中段切点/因果语义/性能验证的验收标准。本次为规格与规则保存，不计入 Codex 代码修正轮次。

# Repo changes
新增 docs/CODEX_CORRECTION_PLAN.md；PROJECT_RULES.md 追加持续约束；CHANGELOG.md 记录规则更改；更新固定审查报告。代码、配置、测试和真实运行状态均不修改。

# Runtime changes
无；未读取本机媒体、状态文件、ASR 或 Resolve 项目。此规则更新影响 runtime-rules-v2 指纹，现有派生缓存须在后续独立授权下按依赖链重建，不自动认证。

# Validation
Project doctor: 未运行（无本机完整依赖与工作区）。
Tests: 未运行（本次仅文档/规则提交）；0/3–5 代码修正轮次。
Preflight: 阅读已提交源代码后核对现有人物检测固定 0.5 秒抽样与成片终审缺口；未进行本地 runtime 预检。
Git diff checks: 计划仅四份公开 Markdown，检查敏感内容及写入范围；推送后以最终 commit 复查。

# External actions
MiMo called: no
Resolve connected: no
export performed: no
仅通过已授权 GitHub 仓库分支保存公开规划；不触发任何实际剪辑/实测，也不合并 PR。

# Blockers
自适应抽样与最终 Codex 语义终审尚未编码、编译或验证；本机未提交工作树不可从 GitHub 读取。项目工作簿依赖门禁历史存在版本不一致，未复验。不能声明性能提升或真实视频审核通过。

# Git state
提交前快照：branch codex/repository-baseline；HEAD aaf928564aecf24098f92170b65a1bfcdd6b3bf2；PR #1 开放。计划提交文件：PROJECT_RULES.md、CHANGELOG.md、docs/CODEX_CORRECTION_PLAN.md、tmp/review_reports/LATEST_CODEX_REPORT.md；不读取或覆盖本机未提交改动。
