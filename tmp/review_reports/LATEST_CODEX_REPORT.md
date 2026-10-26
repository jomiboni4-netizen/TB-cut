# Task
将单个 Resolve 项目长任务的目标、验收、计划、派生进度与用户决定落实为本地项目档案和代码门禁；保持用户对旧失败事务的暂停决定。

# Repo changes
- 新增 scripts/project_task.py：唯一项目目录内建立五份 Markdown 与 PROJECT_TASKS.json；新档案拒绝覆盖。控制文件绑定稳定项目身份，任务声明验收编号、批次/商品范围、允许操作及运行/返修上限。
- STATUS.md 从已有检查点、事务、用户限制和任务运行记录生成；商品状态明确标为未重新验证，事务状态不是新验收证书。无阻塞时仍为 NOT_EVALUATED，不从命令 PASS、WRITTEN 或单批结果自动宣布整项目完成。
- 控制文件原子写入并使用项目锁；预占运行次数，失败/中断不返还预算，未关闭运行阻断新运行。支持显式选择任务、暂停/解除以及关闭中断记录，均不重放交付。
- workflow_scheduler 接入任务配置与文档指纹、运行预算、阶段边界限制、运行结果及诊断摘要。目标/验收/计划/决定变化使旧计划失效；运行期间配置变化阻断后续阶段。自动状态页刷新和运行计数不使计划失效。
- Resolve connection doctor 增加 fresh-identity，档案 CLI 重新读取实时稳定项目 ID；无连接、绑定不符或部分初始化均停止。project doctor 校验已登记档案完整性。
- Resolve 正式写入、试写、语义预审、测试线及都督交付入口接入暂停检查；恢复入口还检查 recover 限制。清理器保护项目级档案及其父目录。
- AGENTS.md、PROJECT_RULES.md、CHANGELOG.md 与 docs/PROJECT_LONG_TASKS.md 记录接手协议、职责和使用方式；新增合成集成测试。全部修改保留在原分支工作区。

# Runtime changes
- 按实时稳定项目 ID 找到唯一项目目录，建立 PROJECT.md、ACCEPTANCE.md、PLAN.md、STATUS.md、DECISIONS.md 及控制文件；未复制到仓库根目录或批次目录。生成档案均留在本机忽略目录。
- 将用户已确认的保留旧失败状态、暂不恢复决定保存为项目级 run/recover/write 限制，并追加决定。未登记新的编辑任务，运行记录为 0，未批准新的真实测试。
- 原失败追加事务仍为 FAILED_VERIFICATION；现有商品检查点未更改，未清空、追加、修剪或恢复时间线。
- 当前状态为 BLOCKED；诊断保留 resume_required=true、resume_command=null，显示项目级限制和未关闭事务。直接门禁检查 run、recover、write 均阻断。

# Validation
- 最终定向及模拟集成测试 87 项通过：项目隔离、跨批次限制继承、覆盖保护、损坏/缺失档案、文档变化使计划失效、预算、中断、锁、直接交付门禁、清理保护、交付模拟、音画回读及连接目标检查。
- 扩大测试期间发现旧交付测试路径对象不兼容及临时目录别名问题，已修正并复测通过；新增测试初次有字符串语法错误，已修正。最终以 87 项通过为准。
- 项目 doctor：通过。Resolve connection doctor fresh-identity 与指定目标校验：通过。
- 全部本轮相关 Python 文件编译及 git diff --check：通过。
- 本项目档案初始化、刷新、诊断和暂停门禁检查：通过。没有运行真实媒体分析、调度器真实 run、模型推理、Resolve 写入或导出；没有运行整仓库所有历史测试。

# External actions
MiMo called: no。Resolve connected: yes（只读身份与时间线配置校验）。export performed: no。未调用第三方服务或启动模型；本轮没有需要关闭的模型进程。未提交、推送或创建 PR。

# Blockers
旧 Resolve 回读失败仍保留，用户暂停仍生效；本轮代码任务不构成恢复或真实测试授权。完整商品范围尚未登记为新的可执行任务，不自动选商品或续跑。
本层负责管理与追踪，复用原审核验证链；不新增自动验收通过入口、不自动分派任务、不强制中止已进入的外部 API 调用。工程代码修改次数不能由运行门禁计数；预算约束的是调度器运行及单次返修上限。全范围完成仍需单独完成现有证据审核。

# Git state
分支 codex/sentence-boundaries-sync；HEAD da7d81f74891。此为后续提交前快照。新增公开文件为 scripts/project_task.py、tests/test_project_task.py、docs/PROJECT_LONG_TASKS.md；本轮还修改 AGENTS.md、PROJECT_RULES.md、CHANGELOG.md、scripts/workflow_scheduler.py、scripts/project_doctor.py、scripts/resolve_connection_doctor.py、scripts/cleanup.py、scripts/resolve_write_v2.py、scripts/resolve_trial.py、scripts/resolve_semantic_preview.py、scripts/reusable_test_delivery.py、scripts/delivery_resolve.py 及本报告。原有未提交和未跟踪改动均保留；未暂存、未提交、未推送，PR：无。真实项目文档和状态不提交。

---

# Task
继续优化淘宝流程，优先核对上轮失败追加事务；本轮最多一次针对性修复与一次真实测试，不提交或推送。

# Repo changes
- 本轮针对性修复仅改追加失败状态链：写后回读有错误时，不再调用 record_written_checkpoints 封存已验证写入证据。
- 淘宝 diagnose 优先检查追加事务；非 READBACK_VERIFIED 状态明确返回 resume_required=true、无普通续跑命令，并要求保留已有片段和备份。增加四种未验证事务状态的合成回归。
- 未改变帧容差、ASR、语义、人物路由、并发或 Qwen 禁令。原有全部工作区改动保留。

# Runtime changes
- 初次连接报告 app_not_running；用户打开 Resolve 后，按实时稳定项目 ID 认证唯一绑定项目与上轮批次，指定测试目标的稳定 ID 匹配。
- 只读确认现有 7 个视频段和对应音频。第一段原始源尾帧比预期少 1；第二段原始源首帧比预期少 1。第二段左裁切偏移与预期首帧一致，媒体总帧数减去左右裁切偏移等于时间线时长；音视频原始接口结果一致。这支持源帧 getter 与裁切偏移接口存在差异的判断，但本轮没有把推断变成通过证书。
- 旧事务保持 FAILED_VERIFICATION，旧商品检查点未改写；新版 diagnose 正确要求恢复且禁止普通重放。没有清空、修剪或追加现有测试结果。
- 自动审批拒绝源帧归一化及失败事务恢复补丁，原因是放宽失败事务校验并重写通过报告可能掩盖真实错切。被拒补丁未执行；未尝试间接绕过。旧事务未明确处理，因此未启动新真实测试或 plan/run。
- 性能代码复盘：视觉返修仍使整商品 rough、semantic_review、ASR、plan、vision、final_audit 失效；虽锁定未改 variant 的选段，整商品终审仍重跑。ASR 按端点生成短窗并合并相交窗；物理缓存可复用包含窗，但 fallback_level 属于参数身份，扩窗与初始窗之间不能直接互用。以上为代码观察，未声称取得节时结果。
- 本轮新真实阶段耗时、抽帧数、拼图数和返修次数均无新增；未执行媒体测试。不得把上轮 379.30 秒算为本轮成功基准，不设 300 秒门槛。

# Validation
- 针对性与模拟流程测试共 41 项通过，包含测试线授权、失败事务诊断、模拟全准备及零重算、音画回读同步检查。
- 更广项目调度模拟第一次缺少测试导入路径而失败；修正导入路径后发现测试替身未隔离 ASR 预热，停止两次重叠运行并关闭其子进程。隔离预热后的广泛模拟仍未在本轮完成，已停止，不记为通过。
- 项目 doctor：通过。Resolve connection doctor：通过；实时项目与指定测试目标身份：通过。
- Python 编译与 git diff --check：通过。
- 淘宝 diagnose：返回 FAILED_VERIFICATION、resume_required=true、resume_command=null，回退至事务只读核对与恢复。
- 真实测试：0 次；旧事务仍阻塞。帧偏差修复及恢复验收未完成。

# External actions
MiMo called: no。Resolve connected: yes（只读）。export performed: no。未向第三方发送项目内容；未启动 Qwen。测试意外启动的 ASR worker 已终止，隔离模拟已停止；结束检查 Ollama 无已加载模型。

# Blockers
自动审批拒绝了将失败事务恢复为通过的补丁，本轮停止在旧事务恢复处。后续需要用户批准继续该恢复工作，并先建立保留原失败报告的独立核对回执：逐段核对媒体起点、总帧数、左右裁切、时间线时长、原始源帧和音视频一致性，同时保留稳定项目/目标 ID、标记及受保护时间线校验。未证明全部一致前不得改为 READBACK_VERIFIED，也不得清空或重放。
下一步仍是旧事务的安全恢复；恢复通过后才可优化未改 variant 的复算及 ASR 窗口复用，再由淘宝调度器执行唯一一次真实测试。本窗口按要求报告后停止。

# Git state
分支 codex/sentence-boundaries-sync，HEAD da7d81f74891。此为后续提交前快照。本轮新增改动涉及 scripts/workflow_scheduler.py、scripts/resolve_write_v2.py、tests/test_workflow_scheduler.py 和本报告；其中测试文件原已处于未跟踪状态。其余既有修改与未跟踪文件全部保留；未暂存、未提交、未推送，未创建 PR。

---

# Task
按用户授权将相关公开代码发布到 GitHub，并提供新窗口续作指令。

# Repo changes
采用独立发布分支 `codex/taobao-flow-performance`，基于远端已有分支汇集相关代码及依赖；不发布本地未推送历史中的归档素材、运行数据或私人报告。发布版本仅含本节及上一轮脱敏报告，本地历史报告保留。

# Runtime changes
本轮未运行媒体分析或 Resolve 操作。上一轮停在 `WRITTEN / FAILED_VERIFICATION`，不得重复追加。

# Validation
此前 Vision 定向 18 项、调度/ASR/交付模拟 84 项通过；本轮快速回归 34 项通过，复核公开文件凭据、89 个发布文件清单及差异格式。远端分支提交校验通过。未重复运行真实测试、项目 doctor 或 Resolve preflight。

# External actions
MiMo called: no。Resolve connected: no。export performed: no。用户授权 GitHub 推送；仅推送公开代码、合成测试、配置、文档和脱敏报告。

# Blockers
Resolve 回读一帧偏差及状态不一致仍待新窗口处理。性能不设 300 秒硬门槛；按阶段复盘，一轮结束后报告，避免无限修复。

# Git state
当前本地分支 `codex/sentence-boundaries-sync`、HEAD `da7d81f748` 保持不动；发布分支 `codex/taobao-flow-performance` 已推送，远端提交 `1669d5d040` 校验一致。工作区和默认暂存区保留，原分支 8 个未推送提交未发布。发布报告为提交前快照，本地本节追加推送结果。未创建 PR。

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
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。这是后续任何提交前的工作区快照。所有改动均未暂存、未提交、未推送；存在本任务相关的已修改及新增脚本/测试，也保留了进入任务前的其他未提交改动。未创建 PR。

---

# Task
按用户要求从当前 DaVinci 项目素材时间线随机抽取 50 帧，直接进行分级视觉人物识别；跳过 Codex 语义分析，不写入任何时间线。

# Repo changes
- 本轮没有修改产品代码。
- 新增本机运行产物：项目工作区 `visual_probes/random50_20261023_170540/`，含 50 张抽样帧、四格审核图、逐帧 `results.json` 和汇总 `SUMMARY.json`；属于未提交运行数据。

# Runtime changes
- Resolve 稳定项目 ID 与绑定项目匹配；从只读「素材」时间线（稳定 ID `<稳定ID-1>`）按视频片段时长加权抽样，随机种子 `<数值已脱敏>`。23/23 个片段均匹配当前项目登记的视频路径。当前「测试」时间线为空，因此只从已登记的素材源时间线取帧。
- Apple Vision 完整检测 50 帧：48 帧标记有人，2 帧标记无人。
- 两帧升级到 Qwen 四格审核：一帧维持无人，一帧改判为有人。
- Qwen 判为无人的一帧升级到 Codex 四格纯视觉复核，改判为有人。未运行语义分析。
- 最终标签为 49 帧有人、1 帧近景有人。没有人工逐帧真值，以上为模型输出与分歧，不代表准确率。
- 阶段耗时：Vision 8.33 秒、Qwen 96.18 秒、Codex 26.94 秒。Qwen 仅在疑点出现后按需启动；审核结束后调用卸载，随后 `ollama ps` 列表为空。
- 没有清理、写入或导出任何时间线；没有调用 MiMo。

# Validation
- Resolve 只读连接检查：当前项目身份、素材时间线身份及绑定工作区匹配。
- 抽帧结果：50/50 PNG 成功落盘；逐帧识别和 2x2 四格升级结果已保存到本机测试目录。
- Qwen 卸载后状态检查通过（无运行模型）。
- `git diff --check`：通过。未运行剪辑全流程或测试套件。

# External actions
Resolve：只读连接，素材时间线读取。MiMo：未调用。Codex：仅对一张四格图进行人物存在性视觉复核；未做语义分析。时间线写入/导出：无。

# Blockers
没有人工标注的 50 帧真值，无法计算 Vision、Qwen 或 Codex 的准确率；当前结论只能比较标签分布与模型分歧。若要评估准确率，需要对这 50 帧进行人工标注。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮未修改产品代码；工作区既有未提交修改保留。报告文件已更新；未暂存、未提交、未推送。PR：无。

---

# Task
让淘宝调度器按本批用户明确指定的 Resolve 时间线执行交付，不依赖固定时间线名称；同时验证目标解析和计划绑定。

# Repo changes
- `scripts/workflow_scheduler.py` 增加 `--target-timeline`；`plan` 将所选名称纳入计划摘要，`run` 要求名称与计划一致，并将目标传给写入器。未指定时回退到项目注册目标；显式目标能力仅声明给淘宝模式，避免其他模式忽略参数。
- `scripts/resolve_write_v2.py` 按当前项目的唯一精确时间线名称解析目标并记录稳定 ID；拒绝来源时间线，保留项目身份、备份、写前快照和写后回读门禁。
- `PROJECT_RULES.md` 与 `CHANGELOG.md` 记录本批目标选择规则；调度器单测覆盖显式覆盖、默认回退和空名称拒绝。
- 本改动解决目标选择固定的问题；淘宝盲测方案到可复用测试线的清理/写入事务尚未接通，不把该能力标记为已就绪。

# Runtime changes
曾用临时示例名称验证计划绑定，随后恢复为项目登记目标的计划状态；没有运行商品批次、媒体分析或 Resolve 操作，没有清理/写入时间线，也未导出项目。

# Validation
- `tests.test_workflow_scheduler` 与 `tests.test_reusable_test_delivery`：8 项通过，覆盖显式目标、默认回退、空名称拒绝和测试线能力边界。
- Python 编译与 `git diff --check`：通过。
- `taobao_scheduler plan` 最终恢复为项目登记目标；当前仍 `batch_id=null`、`products=[]`，且 `test_timeline_reuse_ready=false`。
- 项目 doctor、Resolve preflight、真实时间线名称/稳定 ID 解析：本轮未运行。

# External actions
MiMo called: no. Resolve connected: no. export performed: no.

# Blockers
用户现在可以指定任意唯一命名的时间线，但本轮没有可运行批次，且淘宝的可复用测试线清理与写入适配器尚未完成。因此还不能进行所要求的随机盲测时间线写入。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。改动未暂存、未提交、未推送；此状态为后续提交前快照。PR：无。

---

# Task
解释调度器为何未能按盲测要求复用 DaVinci「测试」时间线，并加上交付能力声明与误路由保护。

# Repo changes
- `scripts/workflow_scheduler.py` 为每种项目模式声明实际交付适配器和支持的时间线模式；计划新增 `delivery_adapter`、`delivery_modes`、`test_timeline_reuse_ready`。
- 淘宝模式收到 `--allow-test-reuse` 时，在启动检测器/分析前 fail closed，给出 `test_timeline_delivery_not_integrated_for_project_mode`，避免误写正式「成片」线。
- 新增 `tests/test_workflow_scheduler.py` 覆盖淘宝正式追加能力、都督测试线能力及未知模式默认拒绝；`CHANGELOG.md` 记录。
- 淘宝测试线 adapter 尚未集成，因此这次改动是明确能力边界和防错保护，不伪装成已完成测试写入能力。

# Runtime changes
盲测商品 25（ID `<数值已脱敏>`）只根据已登记标题表随机抽取；未读取该商品的正确范围、切点或历史审核。未创建批次、未运行媒体分析、未清理或写入任何 Resolve 时间线。

# Validation
- 定向联合回归：47 项通过；Python 编译、`git diff --check` 通过。
- 淘宝调度器 plan 现在显示 `delivery_adapter=resolve_write_v2.append_registered_target`、`delivery_modes=[registered_target_append]`、`test_timeline_reuse_ready=false`。计划阶段仍显示无活动批次。
- Resolve 身份和测试线只读检查先前通过；本轮未写 Resolve。

# External actions
Resolve：只读连接记录仍有效，本次未发起 Resolve 写操作。MiMo：未调用。

# Blockers
淘宝产品计划目前只有正式线追加写入器；未接通可复用测试线事务。需要实现并模拟验证淘宝计划到测试线交付适配器，之后才可对商品 25 运行真实测试。不要用当前 `taobao_scheduler run` 绕过此缺口。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮修改未暂存、未提交、未推送。PR：无。

---

# Task
随机抽取一个商品做盲测，不读取正确商品范围或切点，并尝试按流程逻辑执行 DaVinci「测试」时间线测试。

# Repo changes
本次没有新增代码改动。已确认 `taobao_scheduler.py` 路由到 `project_scheduler.py` 后，最终写入仍固定调用 `resolve_write_v2.py --append-registered-target`；淘宝分支没有把 `--allow-test-reuse` 接到已登记测试时间线 writer。现有 `reusable_test_delivery.py` 属于冷启动测试入口，数据契约与淘宝调度器产品计划不同，不能直接替换调用。

