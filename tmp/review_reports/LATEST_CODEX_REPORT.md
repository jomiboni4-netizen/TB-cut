# Task
按用户授权将相关公开代码发布到 GitHub，并提供新窗口续作指令。

# Repo changes
采用独立发布分支 `codex/taobao-flow-performance`，基于远端已有分支汇集相关代码及依赖；不发布本地未推送历史中的归档素材、运行数据或私人报告。发布版本仅含本节及上一轮脱敏报告，本地历史报告保留。

# Runtime changes
本轮未运行媒体分析或 Resolve 操作。上一轮停在 `WRITTEN / FAILED_VERIFICATION`，不得重复追加。

# Validation
此前 Vision 定向 18 项、调度/ASR/交付模拟 84 项通过；本轮复核公开文件凭据、发布清单及差异格式。未重复运行真实测试、项目 doctor 或 Resolve preflight。

# External actions
MiMo called: no。Resolve connected: no。export performed: no。用户授权 GitHub 推送；仅推送公开代码、合成测试、配置、文档和脱敏报告。

# Blockers
Resolve 回读一帧偏差及状态不一致仍待新窗口处理。性能不设 300 秒硬门槛；按阶段复盘，一轮结束后报告，避免无限修复。

# Git state
当前本地分支 `codex/sentence-boundaries-sync`、HEAD `da7d81f748` 保持不动；发布分支为 `codex/taobao-flow-performance`。这是发布前快照，工作区和默认暂存区保留。未创建 PR。

---

# Task
继续优化淘宝剪辑调度流程，调整人物审核顺序、拆分人物预筛与最终审核、修复 ASR 阻塞后的人工修边预审、接通可复用测试时间线事务、修正调度状态与受控并发，并用真实随机盲选商品复盘单商品耗时。用户最后要求本轮结束后停止，不再进入修复或重跑循环。

# Repo changes
- `scripts/adaptive_vision.py` 改为 Apple 初始全检、仅 `NO_PERSON` 送 Codex、Codex 仍判无人时才生成对应分层细化帧；细化帧再次 Apple 全检且仅 `NO_PERSON` 送 Codex。删除生产路径的 Qwen 身份、调用、卸载与旧替身，升级视觉协议和证据缓存身份。
- Codex 四格审核保持每张最多四帧，独立拼图由串行改为最多四张有界并发；输入映射和结果顺序保持确定。配置、规则、变更记录和模拟测试同步更新。
- ASR 前人物检查改为三点轻量预筛，物理方案后保留唯一完整人物比例审核；视觉返修锁定未修改 variant。ASR 只生成切点，短窗和扩窗均无法定位时保留语义结果并生成红色待人工修边预审，不默认启动 medium 大窗口。
- 淘宝调度器接通已登记测试时间线：按批次名称和稳定 ID 唯一解析，备份、清理、写入和回读均有事务；中断后先诊断，禁止重复追加。计划阶段展示真实阶段名，质量通过报告 `READY_FOR_DELIVERY`，交付记录区分 `WRITTEN`、`READBACK_VERIFIED` 和 `EXPORT_VERIFIED`。
- 增加最多两个商品的内存受控准备流水线；ASR worker 与 Resolve 写入仍串行。记录阶段耗时、缓存命中、抽帧、Codex 拼图/帧数和返修次数。
- 相关变更覆盖规则、配置、调度器、人物审核、ASR、Resolve 写入/测试线事务及合成测试；工作区中原有未提交改动全部保留，未 reset、覆盖或清理。

