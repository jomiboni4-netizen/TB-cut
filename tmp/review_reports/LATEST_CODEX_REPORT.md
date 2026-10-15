# Task

自我迭代第 1–2 轮：第 1 轮增强短时间重复表达的语义选择指令；第 2 轮修复粗起点精修被较远停顿抢走、可能跳过有效开头的问题。

# Repo changes

分支 `codex/naturalness-correction-1`：
- `scripts/registered_pipeline.py` 要求选择任务检查短时重复用词和近义表达，优先保留最完整且信息量最高的版本，保护新增证据、限定条件、因果/指代依赖及自然强调。
- `scripts/refine_plans_v2.py` 起点落在词时间范围内时保留该完整词；落在词间停顿时选紧随其后的词，不按远处停顿分数跳过前部内容。
- `tests/test_project_scheduler.py` 增加选择约束断言及两个切点对齐反例。
原工作区用户修改未纳入。

# Runtime changes

所有迭代在基线提交 `b994e42` 的临时独立副本中；未访问真实素材或字幕，未调用 MiMo，未连接 Resolve，未导出。

# Validation

定向新测试：3 项通过。完整 `test_project_scheduler`：16 项通过。差异检查 `git diff --check`：通过。曾有一次模块路径不完整的测试启动错误，修正调用后通过。项目 doctor、全套测试、preflight 未运行；真实自然度仍未验证。

# External actions

MiMo called: no。Resolve connected: no。export performed: no。GitHub 草稿 PR #4 已更新，仍未合并。

# Blockers

第 1 轮只把重复表达要求传给 Codex 选择任务，尚无真实样例证明删减质量。第 2 轮只解决起点在 ASR 时间戳上的对齐选择；音频声学边界与 ASR 错位、结尾的句意完整性还需单独验证。没有人工标注的真实语音样本，不能宣称实际听感改善。

# Git state

GitHub 分支 `codex/naturalness-correction-1`。当前提交前 HEAD 为 `69ac8372bef0e19a0a9c7a9a6c6eb2230b443e70`；PR #4 草稿，基础为 `codex/repository-baseline`。临时副本中的对应源码/测试 diff 已审阅；原工作区和用户改动未动。