# Runtime changes
- 只从已登记标题表随机抽样，选中商品 25（ID `<数值已脱敏>`）。未读取该商品或其他商品的范围、切点、历史候选及审核结果；未创建批次、未运行媒体分析。
- 实时项目与「测试」时间线仍通过只读身份核验；项目 doctor 通过。调度器计划前置条件仍没有批次。
- 因淘宝 `run` 会写入正式「成片」线，未启动该命令；没有清空或写入任一时间线。

# Validation
标题表结构通过既有解析器读取；Resolve connection doctor 与项目 doctor 通过。安全路由检查发现淘宝的 `run` 会无条件走正式目标追加 writer；盲测测试写入通路尚未集成到此调度器。

# External actions
Resolve：只读连接。未调用 MiMo；未读取正确范围/切点。未写入或清理时间线。

# Blockers
必须先将淘宝调度器的最终交付阶段接入 `reusable_test_timeline` 路径，并让批次工作区/产品计划适配其受审计的测试 writer。当前直接运行会触及正式时间线，因此在代码接通并模拟验证前不能开始真实 DaVinci 写入测试。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮未新增代码；保留全部既有未提交改动。PR：无。

---

# Task
确认是否已具备开始 DaVinci 测试时间线剪辑的条件。

# Repo changes
本次只读检查，无代码修改。

# Runtime changes
实时 Resolve 项目身份认证通过；当前打开时间线为「测试」，项目内可见「素材」「成片」「测试」三条线。项目 doctor 通过，测试线稳定 ID 已登记。

# Validation
`resolve_connection_doctor.py`、绑定项目 `project_doctor.py`、`taobao_scheduler.py plan` 均运行通过。计划返回 `batch_id=null`、`products=[]`，因此调度器没有可执行商品批次。

# External actions
Resolve：只读连接。未清空、未写入、未导出。MiMo：未调用。

# Blockers
需要确定本轮要测试的商品 ID（或授权我从标题表中挑一个未剪商品作为测试样本），并据此建立批次及语义/范围审核方案。在有效方案门禁通过前，不清理测试线。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本次无代码修改；工作区原有改动保持不变。PR：无。

---

# Task
修复前一轮 13 项 Resolve 交付测试在共享合成 fixture 初始化阶段的失败。

# Repo changes
- `tests/test_delivery_pipeline.py` 将音频 mock 从已移除的 `audio_level_frames` 更新为当前 refinement 调用的 `audio_waveform_frames`。
- 为合成主语义审核回执补充 `semantic_map`，每条引用对应 ASR 保留词，符合当前 `delivery_editorial` 契约。
- 只调整测试夹具，没有放宽产品审核门禁或 Resolve 写入条件。

# Runtime changes
未连接或写入 Resolve；无运行时项目内容变更。

# Validation
- 原先 13 项 `tests.test_delivery_resolve`：全部通过。
- 交付管线、Resolve 交付、测试线 30fps/身份、ASR runtime 联合回归：44 项通过。
- Python 编译与 `git diff --check`：通过。

# External actions
Resolve：未连接、未写入。MiMo：未调用。

# Blockers
无。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。未暂存、未提交、未推送；保留现有工作区改动。PR：无。

---

# Task
优化 ASR 预启动与复用，并将循环测试固定到用户新建的 DaVinci「测试」时间线；每轮有效测试先备份，再清理上一轮测试结果。

# Repo changes
- `scripts/asr_runtime.py`、`scripts/asr_worker.py`、`scripts/windowed_asr_v2.py` 等增加本轮常驻 worker、后台预热与模型复用；预热安排在语义终审通过后，不提前触发 ASR 判定。
- `scripts/reusable_test_delivery.py` 改为按项目登记的测试线稳定 ID 定位写入目标，清空授权、备份和清理都限定到该线；片段帧数与时长按实时测试线帧率核算，支持当前 30 fps。
- `PROJECT_RULES.md` 和 `CHANGELOG.md` 记录测试线帧率不得写死或自动更改；新增 30 fps 换算和稳定 ID 清理边界模拟用例。

# Runtime changes
- 当前 Resolve 项目稳定 ID 与绑定项目匹配；测试线「测试」稳定 ID `<稳定ID-2>` 已写入项目状态。该线目前为空，30 fps、1080×1920、1 条视频轨、1 条音频轨、无标记。
- 当前调度器计划没有活动批次和商品，因此没有运行商品剪辑或写入 Resolve。下一次有有效商品方案时，写入入口会先完成所有审核门禁、导出并验证项目备份，再清空这条已登记测试线，随后追加并回读；正式「成片」与素材线不会作为清理对象。
- 本机真实 ASR 模型预热先前实测约 3.21 秒，同权重再次预加载约 0.001 秒。

# Validation
- Python 编译检查：通过。测试线绑定/30 fps 换算与 ASR runtime 定向模拟：9 项通过。
- 另尝试运行交付适配相关测试时，13 项在测试初始化阶段报错：共享旧 fixture patch 的 `owned_audio_refinement.audio_level_frames` 已不存在；不是本次测试线断言失败。此前全量测试仍有已记录的历史失败，未声称全量通过。
- 项目 doctor：通过；Resolve connection doctor：通过；调度器 `plan` 通过但无商品。`git diff --check`：通过。

# External actions
Resolve：只读连接与时间线状态核验；未写入、未清空、未导出。MiMo：未调用。

# Blockers
缺少本轮商品批次/商品 ID，无法构造真实剪辑方案并执行 DaVinci 写入测试。测试线已登记并通过只读核验；有商品批次后才能验证实际备份、清理、追加和回读事务。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。保留工作区原有修改；未暂存、未提交、未推送。PR：无。

---

# Task
将 Resolve 导出的正式视频上传到已登录的阿里妈妈创意中心视频素材页；每次最多 10 条，逐批等待上传成功后点击「确定」。

# Repo changes
- `PROJECT_RULES.md` 与 `CHANGELOG.md` 已补充专用 Chrome CDP 故障原因和恢复步骤；保留报告及其他文件中的既有改动。
- 上传临时暂存副本已清理；导出目录中的原视频未修改。

# Runtime changes
- 上传 123 条视频，共 13 批（12 批各 10 条，最后 3 条）。每批在页面显示全部上传成功后点击「确定」，弹窗正常关闭。
- Chrome 故障原因为专用 Chrome CDP LaunchAgent 已停止，9222 端口未监听；`open -a` 复用了未带 CDP 参数的既有 Chrome for Testing 进程。正常退出该专用实例后，通过项目健康脚本重启并确认 9222 `/json/version` 可用，随后 Playwright 成功重连。普通 Chrome 未关闭。

# Validation
- 每批待确认列表中的各文件均显示上传成功；最后一批 3 条上传成功并确认。上传完成后弹窗关闭。
- `git diff --check`：通过。
- 未运行测试；本次未连接或写入 Resolve。

# External actions
阿里妈妈创意中心素材页：上传并确认 123 条视频。MiMo：未调用。Resolve：未连接、未写入。

# Blockers
无。

# Git state
保留现有未提交修改，未暂存、未提交、未推送；本次新增的手册和报告改动与原有改动一并保留。无回退或覆盖其他工作区改动。

---

# Task
优化批次内 ASR 预启动和模型复用；先做模拟测试，通过后检查已绑定 DaVinci 项目。

# Repo changes
- `scripts/asr_runtime.py` 增加有序、带请求摘要校验的常驻 ASR worker；worker 在本次调度进程内复用，并在退出时关闭，支持超时和崩溃安全回收。
- `scripts/asr_worker.py` 增加 JSON-lines 服务和预加载操作；模型对象按权重摘要复用，最多保留主模型/备用模型。
- `scripts/windowed_asr_v2.py` 为直接运行路径增加按路径与权重摘要的模型缓存。
- `scripts/project_scheduler.py` 在商品语义终审通过后，后台启动 ASR 模型预热，与人物预检并行；未开始切点转写或改变视觉/语义判定顺序。命中已验证 ASR 阶段缓存时不等待模型加载。
- `scripts/registered_pipeline.py` 让真实转写等待正在进行的预热；预热失败不代替原 ASR 门禁或自动通过。
- `scripts/compute_metrics.py` 增加预热耗时指标；`tests/test_asr_runtime.py` 覆盖进程复用、异常回收、模型复用、权重变化隔离和直接加载缓存；`CHANGELOG.md` 记录行为。
- 本轮任务代码文件：上述 6 个脚本、1 个测试文件、`CHANGELOG.md` 与本报告。保留工作区其他既有未提交改动，未覆盖或回滚。

# Runtime changes
- ASR 目标模型真实预热约 3.21 秒；同一 worker 同权重再次预加载约 0.001 秒，权重身份一致。
- DaVinci 连接、稳定项目身份和淘宝模式只读认证通过；项目 doctor 与淘宝调度器 `plan` 通过。项目当前没有活动批次/商品范围，也没有登记的测试时间线；因此未处理媒体、未执行商品剪辑、未写 Resolve 时间线。

# Validation
- `py_compile`：本次涉及 Python 文件通过。
- ASR 定向模拟：18 项通过，包括新增 worker 测试和既有 ASR 缓存测试。
- 全量合成/单元测试：392 项运行，32 failures、148 errors；失败广泛涉及初始化输入更新、旧调度器/审计契约等，ASR 定向集未失败。本轮未建立干净基线，因此不将整仓库失败归因为本次改动，也不能报告全量测试通过。
- `git diff --check`：通过。
- 项目 doctor：通过。Resolve connection doctor 与调度器 `plan`：通过；由于没有批次且没有测试时间线，未运行 Resolve 写入 preflight、scheduler `run` 或商品编辑验收。

# External actions
MiMo called: no。Resolve connected: yes（只读认证）。export performed: no。

# Blockers
- 当前没有可执行的商品批次；已绑定项目未登记循环测试时间线。真实商品级 DaVinci 编辑验收需先有明确测试商品和已登记测试时间线；本轮未写正式交付时间线。
- 整仓库模拟测试未全绿；ASR 相关 18 项定向模拟通过，广泛失败项需与本次代码变更分开处理。