# Runtime changes
- 真实项目身份、来源线、正式线与已登记测试线的稳定 ID 均通过 doctor 校验；真实测试始终由淘宝调度器 `plan`/`run` 驱动，没有手工串联阶段脚本。
- 已观察的成功基准：一次跨三次安全续跑的商品累计约 336 秒并完成回读；另一次单次完整调用约 542 秒并完成写入回读。另有身份锚点不足的商品在范围阶段约 16 秒安全阻塞，没有放宽门禁。
- 最后一轮随机盲选商品：`plan` 0.05 秒，`run` 379.25 秒，总计 379.30 秒。项目分析与质量阶段 337.27 秒，范围匹配 28.03 秒，Resolve 交付 9.26 秒。
- 最后一轮商品内部耗时：语义选择 98.93 秒、语义终审 27.64 秒、人物预筛 18.71 秒、ASR 121.03 秒、物理方案 1.74 秒、完整人物审核墙钟 63.06 秒、最终审核 1.20 秒。一次视觉返修导致选择、终审、预筛、ASR 和方案阶段各执行第二轮；本轮按用户要求不再继续优化或重跑。
- 完整人物审核共抽 72 帧；28 帧进入 8 张 Codex 四格，记录 1 次缓存命中。Codex 四格累计 worker 时间 133.71 秒，但并发后人物审核墙钟为 63.06 秒，确认并发实际生效。当前更大的耗时来自 ASR（20 次窗口转写，转写累计 99.98 秒）以及视觉返修后的第二次语义/ASR链路。
- 最后一轮质量门禁达到 `READY_FOR_DELIVERY`。测试线清理事务先移除上一轮 6 个视频项和 1 个标记，其他时间线保持不变。随后实际写入 7 个视频段、对应音频和 1 个商品标记。
- Resolve 交付事务最终为 `FAILED_VERIFICATION`：回读中前两段源帧出现 1 帧偏差，报告错误为新增素材段不匹配。调度诊断将商品保留为 `WRITTEN`、`resume_required=false`；没有生成正式 `READBACK_VERIFIED`，未来不得重放追加，必须从该事务诊断继续。
- 本轮结束后 ASR worker 与 Codex 子进程均已关闭；`ollama ps` 为空，Qwen 从未加载。未调用 MiMo 或其他第三方语义服务。

# Validation
- Vision 定向回归：18 项通过，覆盖 Codex 推翻初始无人不细化、确认无人只细化对应分层、不确定/服装近景/检测错误不调用 Codex、Qwen 路径不可用、四格有界并发及结果顺序。
- 调度器、ASR 回退、测试时间线事务和交付模拟回归：84 项通过。
- 模拟全流程与零重算恢复检查：通过；项目审核不再返回旧占位门禁。
- 项目 doctor：通过。Resolve connection doctor：通过。实时项目稳定 ID 与绑定工作区一致，测试线名称与稳定 ID 均匹配。
- 真实盲测前置 `plan`：通过，阶段顺序为 registration、resolve_preflight、title/subtitle index、semantic ranges、selection、semantic final review、selection person prescreen、local ASR、physical plan、final person audit、final quality gate、resolve delivery。
- 真实盲测质量审核：通过。Resolve 写入：`WRITTEN`。写后回读：失败，未标记 `READBACK_VERIFIED`。导出验证：未请求。
- `git diff --check`：通过。没有运行整仓库所有历史测试；本报告只声明上述定向与模拟集合。

# External actions
MiMo called: no。Resolve connected: yes。export performed: no。Resolve 操作仅限已登记测试线的事务化备份、清理、写入和回读；来源线与正式成片线未清理、未写入。没有向第三方发送字幕、商品信息或项目内容。

# Blockers
- 当前阻塞点是 Resolve 回读的 1 帧源边界偏差。事务已经写入，状态为 `FAILED_VERIFICATION`/`WRITTEN`；下一次工作必须先读取现有事务与报告，修正回读归一化或写入帧约定，禁止重新追加。
- 本轮 379.30 秒不是成功完成时间，因为回读未通过。当前最快已观察到的验证完成时间是约 336 秒的多次安全续跑；单次完整验证成功记录约 542 秒。要得到更低且完整成功的单次时间，后续应优先减少视觉返修后重复的 ASR/语义链路，并合并 ASR 窗口调用，而不是继续增加 Vision 并发。
- 按用户要求已停止，不执行修复、续跑、再次随机测试或导出。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891d3b244c250c64fb9542c8fe69c7a`。这是后续任何提交前的工作区快照。所有改动均未暂存、未提交、未推送；存在本任务相关的已修改及新增脚本/测试，也保留了进入任务前的其他未提交改动。未创建 PR。
