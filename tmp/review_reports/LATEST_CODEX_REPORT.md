# Task
按用户指定将当前「成片」导出为仅含 ID 与序号的文件名（如 `20_1.mp4`），检查无黑帧，并将每个 ID 的视频条数回填到标题表“剪辑”字段。

# Repo changes
- 导出流程未运行；此前调用旧导出入口被注册项目身份门禁阻止。导出脚本与验证脚本仍有本轮期间的未提交改动；尝试恢复被自动审查拒绝，原因是无法证明覆盖不会丢失用户已有改动。未再尝试覆盖。
- 未修改标题表；没有真实导出计数可供回填。

# Runtime changes
- 导出目录初检为空；未新增或覆盖视频文件。Resolve 时间线未写入。

# Validation
- 绑定项目和实时 Resolve 稳定 ID 一致；项目 doctor、Resolve connection doctor 通过；源/目标时间线绑定分别为「素材」/「成片」。
- 调度汇总明确返回 `BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS`，注册导出器也因 downstream 尚未集成而拒绝运行。没有绕过门禁直接渲染。
- 当前旧验证器全片黑帧检查的代码修改尚未执行验证；本轮 `git diff --check` 通过。

# External actions
Resolve connected: yes。Resolve write: no。Export: no。Workbook updated: no。External semantic review: no。

# Blockers
- 项目尚无已迁移的注册导出/验收入口，无法安全导出或确认无黑帧，故无法准确回填各 ID 的条数。
- 自动审查拒绝覆盖恢复两个导出脚本，理由是可能丢失既有未提交修改；脚本当前改动状态需用户确认后再处理。

# Git state
Branch `codex/sentence-boundaries-sync`；HEAD `5845af7`。保留其他现有修改及未跟踪文件；未暂存、未提交、未推送；PR：无。`git diff --check`：通过。

---

# Task
按用户授权从头重剪商品20、30；ASR仅用于在商品范围内执行邻近切点，完成语义与人物画面审核，并正式写入 Resolve「成片」。

# Repo changes
- 更新 `scripts/registered_pipeline.py`：切点按字幕边缘时间选取最近本机 ASR 词边界，不以 ASR 文本匹配裁决审核；候选边界和物理证明限制在已确认商品范围。
- 更新 `scripts/sentence_boundaries.py`：句子边界契约升级，并支持商品范围内的物理边界约束。
- 更新 `PROJECT_RULES.md`、`config/local_models.json`、`scripts/adaptive_vision.py` 和 `CHANGELOG.md`：本地 Qwen 人物二次核验及 ASR 执行职责。未调用 MiMo 或外部语义服务。
- 保留此前工作区所有改动及未跟踪文件；未提交。

# Runtime changes
- 批次：`20261023_products20_30_fresh_reedit`。旧返修次数不计入本次；商品20、30本批均从 0 次开始。商品20已正式写入；当前续跑范围仅为商品30，避免重复追加商品20。
- 商品20 `1091546409845｜开间/日本进口光感尼龙斜纹夹阿拉善驼绒 PP风Bomber棉服短夹克`：`FINAL_PASS`、正式写入 `成片`；3个版本、4个物理片段，事务 `VERIFIED`，预期/实际片段数均为4。
- 商品30 `859334145857｜开间黑标/意大利进口长毛羊驼TEXCO 珠片羽毛手工织带钻石小香`：语义、人物及最终质量审核通过，`WRITTEN`；3个版本、4个物理片段，事务 `VERIFIED`，预期/实际片段数均为4。
- 两个商品各有一个蓝色单帧起点标记；标记名称已规范为“商品编码｜标题”，并通过 `MARKERS_STANDARDIZED_AND_VERIFIED` 回读。帧位分别为192088、197010。源「素材」未改，未新建时间线。
- 两次 Resolve 追加均自动保存项目备份；没有执行导出。人工复核预览：无；两件均为正式时间线交付。

# Validation
- 绑定项目稳定 ID `c0e7fcf5-1e45-4e8e-9ae9-2443d2ee4832` 与实时 Resolve 一致；源时间线「素材」ID `04102c77-da85-4ff3-9960-26c09d3084ff`，目标「成片」ID `399ed7c6-3989-4f9d-8586-2f81c5499e87`。
- 项目 doctor、写入前 Resolve connection doctor、调度器 `plan`、`run` 和最终 `diagnose` 均通过；最终商品30状态 `WRITTEN`，无待恢复回滚点。
- 商品20、30追加事务均通过写后核验；4/4片段回读。标记重命名事务核验通过，更新2个标记，只改名称，时间线片段行未变化。
- Python 模块编译检查通过；`git diff --check` 通过。未运行测试。
- 调度器汇总仍显示旧式 `BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS` 且 `written_count` 为0，但 Resolve 追加事务和写入报告均为 `VERIFIED`，最终 `diagnose` 也记录商品30 `WRITTEN`。该旧导出核验摘要不影响已完成的时间线写入；未导出文件。