# Git state
工作区分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`（提交前状态快照）。全部改动未暂存、未提交、未推送。除本轮 ASR 预热文件外，保留的既有改动涉及 `PROJECT_RULES.md`、`scripts/adaptive_vision.py`、`scripts/asr_identity.py`、`scripts/codex_worker.py`、`scripts/final_quality_gate.py`、`scripts/product_checkpoints.py`、`scripts/project_scheduler.py`、`scripts/registered_pipeline.py`、Resolve 连接/试验/写入脚本、`scripts/semantic_evidence.py`、`scripts/sentence_boundaries.py`、`scripts/update_title_counts.mjs`、`scripts/workflow_scheduler.py` 与本报告。PR：无。

---

# Task
按用户确认以当前「成片」为正式版直接导出，文件名仅含 ID 与序号；按用户指定以出点减一帧控制片尾，不逐条扫描黑帧；将各 ID 导出条数回填到标题表“剪辑”列。

# Repo changes
- `scripts/update_title_counts.mjs` 增加对项目规则允许的 `ID` 表头兼容；保留仅更新“剪辑”列及导出回读保护。
- 其余代码改动沿用此前提交；本轮没有修改 Resolve 时间线或导出程序。

# Runtime changes
- 按实时项目稳定 ID 加载绑定工作区，只读来源时间线「素材」，以「成片」正式时间线为导出来源；时间线导出前后快照一致。
- Resolve 导出 123 条 H.264 视频，文件名均符合 `数字ID_序号.mp4`，无中文标题；43 个 ID 的条数已回填到表格“剪辑”列。
- Artifact Tool 导出并重新导入工作簿回读：43 行更新、总数 123，非目标单元格、公式、样式和表格形状均通过保护校验。

# Validation
- 项目 doctor、Resolve connection doctor、匹配淘宝调度器 `plan` 和 `diagnose` 均已运行；实时项目 ID、源/目标时间线 ID 与注册绑定一致。诊断显示商品 30 `WRITTEN`、无回滚点，未重跑编辑/追加。
- Resolve 各渲染任务完成；导出目录文件集合与 123 个预期名称完全相同，43 个 ID 均有对应文件，全部文件非空。每个片段的 `MarkOut` 使用结束边界减一帧。
- 按用户指示未对视频逐条运行黑帧扫描；因此报告导出完成和文件集核验，不声称完成黑帧扫描。
- 工作簿更新回读：通过。测试：未运行。`git diff --check`：本轮报告更新后待最终检查；未运行全量 preflight。

# External actions
MiMo called: no。Resolve connected: yes。Resolve write: no。export performed: yes。Workbook updated: yes。External semantic review: no。

# Blockers
- 黑帧逐条扫描按用户明确指示省略；如需黑帧结果，需另行运行扫描。

# Git state
分支 `codex/sentence-boundaries-sync`；本轮变更前 HEAD `da7d81f`。本轮未暂存、未提交、未推送；报告与 `scripts/update_title_counts.mjs` 为未提交修改。PR：无。

---

# 前次尝试记录（状态以本节之上最新结果为准）

# Task
按用户要求依项目流程继续导出当前 Resolve「成片」时间线，文件命名限定为 ID 与序号，不含标题；导出后检查黑帧并回填剪辑计数。

# Repo changes
- 本轮仅更新本报告；没有修改剪辑或导出实现。导出集成缺失，不绕过调度器自行渲染。

# Runtime changes
- 当前商品 30 状态为 `WRITTEN`；未重跑写入、未改动时间线、未导出视频，未修改工作簿。
- 淘宝调度器已生成当前批次计划。计划仅覆盖至 `resolve_delivery`；计划后读取 `diagnose`，未发现回滚点。

# Validation
- 重新读取实时 Resolve 稳定项目 ID，并按其唯一匹配到项目绑定工作区；当前项目模式为 `taobao_legacy`，源/目标时间线稳定 ID 与项目绑定相符。
- `project_doctor`：通过。`resolve_connection_doctor`：通过。匹配的 `taobao_scheduler plan`：通过，计划摘要 `3ae45be85c`。`taobao_scheduler diagnose`：商品 30 `WRITTEN`，无回滚点，并提示不得重放 Resolve 追加。
- 注册交付审计仍报告 `BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS`；当前计划的末阶段是 Resolve 写入，未实现导出验收，因此停止于门禁前。
- 测试：未运行。`git diff --check`：本轮报告更新后待最终检查。未运行全量 preflight。

# External actions
MiMo called: no。Resolve connected: yes。Resolve write: no。Export performed: no。Workbook updated: no。

# Blockers
- 注册导出与验收入口尚未迁移，无法按项目流程进行视频导出和黑帧验证；因此也没有可靠的各 ID 导出条数用于表格回填。
- 调度器诊断未给出恢复点；已有 `WRITTEN` 商品不可盲目重跑，以免重复追加。

# Git state
分支 `codex/sentence-boundaries-sync`；本报告更新前 HEAD `da7d81f`。本轮只修改报告；未暂存、未提交、未推送。PR：无。

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
- 商品20 `<商品编码及标题已脱敏>`：`FINAL_PASS`、正式写入 `成片`；3个版本、4个物理片段，事务 `VERIFIED`，预期/实际片段数均为4。
- 商品30 `<商品编码及标题已脱敏>`：语义、人物及最终质量审核通过，`WRITTEN`；3个版本、4个物理片段，事务 `VERIFIED`，预期/实际片段数均为4。
- 两个商品各有一个蓝色单帧起点标记；标记名称已规范为“商品编码｜标题”，并通过 `MARKERS_STANDARDIZED_AND_VERIFIED` 回读。帧位分别为192088、197010。源「素材」未改，未新建时间线。
- 两次 Resolve 追加均自动保存项目备份；没有执行导出。人工复核预览：无；两件均为正式时间线交付。

# Validation
- 绑定项目稳定 ID `<稳定ID-3>` 与实时 Resolve 一致；源时间线「素材」ID `<稳定ID-1>`，目标「成片」ID `<稳定ID-4>`。
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

- 实时 Resolve 项目 `<淘宝项目>`，稳定 ID `<稳定ID-3>`；来源 `素材`（ID `<稳定ID-1>`）只读，目标 `成片`（ID `<稳定ID-4>`）。项目 doctor、连接 doctor 与 `taobao_scheduler.py plan` 通过，批次 `20261022_products36_38`。
- 商品 37 通过最终门禁，调度器正式写入并回读为 `WRITTEN`。商品 36、38 的语义与人物画面审核已完成；ASR 切点仍未确认，状态为 `REPAIRABLE`，回退点 `local_audio_cutpoint_review`。
- 商品 36、38 各三个版本已按 `manual_cutpoint_preview` 流程写入并回读为 `PREVIEW_WRITTEN_AND_VERIFIED`。共 6 条预览、9 个素材段，明确属于人工复核预览，不是正式交付。
- 商品 37、36、38 的起点商品标记经调度器标准化并回读；每商品一个蓝色单帧标记，名称为 `商品 ID｜标题`。未添加版本标记或绿色范围标记。源时间线未写入，未新建时间线；未导出。
- 本次没有调用 MiMo 或其他外部语义审核服务。Resolve 写入仅为商品 37 正式版本及 36、38 人工预览。
- 命令行参数顺序首次误用、以及普通语义预览门禁拒绝了未确认切点；已先运行 `diagnose`，再用调度器的 `--preview-rough-semantic` 人工切点预览模式完成。最后一次调度器诊断显示 37 `WRITTEN`、36/38 `REPAIRABLE`，预览通过。

# 最新进度：商品 34（2026-10-22）

- 实时 Resolve 项目：`<淘宝项目>`，稳定 ID `<稳定ID-3>`；只读 `素材`，只写 `成片`。两项 doctor 与调度器 `plan` 通过。
- 新批次：`20261022_product34_visual_repair`，仅含商品 34。已绑定标题与字幕索引，未使用根目录旧状态或外部语义审核服务。
- 旧画面采样显示 `451-480.mp4` 源片约 381.5–408.1、420.8–435.4、501.9–528.0、602.3–649.0 秒连续采样点未见人物。这是采样证据，不代表每帧保证；新候选窗避开这些时段。
- 新调度器运行得到三条方案，时长约 45.17、53.03、46.10 秒；语义、ASR 切点和本机人物采样终审均 PASS。每条人物采样估算为 100%，证据等级 `ENGINEERING_SAMPLED`，非逐帧认证；ASR 未解决边界 0 个。
- Resolve 已实际追加 3 条到 `成片`，共 8 个物理素材段；但写后复验 `FAILED_VERIFICATION`：第 6 段的源起止帧均比方案早 1 帧（预期 15965–16190，实读 15964–16189），其余 7 段一致。事务状态 `FAILED_VERIFICATION`，调度器 `diagnose` 无有效回滚点；严禁重放追加。商品 34 当前为“已写入、待切点核验”，**不是正式交付**。
- `素材` 时间线快照前后相同。商品 34 在 `成片` 起点有且仅有一个蓝色单帧标记；已通过调度器将标记名称改为 `<商品编码及标题已脱敏>` 并回读。未放绿色范围标记。
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
---

# Task
将本地模型在剪辑任务结束后的清理要求设为项目默认规则，并释放上一轮中断任务遗留的视觉模型。

# Repo changes
- `PROJECT_RULES.md` 增加收尾约定：任务结束、失败或中断后先确认没有其他活动任务使用模型，再通过模型服务接口卸载；不关闭仍被其他任务共用的服务。
- `CHANGELOG.md` 记录该默认规则。

# Runtime changes
- 确认 `qwen3-vl:8b` 由 Ollama 提供，运行在 GPU 上，内存占用约 11 GB；相关剪辑调度器和 ASR worker 已退出。
- 停止该模型会话并确认 Ollama 当前无已加载模型。

# Validation
- Ollama 模型状态复查：无已加载模型。
- `git diff --check`：本次规则和报告改动后执行，通过。
- 自动化测试、项目 doctor、Resolve preflight：本次未运行（仅规则文档变更与本地模型清理）。

# External actions
MiMo called: no. Resolve connected: no. export performed: no.

# Blockers
无。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本次规则、变更记录及报告为未暂存改动；仓库另有此前未提交的代码、测试和文档改动，均保留且未纳入本次修改。无暂存内容；未提交、未推送。此状态为后续提交前快照。PR：无。

---

# Task
查明盲测商品未写入 DaVinci 时间线的原因，并修复调度器的前置校验和人物审核耗时问题。

# Repo changes
- `workflow_scheduler.py` 在淘宝批次索引、范围匹配、商品分析前记录并运行 Resolve preflight；只有项目身份及 Resolve 连接通过后，才构建人物检测器并进入分析。
- `resolve_connection_doctor.py` 支持检查本批精确指定目标时间线：必须唯一、稳定 ID 可读、不是来源线且同时有音频/视频轨。
- `adaptive_vision.py` 将独立帧审核限制为最多 4 个 worker，保留逐帧样本、Apple Vision → Qwen → Codex 顺序和结果缓存关联；不减少审核样本或放宽门槛。
- 补充 preflight、目标时间线和 worker 并发合成回归；更新测试夹具以匹配当前帧时钟/Apple detector 协议，并让项目注册 fixture 显式 force 初始化。
- `CHANGELOG.md` 记录修复。

# Runtime changes
- 只读检查盲测运行记录：商品分析阶段总耗时约 1270.5 秒，其中 `selection_vision` 约 1178.1 秒；该阶段处理 29 个画面，原先逐帧串行执行。任务被中断时未通过最终审核，因此没有进入写入阶段。
- 中断后 Resolve doctor 返回应用未运行。旧流程把 Resolve preflight 放在商品分析之后，故应用关闭没有被提前发现。
- 本轮没有连接 Resolve、分析媒体或写入时间线。

# Validation
- 调度器与 Resolve 目标校验测试：13 项通过。
- 视觉审核合成测试与 worker 并发测试：29 项通过。
- Python 编译检查与 `git diff --check`：通过。
- `test_adaptive_vision` 初次全模块尝试暴露了旧帧时钟/Apple 协议测试夹具不匹配；更新夹具后，本轮通过视觉审核类 27 项及独立 worker 测试 2 项。ASR 集成用例未纳入本轮定向验证。
- 项目 doctor、真实 Resolve preflight、真实素材/时间线验收：本轮未运行。

# External actions
MiMo called: no. Resolve connected: no. export performed: no.

# Blockers
修复尚未在真实 Resolve 会话和素材上验收；需要 Resolve 打开并按调度器重新执行时，前置检查才会验证当前项目和目标线。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本任务改动未暂存、未提交、未推送。工作区另有先前未提交的代码、测试和规则改动，均保留；PR：无。此状态为提交前快照。

---

# Task
修正视觉审核模型升级逻辑，记录每帧各模型是否调用、判定结果，并分别记录 Qwen 与 Codex 耗时；按用户要求重新运行测试。

# Repo changes
- `scripts/adaptive_vision.py` 将升级规则固定为：Apple Vision 判定有人时停止；判为无人、不确定或衣物特写时调用 Qwen；Qwen 判有人时停止，判无人或不确定时调用 Codex。每帧结果保存三层调用状态和标签。
- `scripts/compute_metrics.py` 增加独立 `codex_fallback` 耗时与调用统计；现有 `qwen_fallback` 现在实际计时。视觉缓存协议升级，旧缓存需重新分类以补全新 trace。
- `config/local_models.json` 同步升级条件和本地复核策略；`tests/test_adaptive_vision.py` 新增升级矩阵及分层指标回归；`CHANGELOG.md` 记录改动。

# Runtime changes
- 仅运行合成测试，Qwen、Codex、Resolve 和真实素材均未调用。

# Validation
- 视觉审核主体类：29 项通过；视觉 worker 并发测试：2 项通过。
- 视觉审核全文件：30 项通过，2 项现有 ASR 缓存集成测试失败。失败表现为 ASR 窗口数期望 1、实际 2，以及转写调用期望 2、实际 4；与本轮视觉审核路径改动无关，未在本轮修复。
- `py_compile` 与 `git diff --check`：通过。
- 项目 doctor、Resolve preflight、真实模型计时和真实素材审核：未运行。

# External actions
MiMo called: no. Resolve connected: no. export performed: no.

# Blockers
尚未实测真实模型的耗时，也未在 Resolve 和真实商品素材上验收。完整视觉文件包含两项上述 ASR 集成失败。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮改动未暂存、未提交、未推送；工作区此前的未提交变更均保留。PR：无。此状态为提交前快照。

---

# Task
按项目工作流重新运行视觉逻辑模拟测试，并尝试通过淘宝调度器进入 DaVinci 测试线验证；记录阻塞原因。

# Repo changes
- `scripts/workflow_scheduler.py` 修复无活动批次时 `diagnose` 崩溃的问题，改为返回明确的无批次状态。
- `tests/test_workflow_scheduler.py` 增加无批次诊断测试；`CHANGELOG.md` 记录修复。

# Runtime changes
- 通过 Resolve live ID 只读确认项目绑定身份和指定测试目标线身份，Resolve 连接正常；未清理或写入时间线。
- 调度器计划显示没有活动批次和商品。调用 `run --allow-test-reuse` 后在媒体分析前被阻断：淘宝模式尚未接入测试时间线复用交付适配器。
- `diagnose` 原先因无批次而崩溃；修复后返回 `no_active_batch`，无回滚点或可续跑命令。

# Validation
- 项目 doctor：通过；指定测试目标的 Resolve connection doctor：通过。
- 视觉审核 29 项、视觉并发 2 项、调度器 10 项、Resolve 目标 4 项、测试交付模拟 3 项：共 48 项通过。
- Python 编译检查与 `git diff --check`：通过。
- 淘宝调度器 plan：通过生成计划；产品列表为空。调度器 run：按缺失的测试线适配器能力安全阻断，未启动媒体分析。
- 真实素材/ASR/人物模型审核及时间线写入：未运行。当前没有活动商品批次，且淘宝测试线清理/复用交付能力未接入。

# External actions
MiMo called: no. Resolve connected: yes (read-only identity and target checks only). export performed: no.

# Blockers
继续到 DaVinci 项目测试前，需要先接通淘宝调度器的测试线事务（备份、清理本轮范围、逐商品写入与回读），并建立本轮明确的商品批次。普通追加写入无法满足清理上一轮结果的要求，故未用它替代测试。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮修改未暂存、未提交、未推送；此前未提交改动均保留。PR：无。此状态为提交前快照。

---

# Task
按用户指定的 DaVinci「测试」时间线启动单商品盲选真实流程测试，并记录最终结果。

# Repo changes
- 本轮沿用既有调度器、视觉审核修复和批次工作区创建工具；没有为通过审核而放宽门禁。
- 更新本报告，记录真实运行耗时、失败门禁和回退点。

# Runtime changes
- Resolve 项目和用户指定的「测试」时间线身份预检通过；本轮测试线初始为空。
- 单商品盲选批次 `20261023_visualfix_blind_25` 的索引、范围匹配与范围发布通过。
- 项目分析与质量审核运行 1,557.9 秒后失败；商品 25 状态为 `BLOCKED`，原因 `selection_vision_uncertain_or_blocked`。调度器记录最近可复用阶段为 candidate、rough、ASR，回退点 `local_asr_edge_review`，此前修复尝试 2 次。
- 最终交付门禁为 `BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS`，最终通过数 0，写入数 0；未向时间线写入任何片段。
- 本轮视觉审核启动的本机 llama-server 已在任务结束后终止并确认退出。

# Validation
- 项目 doctor、Resolve 指定目标预检与调度器 plan：通过。
- 调度器真实 run：退出码 2；`diagnose` 已运行并确认回退点。
- 测试线写入与时间线回读：未执行，因为最终审核和交付门禁未通过。
- `git diff --check`：通过。

# External actions
Resolve connected: yes（只读身份和目标预检）；Resolve timeline written: no；export: no；MiMo: no。

# Blockers
视觉审核未能为该商品生成可接受的选段审核结论，且调度器报告交付验证器未迁移。按项目硬门禁，本轮不得写入时间线。下一次应从诊断给出的 `local_asr_edge_review` 回退位置修复或续跑，不得复用失效审核证据。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。工作区改动未暂存、未提交、未推送；此前用户未提交变更均保留。PR：无。此状态为提交前快照。

---

# Task
按用户要求优化分阶段视觉审核和 Qwen 资源使用，并只运行针对视觉审核的定向测试，不运行完整流程测试。

# Repo changes
- `scripts/adaptive_vision.py` 将审核拆成完整 Apple Vision 阶段、最多四帧一组的本机 Qwen 拼图复核、Qwen 卸载、Codex 拼图终审。Qwen 或 Codex 确认有人时可覆盖前级无人；Codex 无人结论才作为最终无人判断。模型错误仍不能转成无人证据。
- 新增 `scripts/vision_contact_sheet.swift`，生成带帧号的 2×2 联系表；拼图源码已加入视觉生产者和检查点身份指纹。初始样本和细化样本的临时帧文件名改为唯一键，避免阶段间互相覆盖。
- 增加经证明的视觉结果缓存读取路径，命中时跳过模型；批量接口遇到缺失、重复或异常面板返回时，将不能确定的面板保留为 `UNCERTAIN`。
- `classify_frame` 单帧兼容入口在交给 Codex 前卸载 Qwen；持久流程规则和 `config/local_models.json` 已更新为 Qwen 不预加载、按需使用并立即卸载。
- 新增审核阶段顺序、Qwen 卸载时序、两级正向覆盖及异常面板解析的视觉回归测试。

# Runtime changes
- 未连接 Resolve，未读取或写入真实素材/时间线；未调用 Qwen 推理或预加载模型。本轮测试只运行本地合成审核数据。
- 用四张纯色合成图生成四宫格，输出为 1920×1080 PNG；拼图工具协议版本检查通过。

# Validation
- `test_adaptive_vision.AdaptiveVisionTests` 与 `test_adaptive_vision_workers`：34 项通过。
- 本次涉及的 Python 文件编译通过；`git diff --check` 通过。
- Swift 四宫格工具编译通过；合成图渲染与尺寸检查通过。
- 未运行完整测试套件、ASR 集成测试、调度器全流程测试或 DaVinci 测试。

# External actions
Qwen model loaded: no；Codex 图像复核调用：no；Resolve connected: no；真实素材访问：no；MiMo: no。

# Blockers
四宫格分阶段行为已通过合成测试，但尚未对真实画面衡量 Vision、Qwen、Codex 的准确率或实测节时。准确率需要带人工真值标注的样本集，不能从本轮合成测试推断。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮改动未暂存、未提交、未推送；工作区中先前存在的未提交改动均保留。PR：无。

---

# Task
确认上一轮三版视频帧是否仍在，并尝试针对它们重新运行视觉审核。

# Repo changes
- 未修改流程代码；只在本报告记录检查结果。

# Runtime changes
- 重新连接 Resolve 并确认稳定项目 ID 与绑定工作区登记一致；只读检查当前绑定「测试」时间线，视频片段数为 0。
- 上一轮对应批次状态只到 `subtitle_indexed`，没有持久化三版剪辑方案或逐帧 PNG/JPG。项目中存在其他视觉缓存，但无法证明属于这三版，且缓存本身不包含可供拼图复核的原始画面。
- 未读取真实视频、未加载模型、未改动时间线或导出内容。

# Validation
- 指定工作区的 `project_doctor`：通过。
- Resolve connection doctor：通过，项目稳定 ID 与工作区和测试线绑定记录匹配。
- 时间线只读检查：测试线 0 个视频片段；项目内其他时间线未用于本次复测。
- 视觉复测：未能启动，缺少可识别的三版方案及测试线片段。未运行全流程测试。
- `git diff --check`：本轮仅追加报告；未执行额外测试套件。

# External actions
MiMo called: no；Resolve connected: yes（只读）；export performed: no。

# Blockers
原三版帧图和方案未持久保存，测试线为空，无法对“原来的三版”做同源复测。需要先把待复测的三个版本恢复到绑定的「测试」时间线，或提供对应的当前方案检查点；之后可只运行视觉审核阶段。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮仅更新报告，未暂存、未提交、未推送；其他已有工作区改动均保留。PR：无。

---

# Task
按用户要求收窄 Vision 到 Codex 的升级范围：只有 Apple Vision 明确确认无人的帧交给 Codex。

# Repo changes
- `scripts/adaptive_vision.py` 将送审条件固定为 Apple 标签 `NO_PERSON`。`CLOTHING_CLOSEUP`、`UNCERTAIN` 与 `DETECTOR_ERROR` 不送 Codex，保留各自标签或阻断；Vision 判为有人也不送审。
- 视觉缓存与审核协议升版，旧升级路由产生的缓存不作为当前结果复用。
- `PROJECT_RULES.md` 与 `CHANGELOG.md` 更新为该路由规则；对应既有测试断言同步调整，但本轮没有运行测试。

# Runtime changes
- 未连接 Resolve、未读取或写入时间线；未启动 Qwen 或其他视觉模型；未调用 MiMo。

# Validation
- Python 编译：通过。
- `git diff --check`：通过。
- 单元测试：未运行。
- project doctor / Resolve preflight：未运行（本轮仅改代码与持久规则，未执行媒体或 Resolve 操作）。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。

# Blockers
本轮未用真实帧重新测量准确率或 Codex 送审量。此规则会减少升级帧数，但对于 Vision 判为不确定、近景或检测错误的帧不再提供 Codex 兜底；这些帧仍按当前硬规则保留不确定状态或阻断。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮相关文件：`scripts/adaptive_vision.py`、`PROJECT_RULES.md`、`CHANGELOG.md`、`tests/test_adaptive_vision.py`、本报告。工作区含此前未提交更改；均未暂存、未提交、未推送。PR：无。

---

# Task
停用 Qwen 视觉审核，改为 Apple Vision 全量初筛后将疑点帧交给 Codex 四格审核；运行测试时间线上的单商品盲测并记录结果，同时说明 Vision → Codex 的送审条件与准确率限制。

# Repo changes
- `scripts/adaptive_vision.py` 将生产审核路径改为 Apple Vision 完整检测后，批量把 `NO_PERSON`、`CLOTHING_CLOSEUP`、`UNCERTAIN` 及仍有可用帧的 `DETECTOR_ERROR` 送入 Codex 四格复核；Vision 判为有人不升级。Codex 复核可覆盖 Vision 的无人判断。
- 视觉审核路径不再读取 Qwen 模型身份、不预加载、不调用 Qwen；旧的独立辅助函数仍留在模块中，但没有接入本次生产审核路径。
- 更新 `PROJECT_RULES.md` 与 `CHANGELOG.md`，固定 Apple Vision → Codex 四格审核顺序；调整并补充 `tests/test_adaptive_vision.py`。

# Runtime changes
- 项目 doctor、Resolve 连接身份及用户指定的「测试」时间线预检通过；测试线运行前为空。
- 盲测商品 25 的语义准备和分析完成后，在 ASR 边界对齐阶段阻塞：`sentence_local_text_not_found_expand_asr_window`。总运行时间 548.152 秒，超过每商品 5 分钟目标。
- 调度器诊断建议回退到 `local_asr_edge_review`；状态 `BLOCKED`，不可直接用相同证据重试。最终通过数 0、写入数 0、导出数 0。未写入 Resolve，测试线仍为空。
- 本次续跑未启动 Qwen；任务结束检查时 `ollama ps` 为空，未发现活动的调度器、ASR worker 或 llama-server。
- 没有人工逐帧标注真值，因此本轮无法计算 Vision 准确率；50 帧模型分歧记录也不能直接代表准确率。

# Validation
- `test_adaptive_vision.AdaptiveVisionTests`：32 项通过。
- 完整 `test_adaptive_vision` 模块：35 项中 2 项 ASR 集成测试失败，涉及 ASR 窗口复用预期（预期模型调用 1 次、实际 2 次；预期 2 次、实际 4 次）；其余通过。该两项不属于本轮视觉改动。
- `python3 -m py_compile scripts/adaptive_vision.py`：通过。
- `git diff --check`：通过。
- 调度器盲测实际运行：ASR 阶段阻塞；未进入 Resolve 写入或导出。

# External actions
Resolve：连接并进行只读身份/目标检查；时间线写入：无；导出：无；Qwen：未启动；MiMo：未调用；Codex：用于自动化视觉审核流程中的四格复核。

# Blockers
ASR 局部文本与候选句对齐失败，需要从诊断回退点扩大或修复 ASR 证据后再运行。Vision 准确率需先对代表性抽帧进行人工有人/无人真值标注，再与 Vision 输出比较，分别计算准确率、召回率和误报率；当前没有这份真值集。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。视觉改动涉及 `scripts/adaptive_vision.py`、`PROJECT_RULES.md`、`CHANGELOG.md`、`tests/test_adaptive_vision.py`。工作区还包含此前已有的未提交修改及未跟踪文件，均未清理或覆盖；未暂存、未提交、未推送。PR：无。

---

# Task
将用户截图提出的 Apple Vision 输入检查、ROI、小人物/相邻帧优化、阈值校准和轻量二次检测建议纳入 Vision 优化路线。

# Repo changes
- `PROJECT_RULES.md` 增加经人工标注帧验证的优化实验清单：输入方向/尺寸/裁剪核验；小人物 ROI 对照；相邻帧证据与重复送审削减；按帧型校准阈值；针对实证低置信失败类评估轻量二次检测。
- 标明这些属于候选实验，不改变现行阈值、抽样或仅 Apple `NO_PERSON` 送 Codex 的生产规则；明确不得跨帧传播“无人”结论。
- `CHANGELOG.md` 记录优化路线。未改运行时代码。

# Runtime changes
- 未连接 Resolve、未读取媒体或启动模型；未调用 MiMo。

# Validation
- 单元测试：未运行。
- project doctor / Resolve preflight：未运行（没有媒体或 Resolve 操作）。
- 尚未建立人工标注样本集，未做精度或性能基准比较。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。

# Blockers
需要人工标注并覆盖小人物、遮挡、服装近景、纯商品与空画面，才能判断候选优化的准确率、漏检、误报、耗时与 Codex 送审量变化。取得样本结果前不启用这些实验。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮相关文件：`PROJECT_RULES.md`、`CHANGELOG.md`、本报告。工作区其他既有修改均保留；本轮未暂存、未提交、未推送。PR：无。

---

# Task
重新梳理淘宝调度器的真实执行逻辑、上次商品测试耗时和现有优化缺口，不运行真实媒体测试或写入 Resolve。

# Repo changes
- 本轮未修改流程代码，只更新本报告。
- 当前真实顺序为：Resolve 预检与索引/范围 → 单商品 Codex 选择 → Codex 语义终审 → 启动 ASR 预热 → 选段完整人物审核 → 局部 ASR → 物理方案 → 最终完整人物审核 → 硬门禁 → Resolve 写入。调度计划展示的阶段名没有体现 ASR 前的选段人物审核，也没有区分两次人物审核。
- 已实现：Resolve 进程会话内项目身份缓存；显式选择现有目标时间线；持久 ASR worker 与预热；帧级缓存；Qwen 已退出生产审核路径；只有 Apple `NO_PERSON` 送 Codex。
- 尚未落实到运行代码：Vision 输入/ROI/相邻帧/阈值实验；淘宝测试线自动清理复用；ASR 对齐失败后自动写入语义预审时间线；多商品并发或多窗口调度。

# Runtime changes
- 未连接 Resolve、未启动模型、未处理媒体、未写时间线。
- 只读分析上次商品 25 指标：总耗时约 548 秒；选段人物审核约 362 秒，其中 Codex 视觉复核约 339 秒、41 帧、11 次调用；语义选择与终审约 131 秒；ASR 约 53 秒。该指标来自收窄为 `NO_PERSON` 唯一路由前的运行，不能代表新路由实测速度。
- 首要浪费点：代码先对 Apple 判为无人或服装近景的初始分层生成 10 个细化帧，再统一交给 Codex。若 Codex 把初始帧改判为有人，已生成和审核的细化帧不会参与结果，形成白算。
- 第二个浪费点：选段阶段运行完整两层人物比例审核，ASR 形成物理切点后又运行一遍完整人物审核。帧级缓存只在源帧完全相同时命中；切点改变会移动分层采样位置。
- 商品组仍按商品串行执行。Resolve 写入本身串行是正确的，但语义等待、本地 Vision 和其他商品准备尚未形成受控流水线。

# Validation
- 对调度器、项目处理循环、Vision 两层采样、ASR 回退、Resolve 会话身份、测试线写入器及上次耗时指标进行了只读核对。
- `git diff --check`：通过。
- 单元测试、project doctor、Resolve preflight、真实媒体基准：本轮未运行。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。

# Blockers
- P0：将 Vision 改为“初始 Apple 全量 → 仅初始 `NO_PERSON` 给 Codex → 仅 Codex 仍确认无人的分层再生成 10 个细化帧 → 细化帧仅 Apple `NO_PERSON` 给 Codex”。这直接针对历史运行的最大耗时。
- P0：把 ASR 错误从产品阻塞改为切点降级。语义终审和人物预筛通过后，若局部文字锚点仍失败，调度器应自动调用已有语义预审写入器，按字幕边界写到本批指定时间线并回读，供人工修边；目前只返回 `REPAIRABLE_ASR_EDGE`，需要另行手动启动预审操作。
- P0：接通淘宝可复用测试线清理事务。当前正式写入器发现已绑定测试线非空会拒绝，`reusable_test_delivery.py` 的备份、清理与写入能力没有接入淘宝调度器；显式时间线追加仍设置 `clear_existing=false`。
- P0：移除项目审核中的固定 `BLOCKED_UNMIGRATED_RESOLVE_EXPORT_VERIFIERS` 占位结果。淘宝 Resolve 写入器已接入，质量审核应报告 `READY_FOR_DELIVERY` 或真实失败原因，写入后再报告回读状态。
- P1：将 ASR 前的人物检查降为快速预筛，只定位禁用源区间；在物理切点形成后执行唯一一次完整比例审核。否则同一商品可能做两次完整视觉比例计算。
- P1：ASR 首次、扩窗和备用模型应按已标注切点集比较。当前从 large-v3-turbo 扩窗后再用 medium 扩到约 24 秒；最后错误仍来自字幕文字与 ASR 词的匹配器，不能假定更换模型必然解决。先统计失败类型，再决定是否调用备用模型。
- P1：更新调度计划阶段名，使其与真实顺序一致；拆分 `selection_person_prescreen` 与 `final_person_audit`，避免诊断时误以为只运行一次 Vision。
- P1：删除 `adaptive_vision.py` 中已脱离生产路径的 Qwen 函数及旧测试替身，防止未来误接回模型加载。
- P2：批内最多并行准备两个商品。Codex 等待、字幕准备与 Apple Vision 可交错；共享 ASR worker 和 Resolve 写入继续串行，避免 18GB 内存压力。
- P2：把语义终审改成按 variant 封存回执。视觉返修只重审改变的 variant，锁定版本复用原回执，减少整商品重复终审。
- P2：完成已登记的 Vision 标注基准，再启用 ROI、相邻帧人物证据复用、分帧型阈值和轻量二次检测。无人结论不得跨帧传播。
- 建议验收顺序：先改 Vision 细化顺序并跑同一批视觉专项测试；再接 ASR 预审降级和测试线清理；最后做单商品真实计时。目标为单商品总耗时不超过 300 秒，且任何失败都能定位到单一阶段并从有效检查点续跑。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f`。本轮只更新本报告；工作区其他已有修改与未跟踪文件均保留。未暂存、未提交、未推送。PR：无。

