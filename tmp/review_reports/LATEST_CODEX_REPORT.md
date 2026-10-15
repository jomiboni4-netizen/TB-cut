# Task

自我迭代第一轮：增强候选语义选择对短时间重复用词/重复表达的处理要求，并建立回归断言。工作以基线提交 `b994e42` 的隔离副本进行。

# Repo changes

更新 `scripts/registered_pipeline.py` 的 selection task 规则：要求检查短时重复表达与近义改述，保留最完整且信息量最高的表达，只有剩余片段独立完整时才删冗余；保留新增证据、限定条件、指代/因果和自然强调。更新 `tests/test_project_scheduler.py`，断言规则被传入选择任务。原工作区用户修改未纳入。

# Runtime changes

在隔离副本中核对合成字幕时间戳和人工构造的纯音/静音波形。发现精修起点可能被较远停顿吸引，但尚未改变切点代码。真实媒体、字幕、MiMo、Resolve 和导出均未使用。

# Validation

新增定向测试 1 项通过；`git diff --check` 通过。首次测试因测试模块路径错误未启动，修正调用后通过。项目 doctor、完整测试、preflight、秘密扫描本轮未运行；提交前需复核 diff/暂存清单并扫描。

# External actions

MiMo called: no。Resolve connected: no。export performed: no。已在 GitHub 建立独立开发分支并写入本轮源码和测试，待创建草稿 PR；未合并。

# Blockers

选择指令无法单独保证模型每次准确提炼；没有人工标注的真实口播集，不能报告自然度提升。下一轮优先为切点起点增加合成反例，明确有效词句不因远处停顿被跳过，再单变量修复。

# Git state

GitHub 开发分支 `codex/naturalness-correction-1`，代码和测试提交后、报告更新前 HEAD 为 `d429e1a7e9637b9440d9115e4ec023fdf603b308`。临时本地副本分支同名，基线 `b994e42d3bc0983aea440cafa8ac711a421ddc68`，源码/测试尚未提交；原项目分支及用户改动保持不变。
