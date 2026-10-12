# Task
补充并保存用户的两项 P0 要求：整达芬奇项目文件夹一次指定、跨窗口继承；解决缓存相互覆盖/误复用，并允许一次接下全项目任务但按小组质量审核完成。继续使用此前总计 3–5 轮 Codex 修正上限。

# Repo changes
更新 PROJECT_RULES.md、docs/CODEX_CORRECTION_PLAN.md、CHANGELOG.md 与本固定审查报告；只增加公开规格、缓存隔离/恢复设计、整项目质量门禁和合成测试场景，不改变源代码、配置或剪辑流程实现。

# Runtime changes
无。没有读取或改动真实状态、缓存、素材、字幕、Resolve 时间线或导出。永久规则新增使 runtime-rules-v2 摘要变化；旧派生缓存仍按旧身份失效，后续须经授权按依赖关系处理，不能重新认证冒充当前结果。

# Validation
Project doctor: 未运行（仅远端文件审阅，未取得真实 workspace 与固定工作簿依赖）。
Tests: 未运行（无代码实现改动）；代码修正轮次仍为 0/3–5。
Preflight: 审阅远端 runtime_paths、runtime_meta、init_project、refine_plans、person_coverage_audit、resolve_write、update_batch_state 的当前身份、固定输出名称及完成集合机制；未进行真实 runtime 预检。
Git diff checks: 设计仅四份公开 Markdown；提交前检查敏感路径、凭据、原始数据及变更范围，发布后复验文件清单与分支。

# External actions
MiMo called: no
Resolve connected: no
export performed: no
仅在用户指定的 GitHub PR 分支保存需求设计，不合并 PR，也不执行实测。

# Blockers
项目级一次登记、避免跨运行缓存冲突、全项目分组质量门禁均未编码或验证。现有 `batch_id` 身份门禁可拒绝错批但不能自动实现跨窗口恢复；不能据此宣称已修好。本机未提交工作树和项目私有数据不可读取。工作簿依赖版本不一致是此前记录，本轮未复验。

# Git state
本次后续提交前的快照：branch codex/repository-baseline；HEAD 606f808fe1ab；PR #1 开放、未合并。拟提交 PROJECT_RULES.md、docs/CODEX_CORRECTION_PLAN.md、CHANGELOG.md、tmp/review_reports/LATEST_CODEX_REPORT.md。远端内容为读取基准；本机未提交文件与未跟踪文件未知，未覆盖。