---

# Task
按用户要求随机抽取两款商品并行剪辑，其中一款的三条成片写入“测试”时间线，不导出；按字幕判定讲解范围，最多三次返修，并记录运行指标。用户另要求不执行项目计划、不登记下一项长任务。

# Repo changes
- 本轮未修改代码；未运行项目计划或登记新任务。
- 原分支 `codex/sentence-boundaries-sync`、HEAD `da7d81f74891` 和全部已有未提交改动均保留。

# Runtime changes
- 用户确认 Resolve 已打开。沙盒外 fresh identity 检查通过：项目稳定 ID `<稳定ID-3>`，绑定工作区 `<稳定ID-5>`；项目“<淘宝项目>”，当前时间线“测试”，目标稳定 ID `<稳定ID-2>`，路由 `taobao_legacy`。
- 项目 doctor 通过。淘宝调度器只读 diagnose 返回无活动批次、无活动长任务，且 `run/recover/write` 三项 hold 仍在。
- 项目状态发现两笔未解决的 `FAILED_VERIFICATION` 追加事务：商品 1（`20261024_fastest_blind_p01`，错误“新增素材段2不匹配”）和商品 34（`20261022_product34_visual_repair`，错误“新增素材段6不匹配”）。商品 1 记录的前两段预期/实际源帧分别为 `[14317,15635]` vs `[14317,15634]`、`[16194,16565]` vs `[16193,16565]`，均有一帧边界差异；事务和商品状态均未改写。
- 用户先前明确要求保留失败状态、暂不恢复；本轮请求虽授权新测试的剪辑与写入，但未处理这两笔失败事务，也未解除项目级 hold。按既有“失败事务先明确处理才启动新测试”约束，停止在调度启动之前。
- 没有创建批次、抽取商品、分析媒体、启动模型、清理/写入时间线或导出；Resolve 时间线保持本轮未触碰。阶段计时、缓存、抽帧/拼图和返修计数均未产生。

# Validation
- Resolve fresh identity 与目标时间线 ID 检查：通过。
- 项目 doctor：通过。
- 淘宝调度器 diagnose：检测到项目限制和两笔未验证事务，正确阻止执行。
- 本轮未运行针对性模拟、模拟全流程或真实媒体测试；未运行源码测试。

# External actions
Resolve：已连接，只读身份与诊断。MiMo：未调用；Qwen：未加载；其他模型进程：本轮未启动。时间线写入/导出：无。

# Blockers
- 两笔失败事务都尚未明确处理；`PROJECT_TASKS.json` 中 `run`、`recover`、`write` hold 仍生效。新的随机并行测试因此不能安全启动，不能绕过 hold 或改写失败回执。
- 下一步：先对两笔失败事务分别做独立逐段只读核验并保留 FAILED 状态；明确它们与当前“测试”时间线的关系，再由用户决定是否解除本轮测试所需的 `run/write` 限制。核验通过前不清空、追加或恢复任何内容。之后才可按用户要求运行一次新的淘宝调度测试，并将最多三次返修、2 秒间隔、标记与无黑帧检查纳入计划。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。仅追加本报告；工作区原有改动全部保留，未暂存、未提交、未推送，未创建 PR。

---

# Task
用户明确授权清理注册稳定 ID 的“测试”时间线并开始本轮测试：随机两款商品并行剪辑，只把随机选中的一款三条成片写入该时间线，不导出；依据字幕判断讲解范围，最多三次返修，记录耗时和缓存/视觉指标。本轮不得切换项目计划，也不得登记后续任务。

# Repo changes
- 针对本任务做了一轮有界调度修复：项目任务事务阻塞按目标时间线 ID 限定；仅当用户明确授权的目标清理事务回读通过后，记录旧失败事务被清理替代，原始事务状态与报告哈希保持不变。批次间并行审核可通过 `--delivery-product-id` 限制单一商品交付，并将该范围绑定到计划/预审/交付。
- 新增任务事务隔离、唯一交付范围和内部计划校验的合成回归；更新 CHANGELOG。分支、HEAD、进入任务前所有未提交改动均保持。

# Runtime changes
- 通过实时 ID 认证项目 `<淘宝项目>`，稳定项目 ID `<稳定ID-3>`；目标“测试”时间线 ID `<稳定ID-2>`。项目/Resolve doctor 与调度计划通过。
- 从登记标题索引 43 款商品按随机种子 `<数值已脱敏>` 抽到商品 22、27；商品 27 被随机选为唯一交付对象。创建本轮一次性任务，商品范围 `[22,27]`、返修上限 3、导出关闭。没有执行项目 PLAN.md，也没有登记后续任务。
- 按用户明确授权，通过淘宝调度器的可复用测试线事务备份并清理目标时间线：移除 14 个音视频项和 1 个标记；备份为 `<淘宝项目>_before_reusable_test_20261026_093743.drp`，SHA-256 `6b6cf9cb9903`，清理回读 `READBACK_VERIFIED`。旧商品 1 测试线失败事务仍为 `FAILED_VERIFICATION`，仅记录该时间线内容已由经验证的用户清理替换；商品 34 的失败事务在“成片”时间线上，状态保留且未触碰。
- 唯一真实测试 run 通过预检、测试线清理、标题索引和字幕索引后，在 `batch_semantic_range_matching` 阶段失败：`project_range_matching_task_too_large_product_27`。代码对范围匹配 payload 设置 120,000 字节上限；商品 27 本次任务超过该限制，触发点在 Codex 调用之前。没有进入并行商品剪辑、语义审核、ASR、人物抽帧、拼图或 Resolve 成片写入。商品 27 三条成片未写入；当前测试线为空。
- 阶段时间：Resolve 预检 0.498 秒；测试线备份/清理/回读 1.951 秒；标题索引 0.824 秒；字幕索引 0.916 秒；范围匹配 20.159 秒后失败。调度器阶段合计 24.348 秒，运行记录墙钟约 24.77 秒，未归因约 0.42 秒。索引输出为“生成或校验”，无精确缓存命中数；抽帧 0、拼图 0、本轮返修 0、ASR 窗口 0。未做 300 秒门槛判断。
- 首次 run 在媒体/Resolve 阶段前因内部二次计划校验漏传交付商品 ID 而中断；diagnose 后修复该参数传递，保留了这次中断记录。为仍只允许一次真实媒体测试，将当前任务的调用预算从 1 调整为 2（一次前置失败 + 一次真实 run），理由已写入项目决定记录；第二次 run 已是唯一真实测试，本轮停止，不再重跑。

# Validation
- 首轮定向加模拟全流程组合运行共 79 项时，任务桩/计划边界测试返回 4 个失败项；诊断后修正测试桩与内部 plan 参数。
- 最终项目任务/调度器定向回归：40 项通过；修改文件编译和 `git diff --check` 通过。模拟全流程模块在首轮组合运行中没有单独报错，但未在诊断修正后重新运行整组慢速模拟。
- project doctor、Resolve fresh identity、目标时间线稳定 ID 校验通过。
- 真实测试失败后，淘宝调度器 diagnose 定位到 `batch_semantic_range_matching`，耗时 20.159 秒，未给出续跑命令；按限制不尝试修复或换商品重测。

# External actions
Resolve：已连接；仅清理获授权的“测试”线，未写入成片或其他线。导出：无。MiMo/Qwen：未调用/未加载。批内按商品 22、27 顺序匹配：商品 22 的范围 worker 返回 PASS 并生成本地 receipt（18.951 秒，输入 46,484 tokens）；随后商品 27 在调用其 worker 前因 120,000 字节 payload 上限失败，整个批次范围清单没有发布。本轮结束后进程检查没有发现 ASR、Codex worker 或模型服务残留。

# Blockers
- 商品 27 的范围匹配任务体积超过 120 KB 限制，批次保持失败；项目级商品旧检查点未因本轮失败升级或重写。
- 下一步应针对范围匹配任务分块/瘦身，保持标题锚点、完整字幕边界证据和相邻商品上下文；增加超过大小阈值的模拟覆盖。取得明确的新测试授权前不重复运行本批、不抽新商品、不继续改这轮代码。
- 成片时间线上的商品 34 `FAILED_VERIFICATION` 仍保留；恢复暂停仍有效。测试线内容按本轮授权已清理并备份，未写入新结果。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。公开代码改动涉及 `scripts/project_task.py`、`scripts/workflow_scheduler.py`、对应两项测试和 `CHANGELOG.md`；本报告及本轮任务/批次、备份属于本地运行记录。未暂存、未提交、未推送、未创建 PR；原有工作区改动保留。

---

# Task
按范围匹配分块方案运行一轮真实淘宝测试：沿用本批商品22/27并行审核，只向“测试”时间线交付商品27的三个版本，不导出。发生失败后执行 diagnose 并停止。

# Code changes
- `scripts/registered_pipeline.py`：保持不超 120,000 UTF-8 字节的 v3 任务结构和摘要不变，便于复用已有收据；超限商品按窗口和 cue 拆块，每块 64,000 字节以内、保留前后各最多8条重叠 cue，不截断新路径字幕。每块独立缓存，所有块 PASS 后才构建一次边界汇总；汇总和标题/起止原字幕锚点继续通过既有严格验证。单 cue 超限、块数超过48、汇总超过预算均失败关闭。
- `scripts/codex_worker.py`：增加分块审阅 JSON schema 和只引用当前块 cue ID 的边界审阅指令。
- 增加字节预算、cue 完整覆盖/重叠、超大单 cue 拒绝及大小商品混合端到端模拟；`CHANGELOG.md` 记录协议升级。批次清单及范围指标记录每个商品实际协议。

# Runtime results
- 分支 `codex/sentence-boundaries-sync`、HEAD `da7d81f74891`；原有未提交与未跟踪改动保留。没有提交、推送或创建 PR。
- 通过 `project_doctor.py`、Resolve fresh connection doctor 和注册绑定 ID 校验。实时项目 ID 为 `<稳定ID-3>`；“测试”时间线 ID 为 `<稳定ID-2>`，不同于只读源线和正式“成片”线。
- 沿用批次 `20261026_parallel_15553417131581371431`。真实运行复用了已 `READBACK_VERIFIED` 的测试线准备/清理事务；没有新建写入事务，`written_count=0`，没有导出。
- 新分块范围路径完整通过并发布两款商品范围。商品22的旧 v3 任务为72,780字节、1,363条 cue、3个窗口，摘要与先前已封存 PASS 收据完全相同，命中缓存一次，0.015秒完成。商品27原任务196,499字节、3,757条 cue、7个窗口；拆成7块，最大48,572字节，汇总实际28,737字节；7块及汇总共8次 Codex 调用，范围匹配耗时185.791秒，未命中旧块缓存。模拟压力输入估算的汇总体积为61,302字节。
- 商品审核阶段耗时275.715秒；调度器因内存水位将请求的2个商品 worker 降为1，因此本轮实际不是并行商品处理。商品22耗时160.235秒，商品27耗时113.903秒。

