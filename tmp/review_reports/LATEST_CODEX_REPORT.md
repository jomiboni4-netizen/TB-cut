# Task

推送本机已审核的核心源码、规则、文档、合成测试和脱敏报告，并在推送后检查远端分叉与冲突。

# Repo changes

- 更新 Agent 启动约定和项目规则：长期项目记录绑定 Resolve 身份与素材/时间线；批次工作区仅保存引用、范围、进度和派生产物。
- 新增 `docs/PROJECT_STATE_LIFECYCLE.md`，记录目标目录结构、7 天保留期限、完成态判断及迁移要求。
- Doctor 检测到批次级项目注册时明确警告：项目级状态迁移尚未实现。
- 新增 `config/project_editing_modes.json` 和解析器；将模式身份及策略绑定到选择任务、物理计划、时长门禁和特写审核。
- 都督模式使用人工粗范围、单条 55–<60 秒、特写≤40%；淘宝模式保留 45–<60 秒、目标三版、特写≤30%。Resolve 只读检查对当前实时项目名分流；双命中/无命中阻断。
- 更新 Agent 约定、项目概览和变更日志；明确项目状态架构迁移仍待完成。

# Runtime changes

- 未连接 Resolve，未读取或修改时间线；未分析媒体，也未调用 MiMo。
- 未移动或删除任何状态、缓存、素材或批次数据。

# Validation

- Tests：未运行。
- Python 语法检查：通过。
- Project doctor：通过结构检查；并正确提示当前工作区仍使用旧的批次级项目注册。
- Resolve preflight：未运行，未连接达芬奇；模式匹配逻辑已由配置与语法检查覆盖，真实项目名路由待下一次只读连接验证。
- Git diff checks：通过。

# External actions

- MiMo called: no。
- Resolve connected: no。
- export performed: no。

# Blockers

- 项目级状态注册和过期批次清理的运行时迁移尚未完成：多个入口仍直接读写批次工作区状态。旧数据未自动迁移或删除。

# Git state

- Branch: `codex/sentence-boundaries-sync`。
- HEAD: `ff8b225`（本地提交前快照；提交后不再改写本报告以避免自引用）。
- PR: 未创建或关联。
- 推送前远端同步检查：本地与同名远端分支均为 0 ahead / 0 behind。
- Staged: 已审核的核心源码、规则、文档、测试和本报告；排除本地归档资料及批次专用临时脚本。尚未提交。
