# Task
TB-cut 既定 3–5 轮代码修正中的第 1 轮：项目身份、缓存隔离与断点基础。参考提交 bad5a547da36；只使用合成输入。结束后停止，不启动第 2 轮或真实项目实测。

# Repo changes
本轮解决的问题：
- init_project 保存本地项目配置；同一 workspace 跨窗口继承整项目路径，缺失标题可单项补充。项目 UUID、确认的稳定 Resolve ID、目录身份和配置摘要共同校验，不凭显示名称识别项目。旧状态拒绝自动迁移。
- 新合同 project-resume-v2/schema 2 分离项目索引、批次路径和商品阶段。真正的标题/字幕生成器跨批次复用相同有效上游；批次审核不删除 batch_id 校验。商品阶段按商品编号、encoding ID、阶段/版本、模型、规则和父依赖保存不可变结果。
- 七种检查点状态绑定当前方案与逐级审核/写入/导出证据。新方案或范围撤销旧通过；预计算 snapshot、共享 workspace 锁、revision 比较、原子 JSON 和失败标记防止覆盖及半完成。重复恢复不会重复计数。
- 真实 update_batch_state 接入恢复；裸完成标志阻塞。真实 refine/Resolve loader 和辅助入口在新登记模式的外部动作前阻塞未完成迁移。
- 复核并复用既有 runtime_meta 输入/编号校验、index_titles 占位编号实现、相应合成测试和 workbook_runtime 默认路径选择。没有纳入其余尚未复核的剪辑/连接/缓存实现。
- 同步远端仅增补的计划/规则基线，保留工作区原有内容；CHANGELOG 的既有顶部结构与基线不同，提交只收录远端基线及本轮脱敏条目，原有运行记录仍留在工作区。
- 更新 PIPELINE_DEPENDENCIES、CODEX_CORRECTION_PLAN、PROJECT_RESUME_UPGRADE 和本固定报告。剪辑时长、人物比例、语义闭合、去重硬规则未修改。

公开提交文件：.gitignore；AGENTS.md；PROJECT_RULES.md；CHANGELOG.md；docs/CODEX_CORRECTION_PLAN.md；docs/PIPELINE_DEPENDENCIES.md；docs/PROJECT_RESUME_UPGRADE.json；scripts/{project_registry,product_checkpoints,runtime_paths,runtime_meta,runtime_publication,init_project,update_batch_state,index_subtitles,index_titles,workbook_runtime,fingerprint_inputs,compare_timeline_versions,inspect_timeline_for_export}.py；tests/{test_project_resume,test_index_titles,test_range_overrides,test_workbook_dependencies}.py；本固定报告。共 25 个文件。

# Runtime changes
只在临时合成工作区登记、生成索引、发布阶段和恢复检查点，并导出独立公开源码快照验收。没有修改真实素材、SRT、标题工作簿、PROJECT_STATE、原有运行缓存或时间线。未清理缓存。现有未提交改动均保留；真实运行文件不会提交。

# Validation
- PASS：独立公开源码快照的可用测试 152 项，其中本轮新合成集 15 项。覆盖双项目同编号商品、跨窗口不重填路径、跨批次上游命中与旧审拒用、新方案/范围使旧 PASS 失效、进程并发锁、不可变覆盖冲突、revision 冲突、登记/发布/账本失败恢复、重复计数、旧新版缓存共存、模型/输入/规则/依赖变化、真实消费者外部动作前阻塞。
- 全量公开快照：实际运行 153 项，152 通过，1 项 ERROR；分类为 BLOCKED 的是真实 Node 工作簿解析集成，缺失私有 artifact-tool 分发。命令返回非零，未宣称全量 PASS。独立可用测试运行明确排除该已记录 blocker，不把它改成 PASS。
- 工作区兼容回归：实际运行 175 项，174 通过，1 项 ERROR/BLOCKED；工作簿包版本不匹配。包含原有尚未提交测试，因此交付依据以独立公开快照为准。
- 修正合成范围 fixture 的显式商品清单；未放松范围身份门禁。依赖负例 fixture 在 node_modules 不存在时处理已有失效符号链接，避免 FileExistsError 掩盖原错误。
- Project doctor：BLOCKED。当前工作区 artifact_tool_version_mismatch；公开快照缺失私有包。结构/命令检查已运行；没有修锁、替换 parser 或安装私有依赖。
- Preflight：合成 workspace/preflight 兼容测试通过；新登记的完整 downstream preflight 尚未迁移，保持阻塞。真实项目 preflight 未运行。
- Git diff checks：PASS，工作区与独立暂存快照均通过；公开文件与脱敏报告扫描无真实路径、商品数据、凭据或运行产物。
- 未运行：真实 ASR/人物检测、视频剪辑、MiMo、Resolve 连接/写入/回读、导出、真实项目续剪。无性能提升结论。

# External actions
MiMo called: no
Resolve connected: no
export performed: no
只读核对 GitHub PR #1 和参考提交，取回缺失计划文档并 fetch 工作分支；PR 保持开放未合并，已关联当前聊天。仅创建本地提交；本轮无推送授权，不推送，不更新 PR 正文，不改 main。

# Blockers
尚未解决的问题：
- 缓存冲突并未全链路解决。新登记模式只接入初始化、两种索引和检查点，未迁移的下游阻塞；schema 1 模式保留原有严格检查，不具备新项目登记/续剪能力。
- 待接入消费者完整清单在 PIPELINE_DEPENDENCIES：范围确认、候选/选段/修复、ASR/切点/画面、最终批准、Resolve 写入与导出、preflight/迁移/attest/清理及总调度。既有未提交 publish_confirmed_ranges 的 stale replacement 等改动未纳入或认证。
- 旧状态/缓存不能自动提升为新合同；中断标记需要后续明确恢复入口，不允许删标记绕过。多文件发布不是跨文件事务或断电持久性保证。
- 上游输入/全局规则变化仍保守生成整套新索引；未实现逐源文件增量索引。视频签名仍为路径/大小/mtime，不识别保持这些字段的刻意字节替换。模型标识绑定不验证实际模型执行；检查点绑定不替代真实硬审核。
- 稳定 Resolve ID 本轮仅离线确认；实际 API 身份回读、标记幂等性、跨组重叠/语义去重、全项目完成门禁尚未实测。
- 私有工作簿依赖阻塞真实解析集成与 doctor；不得称完整流程 READY。

下一轮建议：在总共 3–5 轮预算内接入全项目小批次自动处理、最终物理方案的独立 Codex 语义终审和全部质量门禁；贯通本轮阶段与检查点，保留中段吃字案例，并验证跨组完整覆盖。本轮结束后停止，真实项目实测仍需独立指令。

# Git state
本快照位于后续提交之前，不使用自引用提交号。
Branch: codex/repository-baseline
启动 HEAD: aaf928564aec
fetch 后参考/远端 HEAD: bad5a547da36
拟创建一个以 bad5a547da36 为父提交的独立第 1 轮提交；aaf928564aec 是其祖先，分支只向前推进，不重写历史。使用独立暂存快照选择上述 25 个公开文件，主工作区未提交实现和真实运行数据保持原位。
PR: #1，OPEN，未合并；远端仍是 bad5a547da36，本轮不推送。
主 index 在本快照中无暂存改动；独立 index 含本轮审阅成果。保留的未提交部分涉及原有 AGENTS/规则/CHANGELOG/项目说明/模板/依赖锁，以及候选生成、范围发布、视觉、切点、连接、导出等脚本和本地 archive；它们不因本轮测试而被认证或自动提交。