| 阶段 | 状态 | 秒 |
|---|---:|---:|
| Resolve 预检 | PASS | 0.495 |
| 测试线准备（复用已验证清理事务） | PASS | 0.511 |
| 标题索引 | PASS | 0.078 |
| 字幕索引 | PASS | 0.101 |
| 商品范围匹配 | PASS | 186.985 |
| 范围发布 | PASS | 0.209 |
| 商品分析与质量审核 | FAILED | 275.715 |
| 调度器总运行墙钟 | FAILED | 466.260 |

- 商品22：选择47.751秒，语义终审19.047秒，人物预筛3.752秒，ASR 88.930秒；本轮状态为 `READY_FOR_MANUAL_CUTPOINT_PREVIEW`，原因 `asr_cutpoint_requires_manual_review:subtitle_boundary_preview`。
- 商品27：选择51.848秒，语义终审12.281秒，人物预筛3.072秒，ASR 36.039秒，物理方案0.811秒，最终人物审核6.258秒、另一次缓存命中0.093秒，最终审核1.193秒；状态 `FINAL_PASS`。
- 两款商品本轮人物抽帧分别9和39帧，共48帧；Codex 拼图0张、Codex 复核0帧。商品指标记载两款本轮返修次数均为0；ASR 转写分别15和6次，ASR 缓存未命中。低层 compute 指标另记商品27缓存命中2次；项目指标中的 cache hit 口径与范围 worker 收据分开记录。
- diagnose 显示商品27的返修累计为2；该值来自项目级 cursor（两条历史 repair history），而本轮盲新鲜批次 cursor 没有 repair history，且本轮指标为0。两种范围的计数不能混为本轮返修数，下一次调度前应先明确需要使用哪个范围的预算。
- diagnose 返回最后失败阶段 `project_analysis_and_quality`，无自动续跑命令；商品22为 `REPAIRABLE`、商品27为 `FINAL_PASS`，两者 `resume_required=false`。测试线没有写入，故切点标记及切点无黑帧本轮未能验收。正式“成片”线上商品34的 `FAILED_VERIFICATION` 事务仍保留；`recover` 暂停未解除，也没有尝试恢复该事务。
- 当前项目任务预算为3次、已用3次（含两次先前中断/失败）；本轮真实运行以返回码2记录为 `FAILED`。没有运行项目 `PLAN.md`，没有登记下一项任务。结束时本任务启动的 Codex/ASR worker 均已退出；Qwen 未加载，MiMo 未调用。

# Validation
- 范围匹配定向模拟：6项通过；项目任务测试25项通过；完整 `project_scheduler` 模拟18项通过；工作流调度器测试通过。
- `project_doctor.py`、Resolve connection doctor、项目与测试时间线稳定 ID 校验：通过。
- 真实测试失败后立即运行淘宝调度器 `diagnose`。按用户限制，本轮之后未修复、未重跑、未换商品。
- 未设置300秒硬门槛。本轮耗时数据显示范围匹配186.985秒为单一最长阶段；商品分析/质量审核275.715秒由语义选择、ASR等组成。下次优化应先查看范围分块的阶段/块级成本及 ASR 阶段计时，再调整性能；不要据此直接加大视觉并发。

# Next step
- 本轮停在商品22的字幕边界人工修边预审前，商品27虽为 `FINAL_PASS` 但未交付。下一步先决定是人工完成商品22预审，还是另行授权仅交付已通过的商品27；当前运行预算已耗尽，不自动续跑。若要再写入商品27，应先明确新的单次授权/预算，并使用调度器的“仅交付已通过商品”模式；完成后再读回核验标记和切点画面。
- 本轮不恢复旧失败事务，不触碰“成片”时间线。

---

# Task
按用户明确的新规则改造 ASR 切点流程：Codex 选段边界为权威；ASR 仅在附近选最近的完整词边界，不做字幕文字对齐，也不作为质量门禁；未命中时保留 Codex 边界并让已通过的商品继续进入时间线，供人工修边。

# Repo changes
- `scripts/registered_pipeline.py`：ASR 窗口中心改为 Codex 选段的首尾秒数，缓存先检查，只有缺失窗口才等待或启动 ASR；按最近完整词首/尾执行切点。不匹配字幕文字。ASR 不可用或附近无词界时发布 Codex 坐标回退记录，并记录窗口数、缓存命中/未命中、回退边界数。
- `scripts/sentence_boundaries.py`、`scripts/final_quality_gate.py`：新增 Codex 边界执行证明。ASR 回退证明可以通过独立的来源帧、音画同步、时长、字幕语义、人物和重叠门禁；不再要求 ASR 词级文本对齐。
- `scripts/project_scheduler.py`、`scripts/workflow_scheduler.py`：移除 ASR 对齐失败时提前结束为待预览及调度器自动转预览的路径；保留语义审核和人物预筛顺序。ASR 回退不触发语义重选。
- `scripts/resolve_write_v2.py`：为每个需人工修边的首尾边界规划红色时间线标记，写入后回读验证标记。蓝色商品起点标记和现有写入回读校验保留。
- `scripts/refine_plans_v2.py`：使物理片段归并兼容新边界证明，并修复归并后字幕摘要回填。
- 更新 `PROJECT_RULES.md`、`CHANGELOG.md` 和本报告；新增/调整边界、调度与标记模拟断言。旧切点计划因契约改变不再复用。

# Runtime changes
- 未启动媒体分析或 ASR/Codex/Vision 模型进程。没有写入、清理或导出 Resolve 时间线；Resolve 只读连接检查已完成。
- 代码现在会将 ASR 回退边界随通过的商品交付，并用红色“待人工修边”标记定位。由于本任务此前唯一真实测试已失败且本轮约束禁止追加第二次真实测试，本轮没有重跑媒体批次；失败回执及检查点均未改写。
- 本轮没有新的真实媒体阶段耗时、抽帧、拼图或返修数据。

# Validation
- 针对性模拟：句界与回退证明 13 项通过；Resolve 标记映射 4 项通过；工作流调度器 15 项通过；单商品 ASR 未命中仍 `FINAL_PASS` 1 项通过。
- 模拟全流程：12 个合成商品、4 个分组的调度链 1 项通过，耗时约 34.7 秒。它不是 Resolve 写入或真实媒体测试。
- 完整 `test_project_scheduler` 套件曾运行后因既有 `project_audit` 的证据遍历耗时超过约 100 秒而主动中断；随后按目标分别重跑单商品失败回退和 12 商品模拟，均通过。没有把中断套件记为通过。
- Python 编译检查、`git diff --check`：通过。绑定工作区 `project_doctor`：通过。Resolve connection doctor：通过；当前实时项目与绑定身份匹配，“测试”目标稳定时间线 ID 校验通过。
- 淘宝 scheduler plan/run、真实媒体测试、写入后回读：本轮未运行。未设置 300 秒门槛。

# External actions
MiMo called: no；Resolve connected: yes（只读 doctor）；export performed: no。Qwen 未加载。Resolve 时间线写入/清理：无。本轮没有启动需要关闭的模型进程。

# Blockers
- 本轮没有重新执行真实测试，因此尚未实际确认两款商品写入和红色人工修边标记回读；上一轮失败状态保持原样。真实重测需在当前单次真实测试上限外获得新的明确授权/预算，并由淘宝调度器重新 plan/run；不能直接重放旧失败事务。
- ASR 在语义审核与人物预筛后执行；物理方案构建和最终人物比例硬门禁仍然在其后，因为它们需审核实际落地的切点。ASR 本身和 ASR 未命中不再阻断。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改：`PROJECT_RULES.md`、`CHANGELOG.md`、`scripts/sentence_boundaries.py`、`scripts/registered_pipeline.py`、`scripts/final_quality_gate.py`、`scripts/project_scheduler.py`、`scripts/refine_plans_v2.py`、`scripts/resolve_write_v2.py`、`scripts/workflow_scheduler.py`、对应四个测试文件及本报告。任务开始前已有的所有 tracked/untracked 改动均保留。工作区修改未暂存；无提交、无推送、无 PR。本节记录的是后续提交之前的工作区快照。

---

# Task
再次随机盲测一款未写入商品；采用 Codex 语义边界与 ASR 邻近词界执行规则，质量通过后写入稳定“测试”时间线，不导出。用户单独授权本批备份并清理测试线。

# Repo changes
- 本轮没有新增公共代码改动。仅更新本报告；本地项目档案登记了一次运行预算的单商品任务和隔离批次。

# Runtime changes
- 使用系统随机数从 12 款未写入、未阻塞且不属于上一测试商品对的候选中抽取 1 款（商品 33）。创建独立批次，预算 1 次运行、每次最多 3 次返修；调度器 `plan` 校验目标为“测试”线。
- 测试线执行前只读检查为 0 个片段、0 个标记。用户授权后完成备份与清理事务，回读通过；移除片段 0、标记 0。
- 运行止于商品范围匹配，耗时 21.323 秒（阶段指标 20.135 秒）；调度器整轮墙钟约 25.514 秒。范围请求包含 2,111 条字幕 cue、4 个窗口、109,351 字节，发生 1 次 Codex 范围匹配调用。未记录可核验的范围缓存命中数。
- 字幕索引生成或校验后，范围匹配发现首次讲解跨到下一段字幕文件但缺少后续边界证据；后段返场不能代替首次讲解的完整结束范围。未进入选段、语义终审、人物检查、ASR 或物理方案；抽帧 0、拼图 0、返修 0、ASR 窗口 0。没有向时间线追加商品片段。

# Validation
- `project_doctor`：通过；Resolve connection doctor 与当前项目/“测试”时间线身份校验：通过。
- 淘宝调度器 `plan`：通过，商品 33、盲新鲜模式、目标“测试”线；`run`：在 `batch_semantic_range_matching` 失败。随后执行 `diagnose`：成功，未提供续跑命令；诊断要求保留有效上游并修复范围匹配问题。单次运行预算已用尽，本轮未重试或改抽商品。
- 测试线清理回读：通过，移除 0 个片段、0 个标记。写入后成片回读未运行，因为没有进入交付阶段。
- 失败后只读回读“测试”线：通过，仍为 0 个片段、0 个标记。先前已记录的 ASR 定向模拟结果不代表本次真实运行验证了 ASR 行为。
- 本轮未运行合成测试；最终 `git diff --check`：通过。

# External actions
MiMo called: no；Resolve connected: yes；export performed: no。Qwen 未加载。Codex 范围匹配调用 1 次；未启动 ASR 或视觉模型；结束检查未发现本任务遗留的模型进程。

# Blockers
- 商品 33 在范围匹配阶段因首次讲解的字幕结束边界证据不足而阻塞；商品未写入时间线，因此本次不能验证 ASR 邻近词界执行或红色人工修边标记。任务运行状态记录为 `INTERRUPTED`，预算 1/1 已消耗。
- 既有正式“成片”线失败事务的 `recover` 暂停保持不变。下一步先为范围匹配提供完整的跨段边界证据；重新试跑需要单独登记新任务和预算，不能重放本批或换商品规避本次限制。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。所有任务前已有 tracked/untracked 工作区改动保留；本轮只修改本报告。无暂存文件；未提交、未推送、未创建 PR。本节是后续提交前的快照。

---

# Task
解释随机盲测商品 33 的范围匹配失败、`diagnose` 的职责及其与 Resolve 已有商品 33 三个成品的关系；参考 GitHub 上成熟的长任务故障恢复设计。

# Repo changes
- 本轮没有代码修改；只读核对运行记录、项目商品检查点、历史 Resolve 预审事务和实时时间线，并更新本报告。

# Runtime changes
- 实时 Resolve 只读快照：正式“成片”线有 197 个视频、197 个音频片段和 43 个标记；“测试”线仍为空。
- 项目记录中存在一笔较早的商品 33 预审事务，目标为正式“成片”线，回读状态 `VERIFIED`，包含 3 个预审版本并标记需要人工修边。这不是本次新随机批次产生的交付；`VERIFIED` 证明当时写入回读成功，不等同于最终质量验收通过。
- 本次新随机批次停在 `batch_semantic_range_matching`：4 个上下文窗口、2,111 条 cue、109,351 字节，低于 120,000 字节上限；错误原因是首次讲解的完整结束边界证据缺失。阶段事件状态为 `FAILED`，耗时 21.323 秒；未进入 ASR、人物审查或写入。
- `diagnose` 是只读诊断摘要，不调用模型、不读取新上下文、不修复或重跑。它组合最近阶段事件、项目级商品检查点和任务限制。当前显示的 `checkpoint_context_stale` 来源于项目级历史检查点，并不代表本次 blind-fresh 批次已运行到该商品处理阶段。