# External actions
MiMo called: no。Resolve connected: yes。Resolve write: yes（仅目标时间线「成片」追加商品20、30，并规范化两枚起点标记）。export performed: no。

# Blockers
无待处理切点或商品审核阻塞。导出核验旧摘要与实际已验证的时间线写入记录不一致；本轮未执行导出。

# Git state
Branch `codex/sentence-boundaries-sync`；HEAD `5845af7`。保留原有改动及未跟踪文件；未暂存、未提交、未推送。PR：无。`git diff --check`：通过。

---

# 最新进度：商品 36–38（2026-10-22）

- 实时 Resolve 项目 `20261021-淘宝`，稳定 ID `c0e7fcf5-1e45-4e8e-9ae9-2443d2ee4832`；来源 `素材`（ID `04102c77-da85-4ff3-9960-26c09d3084ff`）只读，目标 `成片`（ID `399ed7c6-3989-4f9d-8586-2f81c5499e87`）。项目 doctor、连接 doctor 与 `taobao_scheduler.py plan` 通过，批次 `20261022_products36_38`。
- 商品 37 通过最终门禁，调度器正式写入并回读为 `WRITTEN`。商品 36、38 的语义与人物画面审核已完成；ASR 切点仍未确认，状态为 `REPAIRABLE`，回退点 `local_audio_cutpoint_review`。
- 商品 36、38 各三个版本已按 `manual_cutpoint_preview` 流程写入并回读为 `PREVIEW_WRITTEN_AND_VERIFIED`。共 6 条预览、9 个素材段，明确属于人工复核预览，不是正式交付。
- 商品 37、36、38 的起点商品标记经调度器标准化并回读；每商品一个蓝色单帧标记，名称为 `商品 ID｜标题`。未添加版本标记或绿色范围标记。源时间线未写入，未新建时间线；未导出。
- 本次没有调用 MiMo 或其他外部语义审核服务。Resolve 写入仅为商品 37 正式版本及 36、38 人工预览。
- 命令行参数顺序首次误用、以及普通语义预览门禁拒绝了未确认切点；已先运行 `diagnose`，再用调度器的 `--preview-rough-semantic` 人工切点预览模式完成。最后一次调度器诊断显示 37 `WRITTEN`、36/38 `REPAIRABLE`，预览通过。

# 最新进度：商品 34（2026-10-22）

- 实时 Resolve 项目：`20261021-淘宝`，稳定 ID `c0e7fcf5-1e45-4e8e-9ae9-2443d2ee4832`；只读 `素材`，只写 `成片`。两项 doctor 与调度器 `plan` 通过。
- 新批次：`20261022_product34_visual_repair`，仅含商品 34。已绑定标题与字幕索引，未使用根目录旧状态或外部语义审核服务。
- 旧画面采样显示 `451-480.mp4` 源片约 381.5–408.1、420.8–435.4、501.9–528.0、602.3–649.0 秒连续采样点未见人物。这是采样证据，不代表每帧保证；新候选窗避开这些时段。
- 新调度器运行得到三条方案，时长约 45.17、53.03、46.10 秒；语义、ASR 切点和本机人物采样终审均 PASS。每条人物采样估算为 100%，证据等级 `ENGINEERING_SAMPLED`，非逐帧认证；ASR 未解决边界 0 个。
- Resolve 已实际追加 3 条到 `成片`，共 8 个物理素材段；但写后复验 `FAILED_VERIFICATION`：第 6 段的源起止帧均比方案早 1 帧（预期 15965–16190，实读 15964–16189），其余 7 段一致。事务状态 `FAILED_VERIFICATION`，调度器 `diagnose` 无有效回滚点；严禁重放追加。商品 34 当前为“已写入、待切点核验”，**不是正式交付**。
- `素材` 时间线快照前后相同。商品 34 在 `成片` 起点有且仅有一个蓝色单帧标记；已通过调度器将标记名称改为 `1092603004687｜开间黑标/PP同源轻涂饰羔羊皮 摩登燕尾领撞色弧形下摆中古短皮衣` 并回读。未放绿色范围标记。
- 自动备份和事务位于绑定项目工作区的该批次 `backups/`、`resolve_delivery/`；未导出。原有工作区改动均保留。
- External actions：MiMo called: no；Resolve connected: yes；Resolve write: yes（34 三条已追加但复验失败）；export performed: no。
- Git 快照：Branch `codex/sentence-boundaries-sync`，HEAD `5845af7`；本轮公共文件仅更新 `tmp/review_reports/LATEST_CODEX_REPORT.md`，其他已有修改和未跟踪文件均保留；全部未暂存、未提交、未推送，PR：无。`git diff --check` 通过。

