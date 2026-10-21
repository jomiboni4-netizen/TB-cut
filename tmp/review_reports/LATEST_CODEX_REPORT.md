# Task

推送本机核心源码、规则、文档、测试和脱敏报告；检查 GitHub 默认分支合并冲突，并修正文档中发现的模式上限冲突。

# Repo changes

- 更新 Agent 启动约定和项目规则：长期项目记录绑定 Resolve 身份与素材/时间线；批次工作区仅保存引用、范围、进度和派生产物。
- 新增 `docs/PROJECT_STATE_LIFECYCLE.md`，记录目标目录结构、7 天保留期限、完成态判断及迁移要求。
- Doctor 检测到批次级项目注册时明确警告：项目级状态迁移尚未实现。
- 新增 `config/project_editing_modes.json` 和解析器；将模式身份及策略绑定到选择任务、物理计划、时长门禁和特写审核。
- 都督模式使用人工粗范围、单条 55–<60 秒、特写≤40%；淘宝模式保留 45–<60 秒、目标三版、特写≤30%。Resolve 只读检查对当前实时项目名分流；双命中/无命中阻断。
- 推送后发现依赖总览仍将特写上限固定写为 30%；已改为按模式说明 40%/30%。
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
- GitHub 默认分支检查：当前分支领先 32 个提交且默认分支无独有提交；合并树预检通过，无文件冲突。扫描发现并修正一处文档策略冲突。

# External actions

- MiMo called: no。
- Resolve connected: no。
- export performed: no。

# Blockers

- 项目级状态注册和过期批次清理的运行时迁移尚未完成：多个入口仍直接读写批次工作区状态。旧数据未自动迁移或删除。

# Git state

- Branch: `codex/sentence-boundaries-sync`。
- HEAD: `acda27c`（本轮第一次推送完成后的快照；当前文档修正及本报告更新待第二次提交）。
- PR: 未创建或关联。
- 已推送：`acda27c` 至 `origin/codex/sentence-boundaries-sync`。
- Staged: 本次发现的依赖总览修正和本报告更新；排除本地归档资料及批次专用临时脚本。待第二次提交。