# Validation
- 本轮只读核验实时时间线和历史预审事务；没有运行项目 doctor、合成测试、调度器 `plan/run` 或写入操作。
- 代码核查确认：`workflow_scheduler.run` 当前用宽泛的 `BaseException` 捕获所有异常并记为 `INTERRUPTED`；因此范围匹配阶段已记 `FAILED`，任务运行账却显示 `INTERRUPTED`。`diagnose` 没有针对 `project_range_matching_failed_product_*` 的修复方案；任务存在阻塞时还会清除通用续跑命令。
- 本次范围请求字节数没有超过既有上限；故障属于证据范围不足，不是 120 KB 限制，也不是 ASR 对齐失败。
- GitHub 参考：LangGraph 检查点与 pending writes 用于从失败节点续跑并保留已完成工作（[checkpoint README](https://github.com/langchain-ai/langgraph/blob/main/libs/checkpoint/README.md)、[durable execution](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/durable-execution.mdx)）；LangGraph 按节点设置有限重试，并在重试耗尽后运行错误处理器（[fault tolerance](https://github.com/langchain-ai/docs/blob/main/src/oss/langgraph/fault-tolerance.mdx)）；Temporal 区分固定次数重试、不可重试错误和等待人工修正后恢复（[error-handling patterns](https://github.com/temporalio/documentation/blob/main/docs/design-patterns/error-handling-patterns.mdx)、[recover without restart](https://github.com/temporalio/documentation/blob/main/docs/guides/recover-without-restart.mdx)）；Prefect 将输入/代码身份用于缓存，并支持按失败条件决定是否重试（[caching](https://github.com/PrefectHQ/prefect/blob/main/docs/v3/concepts/caching.mdx)、[tasks](https://github.com/PrefectHQ/prefect/blob/main/docs/v3/concepts/tasks.mdx)）。

# External actions
MiMo called: no；Resolve connected: yes（只读快照）；export performed: no。Qwen 未加载。通过 GitHub 搜索并阅读公开项目文档；没有向第三方发送项目内容。

# Blockers
- 调度器当前主要保存失败证据和通用回滚点，没有按错误类别提供下一步操作；任务状态还把普通阶段异常误标为 `INTERRUPTED`。
- 建议采用轻量的“分类—有界修复—等待人工—按有效检查点续跑”状态机，而不是直接引入需要独立服务的 Temporal：瞬时错误限定次数并退避；边界证据不足只扩展一次相邻字幕索引上下文，仍不足则进入 `WAITING_FOR_EVIDENCE` 并给出具体所需材料；缓存身份失效只作依赖树局部重算；Resolve 写入不确定继续只读核验、禁止重放。每张修复卡绑定失败阶段、输入摘要、保留/失效产物、最多自动修复次数和明确的用户动作；相同阶段与相同输入摘要不能通过新批次无限重试。
- 当前真实测试运行预算 1/1 已用完；本报告仅给出改进设计，没有重跑商品或改写旧事务。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。仅本报告有本轮新增改动；原有工作区变更保留。无暂存文件；未提交、未推送、未创建 PR。本节是后续提交前的快照。

---

# Task
按上轮建议改进长任务失败处置：修正普通失败/中断状态、让 `diagnose` 给出边界缺证动作，并为商品范围匹配增加一次有界的相邻字幕上下文补证。

# Repo changes
- `scripts/workflow_scheduler.py`：普通异常现在将任务运行记为 `FAILED`，键盘中断/系统退出记为 `INTERRUPTED`；阶段事件同样区分失败与中断。范围匹配耗尽唯一一次补证后，`diagnose` 返回 `WAITING_FOR_EVIDENCE` 修复卡，指明商品、应补的证据、保留的上游与失效的下游，并禁止同输入盲目续跑。
- `scripts/registered_pipeline.py`：首次范围审核明确返回 `REPAIRABLE` 且缺少完整边界时，最多追加一次请求，补入候选边缘前后 30 秒的原始字幕 cue；触及素材段首尾时，最多再纳入一个相邻素材段对应端点的 30 秒。原文和源时间不截断、不推测。补证请求沿用 worker 回执缓存并受现有 120 KB 请求上限约束；没有安全上下文或超出上限时停止并等待补证。阶段 metrics 记录补证次数、cue/窗口数、字节数、缓存与耗时；范围清单记录补证协议版本。
- 增加合成回归：相邻素材段证据可完成一次范围判断；无法确认时最多只尝试一次；诊断不给泛化重跑命令；普通异常和人工中断状态保持可区分。其他既有工作区变更均保留。

# Runtime changes
- 未运行淘宝真实 `plan/run`、商品测试或 Resolve doctor；没有访问或修改 Resolve 时间线、恢复旧事务、启动模型或清理模型进程。上轮失败批次、旧失败状态和测试线内容未变。

# Validation
- `tests.test_registered_range_matching_chunks`、`tests.test_registered_range_windows`、`tests.test_workflow_scheduler`：28 项通过。
- 本轮涉及脚本与测试文件 `py_compile`：通过；`git diff --check`：通过。
- 没有运行整仓库测试，也没有用真实素材验证补证质量或节时效果。

# External actions
MiMo：未调用；Qwen：未加载；Resolve：未连接或操作；export performed: no。未提交、推送或创建 PR。

# Blockers
- 如果一次补证仍不能证明完整范围，系统会停止等待相邻字幕索引或人工确认的结束锚点；不会自动启动新批次或重复调用。相同补证任务通过稳定 task digest 复用已封存回执。
- 本轮只验证代码路径的模拟行为，尚未通过真实商品运行确认“商品 33”问题会被补证解决；当前真实测试预算不在本轮续用。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改 `scripts/registered_pipeline.py`、`scripts/workflow_scheduler.py`、`tests/test_registered_range_matching_chunks.py`、`tests/test_workflow_scheduler.py` 和本报告；其余已有改动全部保留。无暂存、未提交、未推送、未创建 PR。

---

# Task
按用户要求再次定向测试商品 33，验证一次性相邻字幕边界补证，并在质量门禁通过后写入“测试”时间线；不导出。

# Repo changes
- 本轮未改产品代码。为本次单次回归登记独立项目任务与隔离批次，运行预算 1 次、单次返修上限 1；旧批次和旧失败事务未改写。
- 仅追加本报告；项目任务与批次记录留在忽略的本机项目工作区。

# Runtime changes
- 实时 Resolve 项目身份与绑定工作区匹配；“测试”目标稳定 ID 校验通过。运行前只读快照为 0 个视频片段、0 个音频片段、0 个标记。
- 淘宝调度器 `plan` 通过。唯一一次 `run` 在 `resolve_delivery:test_timeline_prepare` 失败，错误为 `resolve_timeline_changed_before_reusable_test_clear`；未开始字幕索引或范围匹配，未进入选择、ASR、人物审核、质量门禁或追加写入。
- 准备阶段已生成有效 Resolve 项目备份并写入 `CLEAR_IN_PROGRESS`。异常发生在 `DeleteClips` 之前。失败后只读核对测试线仍为 0/0/0，且目标快照与事务中的准备前快照一致；Resolve 当前焦点时间线切换为“测试”。
- 项目任务将本次记为 `FAILED`，预算 1/1 已用。没有启动本任务模型进程；MiMo 未调用，Qwen 未加载。

# Validation
- 项目 doctor：通过。Resolve connection doctor：通过，项目身份与“测试”目标稳定 ID 匹配。
- 淘宝调度器 `plan`：通过；唯一一次 `run`：失败于测试线准备阶段。随后 `diagnose` 指向“复用测试时间线清理事务只读诊断”，未给续跑命令。
- 失败后只读核验：备份包有效；目标线与准备前快照一致，未删除片段或标记。`project_task refresh`：通过。
- 本轮没有运行商品处理或合成测试；最终 `git diff --check`：通过。

# External actions
MiMo called: no。Resolve connected: yes。export performed: no。未执行视频导出；未恢复或重放任何事务；未提交、推送或创建 PR。

# Blockers
- 新批次的准备事务仍为 `CLEAR_IN_PROGRESS`，项目任务状态为 `FAILED`，单次预算已耗尽。虽然目标线内容与准备前一致，尚未完成事务核验/关闭；禁止再次运行、清理或自行改写事务状态。
- 本次没有触达范围匹配的新逻辑，因此不能判断商品 33 的相邻字幕补证是否会通过。下一步应先针对准备阶段的时间线快照一致性失败做单点诊断；取得新的运行预算后才可再次测试。正式“成片”线上原 `FAILED_VERIFICATION` 事务及 `recover` 暂停仍保持原样。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改仅为本报告；代码与测试文件的既有工作区改动全部保留。无暂存、未提交、未推送、未创建 PR。

---

# Task
解释商品 33 重测前测试线准备为何检查其他 Resolve 时间线，并核对状态文档与实时快照的职责。

# Repo changes
- 未改产品代码；仅更新本报告。
- 源码核对确认：测试线 `prepare` 在清理前对所有 Resolve 时间线做精确实时快照比较；正式追加写入也会保存全时间线写前快照，并在追加后核验非目标时间线未变、目标时间线原有内容仍保留。

# Runtime changes
- 未连接或操作 Resolve，未访问字幕、媒体或模型。上次商品 33 重测现场保持不变：测试准备事务仍为 `CLEAR_IN_PROGRESS`，测试线内容与其准备前快照一致。
- `PROJECT_STATE.json` 与 `PROJECT_TASKS/STATUS.md` 记录项目身份、目标 ID、任务/事务与派生进度，不保存可代替 Resolve 实时内容的全时间线快照；身份和目标用状态记录校验，片段/标记变化仍需实时 Resolve API 核验。

# Validation
- 只读检查 `reusable_test_delivery.py`、`resolve_write_v2.py` 和 `coldstart_resolve.py` 的快照/写入顺序。
- 未运行 doctor、调度器、测试或 `git diff --check`；本轮没有产生运行数据。

# External actions
MiMo called: no。Resolve connected: no。export performed: no。未提交、推送或创建 PR。

# Blockers
- 测试线准备会先快照全部时间线，再执行 Save/Export、激活测试线，并要求全量快照完全相等；它旨在发现清理前的并发/意外改动，但目标为空时仍执行了这套清理准备，范围偏宽。
- 本次失败时只把目标时间线的 `before` 快照写入事务，失败事件没有保存全量差异；虽然已证实目标未变，无法从持久证据判断是非目标时间线真实变化还是 Resolve 快照字段不稳定。
- 源码建议：空目标跳过清理事务，只做目标 ID/空状态预检；非空目标须有明确清理授权，并把逐时间线差异和阶段状态记入事务。真正追加时继续保留目标及受保护时间线的写前/写后快照校验。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改仅为本报告；其他工作区改动保留。无暂存、未提交、未推送、未创建 PR。

---

# Task
修正空“测试”时间线的准备门禁，并按用户要求再次定向测试商品 33；通过质量门禁后写入测试线，不导出。真实运行限定 1 次，失败后 diagnose 并停止。

# Repo changes
- `scripts/reusable_test_delivery.py`：目标稳定 ID 确认后先只读检查目标自身。目标为空时生成 `ALREADY_EMPTY_VERIFIED` 回执，不保存/导出项目、不切换时间线、不执行清理，也不拿其他时间线快照阻挡此空线场景。增加 `verify-no-mutation`，仅在实时目标快照与原准备快照相同、目标仍为空且备份有效时，登记中断准备事务的无改动证据。
- `scripts/project_task.py`：新增由事务哈希和只读证明绑定的 `no_mutation_verified` 结案记录；原 `CLEAR_IN_PROGRESS` 文件状态不改写。项目任务只解除这笔经证实未改动的测试线阻塞，正式时间线失败事务仍是阻塞项。
- `scripts/workflow_scheduler.py`：只有真实清理回执 `READBACK_VERIFIED` 才能 supersede 同目标的旧失败记录；空线只读回执不会清理或 supersede 历史。
- `tests/test_reusable_test_delivery.py`、`tests/test_project_task.py`：增加空线不触发 Resolve 写入的模拟，以及无改动事务保留原状态、正式失败仍阻塞的状态机模拟；更新普通异常应记为 `FAILED` 的旧断言。

# Runtime changes
- 上轮准备事务经实时项目/目标稳定 ID、目标快照完全相同、0 片段/0 标记及有效备份验证，记录为 `no_mutation_verified`；原事务仍为 `CLEAR_IN_PROGRESS`。旧正式线 `FAILED_VERIFICATION` 与 `recover` 暂停均保留。
- 新建批次 `20261026_p33_boundary_retest_02`，商品 33，预算 1 次运行、单次返修上限 1。淘宝 `plan` 通过。唯一一次真实 `run` 在 `batch_semantic_range_matching` 失败，未进入选择、ASR、视觉审核、最终质量门禁或 Resolve 追加。
- 失败后实时只读回读仍为测试线 0 视频、0 音频、0 标记；没有导出。任务记录为 `FAILED`，运行预算 1/1 已用。

# Validation
- 针对性模拟：测试线准备 4 项、项目任务状态机 26 项、调度器模拟 20 项、范围匹配模拟 3 项，共 53 项通过；涉及脚本与测试文件 `py_compile`、`git diff --check` 通过。
- 项目 doctor、Resolve connection doctor 通过。实时 Resolve 项目 ID、素材线、正式线及“测试”线稳定 ID 与绑定配置匹配。
- 淘宝调度器 `plan` 通过；唯一真实 `run` 失败后已调用 `diagnose`。诊断回滚阶段为范围匹配，`resume_command=null`、`same_input_retry=false`。
- 本次调度器阶段耗时：Resolve 预检 0.644 秒；空线准备 0.138 秒；标题索引 0.082 秒；字幕索引 0.100 秒；范围匹配 1.238 秒。任务总耗时 2.355 秒。
- 范围匹配指标：4 个窗口、2,111 条 cue、请求 109,351 字节；初始 Codex 回执缓存命中（当前读取约 0.008 秒，回执记录的原始耗时约 20.106 秒）。回执 verdict 为 `BLOCKED` 且没有范围决定，因此现有一次性上下文补证条件（只接受 `REPAIRABLE`）没有触发，补证次数 0；错误指出现有字幕窗口缺少 451–480 秒的边界上下文。未进入视觉阶段，帧抽取、拼图和返修均为 0。
- 本次没有启动新的语义 worker 请求或本地 ASR/视觉模型进程；MiMo 未调用，Qwen 未加载。没有向外部语义服务发送项目内容。

# External actions
Resolve connected: yes（doctor 与只读回读）；时间线写入：no；export performed: no。未恢复或重放事务；未提交、推送或创建 PR。

# Blockers
- 本轮修复解决了空测试线被清理前全时间线快照比较拦截的问题，但真实测试未能验证剪辑交付，因为范围匹配拿到缓存的 `BLOCKED` 回执后直接失败，没有调用一次性边界扩展。
- 下一步应只针对“无范围决定且明确缺少边界上下文”的 `BLOCKED` 回执增加有界补证分支，保持单次扩展上限，并为缓存命中场景补模拟；本轮真实运行预算已耗尽，不再修复或重跑。正式失败事务和恢复暂停保持原状。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接改动：`scripts/reusable_test_delivery.py`、`scripts/project_task.py`、`scripts/workflow_scheduler.py`、`tests/test_reusable_test_delivery.py`、`tests/test_project_task.py` 和本报告。其他既有工作区变更保留；无暂存、未提交、未推送、未创建 PR。

---

# Task
继续优化上轮商品 33 范围匹配失败后的边界上下文补证触发条件；本轮只改代码并做模拟，不启动新的真实商品运行。

# Repo changes
- `scripts/registered_pipeline.py`：保留 `REPAIRABLE` 的既有一次性补证；新增窄条件：回执为 `BLOCKED`、没有 `range_decision`，且同一条 issue 明确同时包含“缺少/不足”与“边界上下文/结束边界/相邻字幕”等线索时，才请求一次 30 秒相邻上下文。无关的 `BLOCKED`（例如商品归属冲突）仍直接停止。补证仍必须返回 `PASS` 和有效范围锚点；补证不足则进入 `WAITING_FOR_EVIDENCE`，不再追加请求。边界补证协议升为 `codex-adjacent-boundary-context-once-v2-blocked-missing-context`，补证请求的 task digest 随协议更新。
- `tests/test_registered_range_matching_chunks.py`：把缓存命中的 `BLOCKED` 缺边界回执纳入集成模拟，验证只调用一次补证并接受有效结果；增加无关 `BLOCKED` 不触发补证的分类检查。

# Runtime changes
- 未运行淘宝调度器 `run`，未分析真实媒体/字幕，未操作时间线，未导出；上轮商品 33 任务的 1/1 运行预算和失败状态保持不变。`recover` 暂停与正式线失败事务未变。
- `project_task refresh` 已按启动规程刷新状态交接；没有新增项目任务或改动商品检查点。

# Validation
- `test_registered_range_matching_chunks.py`：4 项通过；`test_registered_range_windows.py`：5 项通过。
- `registered_pipeline.py` 与测试文件 `py_compile` 通过；`git diff --check` 通过。
- 没有调用 Codex worker、ASR 或视觉模型；MiMo 未调用，Qwen 未加载。

# External actions
Resolve 仅由 `project_task refresh` 的身份 doctor 做只读连接；没有时间线写入或导出。未提交、推送或创建 PR。

# Blockers
- 本轮仅验证模拟逻辑，尚未通过真实商品运行确认这条补证分支能为商品 33 找到完整结束锚点。上轮真实运行预算已耗尽；本轮未创建新批次或绕过该预算。
- 后续若要做真实验收，应使用单独授权的新运行预算，并经淘宝调度器 `plan/run`；补证仍最多一次。其他 `BLOCKED` 原因继续保持硬停止。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改 `scripts/registered_pipeline.py`、`tests/test_registered_range_matching_chunks.py` 和本报告；既有工作区改动全部保留。未暂存、未提交、未推送、未创建 PR。


---

# Task
按用户要求再次测试商品 33；淘宝调度器驱动，最多一次真实运行，失败后诊断并停止，不导出。

# Repo changes
- 本轮没有修改产品代码；仅追加本报告。

# Runtime changes
- 新批次 `20261026_p33_boundary_retest_03`，商品 33，运行预算 1 次、相邻上下文补证上限 1 次。淘宝调度器 `plan` 通过。
- Resolve 项目身份与目标时间线预检通过；“测试”线准备检查通过，写入前为空。本次失败发生在范围匹配，未到最终质量门禁或 Resolve 追加；没有清理、追加或导出，也未重试。
- 唯一一次 `run` 在 `batch_semantic_range_matching` 失败：原缓存回执命中后，新增的一次相邻字幕补证确实触发，但仍返回 `INSUFFICIENT_EVIDENCE`。范围匹配等待补齐首次讲解结束处及相邻素材段字幕索引，或人工确认完整结束锚点。
- 失败后已执行 `diagnose`。总体修复计划为 `WAITING_FOR_EVIDENCE`，同输入不自动重试；诊断总体 `resume_required=true`，但商品 33 检查点同时显示 `BLOCKED / checkpoint_context_stale / resume_required=false`。该状态不一致已保留，不能据此恢复。
- 正式时间线原 `FAILED_VERIFICATION` 与 `recover` 暂停保持不变；未处理或改写任何既有失败事务。项目任务交接已刷新。

# Validation
- 调度阶段：Resolve 预检 0.505 秒；测试线空状态准备 0.137 秒；标题索引 0.079 秒；字幕索引 0.094 秒；范围匹配 50.653 秒。失败前已记录阶段合计约 51.5 秒；范围匹配指标总计 49.503 秒。
- 范围匹配：初始查询 4 个窗口、2,111 条 cue、109,351 字节；缓存命中，读取耗时 0.008 秒（原 worker 记录耗时 20.106 秒）。一次补证扩为 8 个窗口、127 条 cue、118,371 字节；缓存未命中，耗时 49.475 秒（worker 记录 26.008 秒），结果证据不足。补证次数 1/1，后续不得用同输入自动重试。
- 未进入 ASR、人物/视觉审核、抽帧或拼图阶段；这些阶段本轮均为 0。没有修边返修。检查未发现本轮遗留的 ASR/视觉模型进程；没有加载 Qwen，也未调用 MiMo。
- 项目 doctor、Resolve connection doctor 和实时稳定 ID 校验在本批运行前已通过；本次 `run` 自身的 Resolve 预检也通过。

# External actions
Resolve connected: yes（仅预检及空线准备检查）；Resolve timeline write: no；export performed: no。未提交、推送或创建 PR。

# Blockers
- 这次验证确认新增分支会对“BLOCKED 且缺失边界证据”的缓存回执执行一次有界补证，但商品 33 的现有字幕索引仍不足以给出完整结束锚点，因此未产生成片。
- 下一步先补齐该结束锚点证据，或由用户人工确认边界；同时核查 `diagnose` 总体 `resume_required` 与商品检查点相反的状态映射。取得新的测试预算前，不续跑本批，不清理测试线，不改写失败状态。旧正式线失败事务和 `recover` 暂停继续保留。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮直接修改仅为 `tmp/review_reports/LATEST_CODEX_REPORT.md`；其余既有工作区改动全部保留。未暂存、未提交、未推送、未创建 PR。


---

# Task
按用户明确的语义目标修正商品范围门禁：先理解当前商品卖点，再从相邻商品已知起止字幕线索推断搜索范围；整体精确起止不阻断三条成片候选。

# Repo changes
- `scripts/registered_pipeline.py`：范围任务改为卖点搜索包络协议。将最近且有独特标题证据的前商品结束 cue、后商品开始 cue 一并传给 Codex；一次上下文补证后，如果失败原因只是整商品边界不完整，且当前商品仍有可信标题/卖点窗口，就建立带 `neighbor_inferred_search_envelope` 标记的候选窗口并继续。边界用前商品结束与当前商品标题证据、当前商品末个特征 cue 与后商品开始之间的中点估算；候选窗端点仍绑定原字幕 cue。显式商品身份冲突、无当前商品证据或窗口无法建立时仍停止。
- `scripts/publish_confirmed_ranges.py`：允许推断搜索包络由多个互不连续的字幕窗口组成；校验每个窗口的来源、cue 锚点和跨商品重叠，不再把整个外包络当作实际授权素材范围。
- `scripts/build_expanded_codex_request.py` 与 `scripts/registered_pipeline.py`：把范围模式、相邻锚点和边界不确定性传给候选/语义选择；三条卖点目标不会因整体起点或终点不确定而减少。搜索窗口不会被记成完整商品范围扫描或完整候选池耗尽。
- `scripts/codex_worker.py`、`scripts/workflow_scheduler.py`：更新边界审阅指导和失败后的下一步，要求参考前后商品推断；整体终点缺失不再被当作唯一补证行动。
- `PROJECT_RULES.md`、`CHANGELOG.md`：记录新的范围与切点职责。ASR 仍只执行已选片段附近的切点；无法对齐时保留 Codex 边界并按既有规则留待人工修边。
- 新增/更新合成回归，覆盖一次补证后仍无精确整体终点、相邻商品边界推断、真实范围证明发布、候选窗跨商品重叠门禁以及三条目标提示。

# Runtime changes
- 没有重新运行商品 33 的真实任务，没有操作或写入 Resolve；此前失败批次及 `FAILED_VERIFICATION` / `recover` 暂停均未改写。
- 没有启动 ASR、视觉模型、Qwen 或 MiMo。
- 本轮针对代码验证的 16 项合成测试全部通过，Python 编译与 `git diff --check` 通过。
- 另外尝试的边界安全大组合测试在进入本次范围校验前被既有测试数据拒于 `subtitle_generator_provenance_invalid`；完整调度器测试包含 Resolve 连接预检并报告 `resolve_connection_doctor_failed`。这两项未执行时间线写入；因此没有把它们记为通过，也没有继续运行带 Resolve 副作用的组合测试。

# Validation
- 通过：`test_registered_range_matching_chunks`、`test_registered_range_windows`、`test_neighbor_inferred_ranges`，以及单独的调度器边界失败行动卡模拟，共 16 项。
- 通过：受影响 Python 文件编译、`git diff --check`。
- 未单独调用 doctor 或真实调度 `run`。一次额外的大组合调度器测试触发了只读 Resolve preflight，并返回 `resolve_connection_doctor_failed`；没有进入时间线写入。没有运行全量剪辑/视觉流程。

# External actions
MiMo called: no；Qwen loaded: no。Resolve timeline write: no；export performed: no。未提交、推送或创建 PR。

# Blockers
- 商品 33 的新逻辑尚未由新的真实调度批次验证；此前商品 33 失败批次保持失败且不可续跑。下一步应以新批次和新单次预算，通过淘宝调度器 `plan/run` 验证能否依据卖点窗口产出并写入三条；失败后只 `diagnose`，不在同一任务内重复运行。
- 正式时间线既有失败事务与 `recover` 用户暂停继续保留。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮逻辑相关文件：`scripts/registered_pipeline.py`、`scripts/publish_confirmed_ranges.py`、`scripts/build_expanded_codex_request.py`、`scripts/codex_worker.py`、`scripts/workflow_scheduler.py`、`PROJECT_RULES.md`、`CHANGELOG.md` 和范围匹配模拟测试。此前已有未提交改动均保留；未暂存、未提交、未推送、未创建 PR。


---

# Task
开始下一轮随机盲测；一件商品、最多一次调度运行、返修上限一次；只追加到 Resolve「测试」时间线，不清空、不导出。

# Runtime changes
- 绑定项目稳定 ID `<稳定ID-3>` 与工作区匹配。随机抽样来自登记标题索引的 43 个商品编号；排除此前盲测样本后，从 36 个编号中抽中商品 39（抽样种子 `<数值已脱敏>`）。不读取商品标题或旧切点作为抽样依据；本批以 `--blind-fresh` 重新选择。
- 新任务 `T20261026_RANDOM_BLIND_03`、批次 `20261026_random_blind_03` 已登记，`max_runs=1`、`max_repairs_per_run=1`。淘宝调度器 `plan` 通过，目标为稳定 ID `<稳定ID-2>` 的「测试」时间线，交付适配器报告就绪。
- 运行前测试线为 0 视频、0 音频、0 标记。调度器的准备步骤返回 `ALREADY_EMPTY_VERIFIED` 且 `mutation_performed=false`，没有清理。
- 唯一一次 `run` 完成到质量阶段后失败：商品 39 为 `REPAIRABLE`，原因 `CUTPOINT_PROOF_MISSING_OR_STALE` 与 `SOURCE_OVERLAP`。最终质量门禁没有通过，因此未进入 Resolve 交付写入。失败后按约定只运行一次 `diagnose`，没有续跑、修复或换商品。
- 失败后只读复核「测试」线仍为 0 视频、0 音频、0 标记；未写入、未导出。

# Validation and timing
- 项目 doctor 通过；Resolve connection doctor 通过。实时项目 ID、淘宝模式、目标名称及稳定时间线 ID 均匹配。
- 调度器记录阶段耗时：Resolve 预检 `0.667s`；测试线空状态检查 `0.145s`；标题索引 `0.801s`；字幕索引 `0.893s`；语义范围匹配 `35.675s`；范围发布 `0.176s`；项目分析与质量审核 `117.982s`。阶段合计 `156.339s`；从 run 开始到完成约 `156.547s`。没有 Resolve 写入阶段耗时。
- 商品内部耗时：语义选择 `41.803s`；语义终审 `14.638s`；人物预筛 `3.082s`；ASR 切点执行 `50.184s`（8 次窗口转写，转写累计 `42.017s`）；物理方案 `0.853s`；最终人物审核 `6.046s`。范围匹配查询 4 个窗口、695 条 cue，缓存未命中，耗时约 `34.5s`。
- 人物审核抽帧 39 张；Codex 拼图 0 张、复核 0 帧；Qwen fallback 调用 0。ASR 与主要审核阶段均未复用阶段缓存；底层缓存记录 98 次查找、9 次命中、81 次 `identity_not_found`。调度指标记录 `repair_attempts=1`，而 diagnose 的商品项报告 `repair_attempts=0`，两者计数口径不一致，需后续核对。
- diagnose 指向 `project_analysis_and_quality` 失败，返回 `resume_command=null`、`automatic_resume=false`；候选、rough、ASR、plan、vision、final audit 被列为可复用阶段。当前任务单次运行预算已耗尽，不应续跑。
- 当前项目历史记录此前将商品 39 标为 `WRITTEN`，但其证据未重新验证；本批只作盲选重跑，失败后项目检查点反映本批 `REPAIRABLE`。这不改变任何 Resolve 时间线事务。

# External actions
Resolve connected: yes。测试线写入/清理：no。Export: no。MiMo: no；Qwen fallback/load: no。运行结束后本任务启动的 ASR worker 已退出；系统中仍运行的 Ollama 服务进程早于本轮启动，未由本任务管理或终止。未提交、推送或创建 PR。

# Blockers and next step
下一步先针对商品 39 检查切点证明与源区间重叠校验之间的证据传递，尤其是证明缺失/过期原因及重叠区间来源；同时统一 diagnose 与 metrics 的返修计数口径。按当前预算不续跑、不切换商品；后续真实测试需先完成该定向修复并登记新的单次预算。此前商品 34 正式时间线失败事务及 `recover` 暂停保持原样。

# Git state
分支 `codex/sentence-boundaries-sync`，HEAD `da7d81f74891`。本轮仓库内直接改动为本报告追加；项目任务、批次与运行指标保存在该 Resolve 项目的绑定工作区。未暂存、未提交、未推送，未创建 PR。


# Follow-up diagnosis: actual blockers and bounded solution (read-only)

## Current block points
- Product 39 passed registration, Resolve preflight, title/subtitle indexing, semantic range matching/publication, semantic selection/review, person prescreen, ASR, physical plan, and final person audit. It stopped at `final_quality_gate`; no Resolve append was attempted.
- `CUTPOINT_PROOF_MISSING_OR_STALE` is an artifact consistency failure in this run, not an ASR PASS requirement: `records/cursors/39.json` points to plan `93d6…`, whose five clips have no accepted cutpoint proof. `records/checkpoints.json` points to a different plan `5102…`; three of its four clips pass `verify_cutpoint_proof`, one fails.
- `SOURCE_OVERLAP` is reproducible in the checkpoint plan: variant 1 contains the same `541-570.mp4` interval twice (`9740–10417` source frames), a 677-frame duplicate. The remaining displayed checkpoint-plan intervals do not overlap. The hard gate correctly catches the duplicated physical clip; the planner should normalize/repair it before final audit.
- `project_scheduler.py` increments the repair counter and then stops when `repair_rounds >= repair_limit` or the same-failure count reaches the limit. With `repair_limit=1`, the initial gate failure reaches that condition before a repair worker call. Thus the configured one repair currently allows zero actual repair calls.
- Diagnose reads `cursor.get('repair_attempts', 0)` while the persisted cursor has `repair_attempts_by_key` and no `repair_attempts`; runtime metrics recorded one. It consequently reports 0 and cannot give a precise repair handoff. Cursor and checkpoint plan references also disagree, so no safe rollback point should be inferred until the references are reconciled.
- Separate project blocker: an older product 34 `FAILED_VERIFICATION` transaction on the formal `成片` timeline remains under the user’s `recover` hold. There are no pending unverified transactions on the `测试` timeline, so that older transaction did not cause product 39’s quality failure.

## Bounded fix path
1. Change repair accounting so `max_repairs_per_run=1` permits one real targeted repair call followed by a hard-gate recheck; stop only if that repaired attempt still fails. Keep the one-run task budget and do not start a second scheduler run.
2. Make a single plan digest authoritative across cursor, checkpoint, final audit, and diagnose. On mismatch, report `plan_reference_mismatch` and rebuild only from the last verified upstream stage; never silently combine references.
3. Normalize physical clips before the hard gate: collapse exact duplicates within a variant while retaining semantic-role audit metadata; for genuine cross-variant overlap, ask the bounded repair step for non-overlapping alternatives. Keep overlap as a hard rule.
4. Require a valid cutpoint provenance record for each physical clip, including `CODEX_FALLBACK` with manual review pending when ASR cannot align. This preserves semantic selection and enables the existing red manual-review delivery path without treating ASR alignment as a gate.
5. Add synthetic regressions for one permitted repair call, duplicate-clip normalization, fallback proof acceptance, plan-reference mismatch, and diagnose reading the persisted repair schema. Only after those pass should a separately budgeted real test be considered.

No code or Resolve changes were made during this follow-up; no run was repeated. Current task `max_runs=1` is exhausted. Next work should implement the bounded scheduler/plan fixes and rerun only simulations; it must not claim real-world verification until a new authorized real run passes.


---

# 2026-10-26 — 商品 39 重复范围根因与语义返修修正

## 根因
- 商品 39 在语义选段阶段产生的源区间本来互不重叠；重复片段是在 ASR 边界执行后才出现，所以最初语义审核无法预见。
- ASR 回执原先用 `candidate_id + variant_id + edge` 做索引。同一候选、同一版本如果包含多个不相邻选段，它们的 opening/ending 键相同；后一个选段的 ASR 边界覆盖前一个，物理方案遂将后一个源区间再次写入前一个位置。最终硬门禁正确发现了商品 39 的 `541-570.mp4` 源帧 `9740–10417` 重复，共 677 帧。
- 此前报告中把项目级旧 cursor 与本批 checkpoint 的计划引用不一致当作当前批问题，这个判断不正确。`blind_fresh` 的有效 cursor 在批次缓存下，和当前 checkpoint 指向同一计划；出错的是 `diagnose` 读取了项目级旧 cursor。

## 代码修正
- ASR 的每条边现在绑定具体选段出现项，身份包含选段序号和范围摘要；物理方案按该身份取边，不会让同候选同版本的不同范围互相覆盖。提升 ASR、切点及最终审核协议版本，使旧派生产物不能冒充新身份。
- 物理方案生成后立刻执行范围/切点硬门禁，早于最终人物抽帧。发现重叠时报告双方版本、片段、源文件、帧区间和秒区间，转回 Codex 重新理解与选段，并把交集作为硬排除区间；本地还会拒绝违反排除区间的回执。初选发现重叠时也提供具体交集。
- 修正返修次数边界：`repair_limit=1` 现在允许一次真实语义重选，再审核一次；再次失败就结束，不进入循环。
- `blind_fresh` 诊断按有效 `SCHEDULER_PLAN.json` 读取批次 cursor，并报告批次阶段和返修数。
- 已将重复范围审核/返修规则写入 `PROJECT_RULES.md` 与 `CHANGELOG.md`。

## 验证
- 定向合成回归 6 项通过：物理重叠的双方身份和精确交集、一次语义重选及硬排除、重选通过前不做完整人物审核、同候选多个选段的 ASR 边界隔离、ASR 窗口缓存复用，以及 12 商品分组的模拟全流程。
- 诊断游标测试 2 项通过；选段/返修规则的 6 个函数式合成断言通过。
- Python 编译和 `git diff --check` 通过。pytest 未安装；函数式测试通过直接调用断言执行。
- 更大的 ASR 回归集合还暴露一个独立现象：ASR 权重指纹改变后，`approved_product()` 仍接受已保存的历史 PASS。它不导致本次重复范围，未在本次改动中改变该批准语义。

## 运行与 Git
- 没有运行真实商品、没有执行淘宝 plan/run/diagnose、没有连接或写入 Resolve；商品 39 原批次运行预算已耗尽，仍不得续跑。没有启动模型进程、调用 Qwen、MiMo 或第三方服务。
- 分支仍为 `codex/sentence-boundaries-sync`，HEAD `da7d81f74891`。本次相关代码和测试变更未暂存、未提交、未推送；没有创建 PR。其它既有改动全部保留。


---

# Task
修复 ASR 权重指纹变化后历史 PASS 仍被接受的问题；本轮仅做合成验证，不执行商品或 Resolve 工作。

# Repo changes
- `scripts/final_quality_gate.py`：最终证据校验读取 plan 的 ASR 依赖，要求模型身份摘要与 ASR 阶段绑定，并与当前模型身份一致；最终门禁协议升至 `final-gate-v14-current-asr-identity`。

# Runtime changes
- 未运行商品 39 或其他真实商品；未运行淘宝调度器；未改动正式时间线的失败验证事务或用户暂停状态。
- 只运行合成测试。未连接 Resolve、未启动真实模型、未调用 Qwen、MiMo 或第三方服务。

# Validation
- 通过：`test_model_weights_change_invalidates_approval_and_rebuilds_dependent_plan`。
- 通过：12 商品、四组模拟全流程；ASR worker/runtime 合成测试 6 项。
- 未通过：ASR 复用关联测试 3 项中 2 项通过；`test_other_product_reuses_frame_computation_but_cannot_approve_overlapping_plan` 预期 `outside_confirmed_range`，实际先得到 `final_asr_stage_dependency_invalid`。该合成 plan 没有 ASR 依赖，暴露出新检查的适用范围问题；按本轮返修上限停止，未继续改代码。
- 通过：`python3 -m py_compile scripts/final_quality_gate.py`、`git diff --check`。
- 未运行：project doctor、Resolve preflight、全量测试、真实媒体流程。

# External actions
MiMo called: no；Qwen loaded: no；Resolve connected: no；export performed: no。未提交、推送或创建 PR。

# Blockers
- 当前修复使目标权重回归通过，但关联合成用例发现 ASR 依赖缺失时错误过早。后续修复需保留权重变化失效行为，同时让规范范围门禁在这个合成场景先报告 `outside_confirmed_range`。
- 正式时间线的失败验证状态及用户暂停保持不变。本轮已按用户限定停止，不再修复或重跑。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮代码改动为 `scripts/final_quality_gate.py`，报告改动为本文件；所有先前已修改及未跟踪文件均保留。工作区有未暂存改动，无暂存改动；未提交、未推送、未创建 PR。此为后续提交前快照。


---

# Task
按用户要求取消必须存在一个 ASR 依赖的限制，重新运行合成实例及相关回归。

# Repo changes
- `scripts/final_quality_gate.py`：删除 ASR 依赖数量必须为一的硬限制；仅对实际存在的 ASR 依赖逐项验证执行契约和当前模型身份。无 ASR 依赖的方案继续接受范围、语义、物理方案及人物证据等门禁。
- 保留 ASR 权重变化使旧执行证据失效的校验；ASR 不获得语义决定权。

# Runtime changes
- 仅运行合成输入；没有运行真实商品、淘宝调度器或模型推理。
- 商品 39 未重跑；商品 34 的 FAILED_VERIFICATION 及用户暂停未改动。

# Validation
- 通过：AsrReuseIntegrationTests 四项，包括上一轮失败的无 ASR 依赖实例、模型权重变化、选段边界隔离及窗口缓存复用。
- 通过：重新运行一次 12 商品、四组模拟全流程，约 35.5 秒。
- 通过：受影响 Python 文件编译及 git diff --check。
- 未运行：project doctor、Resolve preflight、真实媒体验收及全仓库测试。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。未调用 Qwen 或第三方服务；未提交、推送或创建 PR。

# Blockers
本轮上述合成检查无未解决失败；未做真实媒体验收。上一轮由强制 ASR 依赖导致的关联回归已通过。既有正式时间线失败事务与暂停继续保留。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮修改 `scripts/final_quality_gate.py` 与本报告，其余既有改动及未跟踪文件保留；均未暂存。未提交、推送或创建 PR，此为后续提交前快照。


---

# Task
随机抽取一个未完成且未被本轮禁跑/既有盲测任务占用的商品进行真实盲测。使用当前项目认证配置及测试时间线，保留正式失败事务和恢复暂停。

# Repo changes
本轮没有修改源码、规则或测试；仅追加本报告。所有既有工作区改动保留。

# Runtime changes
- 通过实时 Resolve 稳定项目 ID 认证并匹配淘宝模式。按输入指纹及文件摘要核对标题编号索引，只按编号和检查点状态抽样，不以标题、字幕或旧切点选样。
- 从 15 个合格编号中随机抽中商品 42，种子 <数值已脱敏>；商品 39、商品 34、既有盲测任务及已记录完成商品被排除。新批次为 `20261026_random_blind_04`，选择模式 `blind_fresh`。
- 第一次调用因助手误传 `group-size=1`，在商品分析启动前被 `group_size_must_be_2_or_3` 拒绝；该调用已完成范围匹配但未进行商品选段、ASR、人物审核或写入。保留失败记录，在同一任务记录参数补正，将助手原设总调用上限从 1 调为 2，仅补一次正确调用；实际商品处理一次，返修上限仍为 1，未换批次或重置记录。
- 补正调用完成候选、两次选段/语义终审、两次人物预筛、ASR、物理方案及最终人物审核；第一次预筛触发一次语义重选。最终状态 BLOCKED，原因 `vision_uncertain_or_blocked_needs_evidence_repair`，未进入 Resolve 写入。
- 最终第一版人物审核 BLOCKED：4 个样本为 `DETECTOR_ERROR`，原因均为 `codex_batch_error_TimeoutExpired`；另外两版 PASS。检测错误没有被当作无人或审核通过。
- 已 diagnose；`automatic_resume=false`、`resume_command=null`、返修次数 1。没有继续运行或换商品。

# Validation
- 通过：project doctor、本批 Resolve connection doctor、实时稳定项目/测试时间线身份与目标校验、调度 plan。
- 测试线准备为 `ALREADY_EMPTY_VERIFIED`，未清空；结束后只读对比所有时间线快照完全相同。测试线仍为 0 片段、0 标记；旧 FAILED_VERIFICATION 事务摘要及 recover 暂停保持不变。
- 初次参数失败耗时 73.589 秒，其中范围匹配 70.619 秒；补正调用耗时 506.282 秒，商品处理 504.654 秒。各阶段为嵌套耗时，不重复加总：选段 53.724/52.825 秒，语义终审 19.367/21.145 秒，人物预筛 51.229/1.602 秒，ASR 115.213 秒，物理方案 0.892 秒，最终人物审核 187.052 秒。
- ASR 转写 19 个窗口、累计 98.641 秒，22 条边全部得到邻近词界，人工复核边数 0；未出现执行错误。三版物理方案分别为 2/3/6 段，时长 51.964/47.831/50.964 秒。最终质量批准未生成，不能据此宣称整链通过。
- 人物计算记录 42 次抽帧和 Apple Vision；Codex 图片复核 3 批、10 个样本，其中 2 批超时。底层缓存 130 次查找、12 次命中、99 次 identity_not_found。并行图片调用累计时间不可当作总耗时。
- 本轮未运行合成或全仓库测试；仅执行真实盲测及只读诊断。报告更新后的 `git diff --check` 通过。

# External actions
MiMo called: no；Resolve connected: yes；export performed: no。Qwen 未调用。Resolve 写入/清理：no。结束后未发现本轮 ASR worker 或调度进程。未提交、推送或创建 PR。

# Blockers
本次盲测未通过，直接阻塞是最终人物证据中 Codex 图片复核超时；不是 ASR 语义否决。一次返修与两次调用额度均已耗尽，不自动续跑。后续需先诊断图片复核超时，并在新的明确测试授权下处理；旧正式失败事务与 recover 暂停不得解除。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮公开文件仅改动本报告；项目任务、抽样、批次、诊断和回读证据均留在忽略的项目记录中。既有修改与未跟踪文件保留，未暂存、未提交、未推送、未创建 PR。此为后续提交前快照。


---

# Task
将“同一商品内通过的版本先交付”落实为默认代码和持久规则，并按用户要求随机抽取另一款商品进行真实盲测。此前商品 39 不重跑，正式失败事务与 recover 暂停继续保留。

# Repo changes
- 根因：原商品流程在任一版本人物审核失败后直接阻断商品；现有部分交付开关只筛选商品，不能筛选同商品内的版本。
- 新增 `scripts/variant_delivery.py`：从原完整物理方案和已封存的人物证据生成精确版本子集，保留原方案、原封存证据和失败状态作为不可变父依赖；不修改保留版本的素材、次序或切点。批准时复核原封存及逐项精确投影，拒绝篡改或全失败的交付请求。
- `scripts/project_scheduler.py`、`scripts/final_quality_gate.py`：最终人物结论混合 PASS/失败时，先批准独立通过全部门禁的版本，失败版本保留待处理；不重做已通过的语义终审，不把检测错误提升为 PASS。
- `scripts/workflow_scheduler.py`：通过范围先交付成为默认；不再依赖额外开关。`scripts/resolve_write_v2.py` 保留原 variant_id 并在写后报告记录实际批准及待处理范围；移除与已批准证据无关的旧项目 cursor 读取。
- `scripts/product_checkpoints.py`、`scripts/update_batch_state.py`：记录每版状态、交付范围与 product_complete；待处理版本存在时不将整商品加入完成清单，检查点字段篡改会被拒绝。FINAL_PASS/WRITTEN 仅覆盖明确批准/写入的版本范围，整商品完成由 product_complete 单独判定。
- 更新 `AGENTS.md`、`PROJECT_RULES.md`、`CHANGELOG.md`；新增 `tests/test_variant_delivery.py`，更新调度器对默认部分交付的测试预期。投影协议为 `passed-variant-projection-v1`，纳入阶段代码身份；旧派生产物不自动换身份盖章。

# Runtime changes
- 实时稳定项目 ID 重新认证通过，淘宝模式、测试目标绑定匹配。从 14 个符合条件的商品编号中抽中商品 21，种子 <数值已脱敏>；仅按核对输入指纹/摘要的编号与检查点状态抽样，不读取标题、字幕或旧选段用于抽样。排除商品 39、42、正式失败范围及既有盲测/已完成记录。
- 新任务与批次 `20261026_random_blind_05` 只执行一次 run，返修上限 1，实际返修 0。执行 plan 后 run，目标为已登记测试时间线；测试线准备为 ALREADY_EMPTY_VERIFIED，未清空。
- 三版语义终审、人物预筛、ASR 和原物理方案本地硬门禁通过。最终第一版人物 PASS，第二、三版 BLOCKED。自动投影出第一版，保留第二、三版及原始失败证据，没有把三版整体记成通过。
- 第一版 51.798 秒，已追加一段视频及对应音频，写后事务 READBACK_VERIFIED。独立回读检查点为 WRITTEN，但 product_complete=false；variant_1=WRITTEN、variant_2/variant_3=BLOCKED。测试线现有一段视频、一段音频、一个商品标记。
- 独立回读确认源/正式等非目标时间线完全未变，旧 FAILED_VERIFICATION 事务摘要不变，recover 暂停仍在。已发送本机写入回读成功通知；未导出成片。

# Validation
- 通过：新增逐版交付合成 4 项，覆盖真实商品控制链的一版失败/两版通过、独立批准和写入检查点、未完成商品保留、防篡改与全失败拒绝；ASR 关联回归 4 项；12 商品四组全通过模拟及默认部分商品先交付模拟 2 项，共 10 项有效定向/模拟测试。
- 初次测试发现误将导入的 fixture 测试类纳入发现，额外运行 16 项历史注册/索引/项目隔离测试，其中 5 项失败；已修正发现方式，未修改这些非本轮目标测试，也不宣称全仓库通过。新调度集成用例初次把同候选的三个源范围误当三个候选，修正合成输入后通过。
- 通过：受影响模块编译、git diff --check；project doctor、fresh Resolve connection doctor、稳定项目/测试目标校验、调度 plan/run、写前备份及写后回读。
- 真实单次任务耗时 716.414 秒；范围匹配 22.999 秒，分析/质量阶段 662.114 秒，Resolve 交付 24.209 秒。商品内部：选段 43.059 秒、语义终审 15.094 秒、预筛 17.307 秒、ASR 59.467 秒、物理方案 0.895 秒、最终人物审核 506.677 秒、批准 3.905 秒。阶段存在嵌套与并行，不重复求和当作总耗时。
- 范围记录 complete_range_scanned=true，候选包数量 1，完整组合产出三版；本次只交付一版是因为其余版本证据阻塞，不是候选池耗尽后降版。原方案及子集的时长、禁用内容、源范围、重叠、切点、音画同步与人物门禁经最终证据链验证；未重新读取完整原字幕。
- ASR 9 个窗口，10 条边均得到邻近词界，人工复核边数 0；本次转写累计 50.123 秒。人物抽帧 79，Codex 图片复核 11 批、41 个样本。第二版 20 个、第三版 10 个样本记录 codex_batch_error_TimeoutExpired，保持检测错误而非无人/通过。累计 codex_fallback 耗时为并行包含时长，不等于墙钟；错误计数 16 与调用 11 并非一批一错口径，不据此推算失败批数。
- 底层缓存 148 次查找、4 次命中、135 次 identity_not_found；本轮 ASR/调度进程已退出。

# External actions
MiMo called: no；Resolve connected: yes；export performed: no。Qwen 未调用；本轮实际追加测试时间线并回读通过，未清空、未恢复正式失败事务。未提交、推送或创建 PR。

# Blockers
- 商品 21 第二、三版仍因最终人物检测证据不完整而阻塞；第一版已交付，不能把整商品或整项目称为完成。图片复核多批超时和耗时较长仍是独立问题，本轮未放宽或跳过人物门禁。
- 商品 42 的历史失败与原有运行预算未改动，未重跑或迁移旧证据。此次真实验证使用新商品21和当前新协议。
- 全仓库历史测试未验证通过；上述额外发现的五项历史测试失败未在本轮修复。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮修改 AGENTS.md、PROJECT_RULES.md、CHANGELOG.md、scripts/final_quality_gate.py、scripts/product_checkpoints.py、scripts/project_scheduler.py、scripts/workflow_scheduler.py、scripts/resolve_write_v2.py、scripts/update_batch_state.py、tests/test_project_scheduler.py、本报告；新增 scripts/variant_delivery.py、tests/test_variant_delivery.py。此前全部修改和未跟踪文件保留。运行状态、抽样和证据保存在忽略目录；未暂存、未提交、未推送、未创建 PR。此为后续提交前快照。


---

# Task
只读核对商品21另外两版阻塞原因，并提出固定、有界、逐版本的自动恢复方案，避免每次异常都停下改代码。

# Repo changes
本轮仅追加报告，未修改实现。提出的自动恢复方案尚未接入调度器。

# Runtime changes
未启动新运行、图片复核、语义重选、ASR 或写入；已交付第一版保持原样。

# Validation
- 核对原完整人物审核证据：第二版初始8帧有人、2帧无人，触发20帧细化，20帧全部为 codex_batch_error_TimeoutExpired；第三版初始8帧有人、2帧无人，细化20帧中10帧超时、10帧无人。两版均缺少完整人物判定，不能从当前比例字段推断合格。
- 核对现有代码：图片复核单请求180秒，异常被转为 DETECTOR_ERROR；失败结果不作为可复用成功缓存。通用诊断返回 automatic_resume=false。缺少图片任务级持久恢复队列、失败分类与独立的有界补证流程。
- 建议固定路由：技术异常只补原失败图片；完整证据证明质量不合格才做该版本局部语义返修；身份/写后验证冲突冻结受影响写入事务。各版独立封存并先交付通过版本。
- 建议固定预算初值：每个失败任务最多两次恢复，商品级最多6次新增图片请求和3分钟恢复墙钟时间，先到上限停止；并发降低、拆小失败拼图，原图与已通过结果复用。预算和恢复尝试写入同一任务，不增加run次数或重置已耗预算。超限仅把该版置为等待证据，继续已授权队列内其他可执行工作。
- 未执行运行检查、project doctor、Resolve preflight、测试或实测；本轮为文件证据核对与设计。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。本轮未调用任何模型或第三方服务，未写入时间线。

# Blockers
当前实现仍缺少上述自动补证/分类恢复控制。技术超时恢复后，两版仍须按完整人物证据核验比例，不能预先承诺通过；不以删除错误样本、改阈值或重封旧PASS作为恢复手段。

# Git state
继续保留 `codex/sentence-boundaries-sync` 与 HEAD `da7d81f74891` 的既有未提交工作。本轮仅追加本报告，未暂存、未提交、未推送或创建PR；此为后续提交前快照。


---

# Task
按用户要求把技术异常补证、确认质量不合格才语义重组、预算耗尽只挂起局部版本的流程固定进调度器。用户不再需要因每次普通阻塞启动现场代码修复。

# Repo changes
- 新增 `config/vision_recovery.json` 与 `scripts/vision_recovery.py`，协议 vision-recovery-v1。固定初始图片请求60秒，恢复请求最多45秒；失败任务拆为两图再单图，商品内恢复串行。每帧最多两次恢复，单商品/同任务最多6次恢复请求及180秒恢复时间，先到上限停止。
- 原图、Apple结果、任务身份和恢复状态持久保存并做摘要/封存校验。调用前预占次数与时间；中断保留占用，不增加run次数或语义返修额度。预算文件绑定稳定任务/商品，修改策略不会生成新预算；跨窗口保留已耗占用。用户暂停阻止恢复请求。
- `scripts/adaptive_vision.py`：识别超时、连接/返回格式类技术失败，只补失败图；逐批成功立即缓存，恢复批内成功也立即保存。原图与Apple证据经身份验证后复用。恢复额度耗尽且仍有技术故障时停止继续发出排队图片请求。真实不确定/检测错误不得被当作无人；完整确认NO_PERSON仍按既有人物比例门禁处理。修复缺失返回面板的技术分类，并移除异常的重复计数入口。
- `scripts/project_scheduler.py`、`scripts/product_checkpoints.py`：引入 WAITING_EVIDENCE，技术补证耗尽不消耗语义重组次数；保存具体失败阶段、版本、证据引用和恢复摘要。完整人物证据确认比例超限才进入原有精确源区间排除和有界语义重组，锁住其他选段。授权队列中的其他可执行商品继续；未知系统阻塞仍保留安全停止。
- `scripts/workflow_scheduler.py`：计划绑定恢复策略，诊断区分等待证据与质量返修并公开剩余版本路由，不建议重放已耗预算的相同运行。
- `scripts/variant_delivery.py`：待处理版本保存 semantic_reselection 或 waiting_evidence 路由；通过版本仍先交付。已写入证据过期时冻结自动重选/追加，避免因代码升级误重做旧成片；未重封旧PASS。
- 更新 AGENTS.md、PROJECT_RULES.md、CHANGELOG.md。新增 tests/test_vision_recovery.py、tests/test_vision_recovery_scheduler.py，调整授权队列继续执行的调度预期。新模块及配置纳入代码/规则身份，使受影响旧派生产物失效而不改写历史事务。

# Runtime changes
本轮没有启动真实商品、图片模型、ASR、Resolve连接或时间线操作；未续跑商品21/42/39，未增加真实任务预算，旧正式 FAILED_VERIFICATION 与 recover 暂停未修改。恢复状态验证仅使用隔离临时数据，不修改真实项目队列或检查点。

# Validation
- 通过：恢复控制9项、调度分流3项、并发边界3项、ASR关联4项、逐版交付4项、完整授权队列2项，共25项定向/集成检查。覆盖超时后自动拆批、不中断其他成功批次缓存、重启不重置预算、策略变更不能领取新预算、用户暂停、原图封存防篡改、确认无人不走技术重试、超时不触发语义重选、无人超限传入精确排除范围、等待商品不阻止授权队列和逐版交付。
- 通过：受影响实现与新增检查文件编译，git diff --check。
- 未执行：project doctor、Resolve preflight、真实媒体验收、全仓库检查。此为实现接入与隔离验证，不代表真实图片服务超时已消除；不向用户用这些检查代替实测结论。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。未调用Qwen或真实图片服务，未发送用户媒体到第三方，未写入/清理/恢复时间线。未提交、推送或创建PR。

# Blockers
- 真实服务仍可能在固定预算内无法提供完整判定，此时仅挂起受影响版本，不降低人物门禁、不把缺证当无人、不无限增加超时或预算。
- 混合通过/失败时仍先交付通过版本，剩余版本保留明确路由与证据；当前改动不自动重新打开已经结束且预算耗尽的旧写入任务，也不重放已验证追加。后续处理必须服从已登记任务的授权、预算和已写入版本锁定。
- 原有全仓库历史问题未在本轮扩大修复。真实商品21的待处理两版尚未因本轮代码更新而重新审核或写入。

# Git state
分支 `codex/sentence-boundaries-sync`；HEAD `da7d81f74891`。本轮新增 config/vision_recovery.json、scripts/vision_recovery.py、tests/test_vision_recovery.py、tests/test_vision_recovery_scheduler.py；修改 scripts/adaptive_vision.py、scripts/project_scheduler.py、scripts/product_checkpoints.py、scripts/workflow_scheduler.py、scripts/variant_delivery.py、tests/test_project_scheduler.py、AGENTS.md、PROJECT_RULES.md、CHANGELOG.md 与本报告。全部既有改动及未跟踪文件保留，未暂存、未提交、未推送、未创建PR；此为后续提交前快照。


---

# Task
按用户授权发布相关代码到 GitHub，保留当前本地分支、HEAD、历史和未提交工作。

# Repo changes
- 发布逐版交付、固定技术恢复、ASR边界身份、项目任务/预算与调度依赖代码，配套配置、构造数据检查和文档。
- 发现本地未推送历史包含真实运行归档与图片，故不直接推送该历史；以当前远端代码分支为父提交，创建独立代码快照分支 `codex/variant-delivery-recovery`，仅包含公开源码、配置、检查文件、说明与脱敏报告。
- 报告中的真实商品编码/标题、稳定项目标识和本机路径已脱敏；完整报告原稿留在本机忽略目录，原章节/段落保留。规则中的个人Chrome路径改为通用HOME表达。

# Runtime changes
本轮不运行商品或Resolve，不改写真实项目、缓存、时间线或失败事务；真实运行归档、截图、媒体、字幕、标题表、凭据及项目备份不新增到发布提交。

# Validation
发布快照103个源码/配置/检查/说明文件的清单、差异格式、凭据及私有数据检查通过；未新增运行归档、图片或二进制。使用独立暂存区，原暂存区不变。扫描命中的静态材质词已人工确认不含真实商品编号/完整标题/项目映射。两个检查文件的日期项目名改为构造样例。代码验证沿用前轮记录，本轮不新增模型或实测调用；不宣称全仓库通过。

# External actions
MiMo called: no；Resolve connected: no；export performed: no。已获用户提交/推送授权；本节为发布提交前快照，实际推送结果以GitHub分支和最终回复为准，不创建PR。

# Blockers
原本地分支的未推送历史混有运行资料，保留在本机且不随本次代码快照上传；不重写其历史。既有图片复核实测限制及历史检查失败仍按前述记录保留。

# Git state
本地工作分支 `codex/sentence-boundaries-sync` 与HEAD `da7d81f74891` 保留。发布分支 `codex/variant-delivery-recovery` 基于远端 `a12f41454066`；使用独立暂存区和新提交，不切换分支、不reset、不覆盖当前工作、不开PR。此为发布提交前快照，未引用自身提交哈希。