# 上轮报告（历史记录；34 的状态以本页最新进度为准）

# Task
本轮继续处理 33–35；核对实时项目身份、修复人物返工盲选与 ASR 门禁参数漂移，并区分人工预览与正式交付。以下保留上轮 30–32 摘要。
继续剪辑商品 30–32，并按语义标题范围通过调度器完成审核与交付。

# Repo changes
- 本轮修改 `scripts/registered_pipeline.py`、`scripts/project_scheduler.py`，新增合成测试并追加 `CHANGELOG.md`；原有其他工作区改动保留。
- 选段返工现在使用本机人物采样摘要；无证据时停止，并限制累计返工。ASR 生成与最终门禁共用逐级回退参数。
- 上轮 30–32 未修改脚本或项目持久规则；更新本报告记录运行结果。
- 语义范围清单保存在 `.cache/projects/<project-key>/batches/20261022_products30_32/delivery/SEMANTIC_RANGES.json`。
- 此工作区原有未提交变更仍为 `scripts/resolve_semantic_preview.py`、`PROJECT_RULES.md`、`CHANGELOG.md` 和本报告；均未提交。

# Runtime changes
- 本轮批次 33–35：33 的三条人工修边预览已写入并回读，仍为 `REPAIRABLE`；34 人物比例审核未过，仍为 `REPAIRABLE`；35 三条正式成片已写入并回读为 `WRITTEN`。
- 33、35 各一个蓝色单帧商品起点标记已按 `ID｜标题` 命名并回读；无版本标记。未启动下一批。
- 新建并运行批次 `20261022_products30_32`；标题和字幕索引通过身份校验。
- 商品 30：候选与语义复核通过；人物画面抽样未检测到人物，重选 worker 无可核查的人物区间，状态 `BLOCKED`。
- 商品 31：最终审核 `FINAL_PASS`。
- 商品 32：语义选段和画面审核均已运行；ASR 句尾对齐无法确认，状态 `REPAIRABLE_ASR_CUTPOINT`，回退点为 `local_audio_cutpoint_review`。
- 上轮 30–32 质量门槛未全部通过，未写入 Resolve，未导出。

# Validation
- 本轮项目 doctor、Resolve connection doctor、scheduler plan 与写后回读通过；四项合成回归检查通过，Python 编译检查与 `git diff --check` 通过。当前 Python 无 `pytest`，因此直接运行测试函数。
- 34 的真实素材复测仍未过人物比例门禁；新的人物采样返工输入与累计次数上限仅完成合成验证，未重新跑真实素材。
- 项目 doctor、Resolve connection doctor 与 scheduler plan：通过，当前 Resolve 项目和配置目标身份匹配。
- scheduler diagnose：30 `BLOCKED`（无人画面证据）；31 `FINAL_PASS`；32 `REPAIRABLE`（需人工复核 ASR 切点）。
- 上轮 `git diff --check`：当时待复核；当时未运行测试。

# External actions
- 本轮 MiMo called: no；Resolve connected: yes；Resolve write: yes（33 预览、35 正式）；export performed: no。
- 上轮 MiMo called: no。
- 上轮 Resolve connected: yes；30–32 Resolve write: no；export: no。

# Blockers
- 当前批次 34 人物画面比例超限，不能正式交付；33 需人工修边。根据项目规则，本轮不跳过该批进入下一批。
- 商品 30 全部画面抽样均判定无人，不能绕过人物画面硬门槛。
- 商品 32 的句尾切点需要人工本地音频复核；最终切点约在源素材 `391-420` 的 1596 秒附近。
- 商品 30、32 未通过前，本批整体交付门槛保持阻塞。

# Git state
- 本轮报告更新前快照：Branch `codex/sentence-boundaries-sync`，HEAD `5845af7`；上述代码、测试与此前已有改动均未提交，未推送，PR：无。
- Branch: `codex/sentence-boundaries-sync`。
- HEAD: `5845af7 Keep ASR within cutpoint review scope`。
- 上轮无代码改动；本报告及此前已有的 `scripts/resolve_semantic_preview.py`、`PROJECT_RULES.md`、`CHANGELOG.md` 变更均未提交；未推送；PR：无。
- 未跟踪用户文件 `archive/`、`product18-main.png` 未触碰。
