# Task
TB-cut 第 2 轮综合修正：全项目总控、自动分组、独立商品 Codex 上下文、统一精确方案质量门禁与恢复。父提交 3efd30cfc714；属于既定 3–5 轮中的第 2 轮。仅合成验收，结束后停止，等待复核。

# Repo changes
本轮解决的问题：
- project_scheduler 从认证标题索引建立完整目标清单，沿用第 1 轮配置补齐当前目标集，不重填素材路径。默认每组 3 款、可设 2 款；分组不改 batch identity。商品失败/REPAIRABLE 后保留同组已通过商品，停止下一组，不声称项目完成。
- codex_worker 用真实 fresh codex exec/ephemeral/read-only 独立进程，商品选择、最终审核和项目审核各用新上下文。正式接口已查阅 OpenAI 非交互文档；实际运行三个合成 CLI 探针，其中两次成对调用确认不同会话 ID 和匹配任务摘要。没有向工作会话复制上一商品聊天。
- 工作单含当前项目/配置引用、商品与编码、确认范围/候选/最终帧与词窗口、规则、有效引用与短问题。没有历史聊天、他商品原始字幕或完整错误日志。输入超过 128KB 阻塞要求拆包，不截掉候选。支持单商品独立入口和 package 模式；缺 CLI/完成事件/正确身份时 REVIEW_PENDING/BLOCKED。
- registered_pipeline 接入真实人工范围验证、全范围候选清理、现有 Whisper 加载/窗口合并、refine_words、RMS 起止检查、源帧换算与连续片段归并，产物进入第 1 轮不可变商品阶段。ASR 改为按需加载，避免仅导入模块就加载未安装依赖。
- final_quality_gate 把精修后的源帧/音频同步范围、顺序、保留文字、邻近词窗口和全片逻辑交给独立 Codex；保存封存执行记录和精确任务摘要，严格导入并重新计算本地 veto。缺真实 Codex 记录、手填 verdict、旧 closure_check 或粗选 PASS 均不能获得新 FINAL_PASS。中段吃字、旧审核伴随新切点、禁用内容、时长、重叠、无意义接缝等拒绝放行；连接词不机械判错。
- 人物证据绑定精确方案/全部版本，必须有已验证主播、完整覆盖和严格 >70% 的保守比例。现有 detector 仅任意人物样本数，适配器明确 UNCERTAIN，不能自动 PASS。没有改抽样算法或硬阈值。
- 原批准入口接入该许可；原 Resolve loader 接入精确许可读取，--check-only 只读全项目验收、不连接 Resolve。登记模式真实写入及旧 schema 1 批准/写入命令明确 BLOCKED，防止旧许可绕过新门禁。
- 全项目检查完整商品状态、跨组源范围/文本重复，随后另需独立项目 Codex 审核。没有项目审核记录保持 PROJECT_CODEX_REVIEW_PENDING；分别统计 final_review_complete、written_count、export_verified_count；真实交付尚未迁移，delivery_complete 不会自动为 true。
- 商品 lease、workspace 锁/revision、不可变结果和依赖复核防止重复覆盖/计数。单商品恢复只读取所需商品，不随分组积累聊天。阶段计时、缓存命中/失效原因、失败和返修计数有本地入口。

公开源码/测试文件：scripts/{codex_worker,final_quality_gate,registered_pipeline,project_scheduler,product_checkpoints,approve_final_plans,refine_plans_v2,person_coverage_audit_v2,resolve_write_v2,windowed_asr_v2,review_compute_cache}.py；tests/{quality_fixtures,test_project_scheduler,test_project_resume}.py；docs/{PIPELINE_DEPENDENCIES,CODEX_CORRECTION_PLAN}.md；docs/INDEPENDENT_WORKER_UPGRADE.json；CHANGELOG.md；本固定报告。共 19 个文件。
复核并复用必要的既有音频精修函数、ASR 按需加载/窗口合并和 LazyModel；人物/Resolve/ASR 文件只选本轮适配器和必要接口，不把其他未提交实现整包发布。旧变更记录与其他未提交文件原位保留。

# Runtime changes
只在临时合成 workspace 中生成工作簿/字幕/媒体占位、范围与阶段记录、工作单、模拟执行证据、检查点及质量结果。真实 CLI 只发送合成上下文隔离探针，不读取真实素材。没有修改真实 state、缓存、工作簿、字幕、视频、Resolve 或导出。没有清理缓存。

# Validation
PASS：
- 最终独立公开源码快照的可用测试 165 项全部通过；包含第 1 轮适配回归及第 2 轮 13 项新合成测试。明确排除并另列已知私有解析 blocker，不把它写成 PASS。
- 包含私有集成的公开全量命令此前实际运行 165 项，164 PASS、1 ERROR/BLOCKED，命令非零；之后补充 1 项完整目标继承测试，纳入上述最终 165 项可用测试。未把“含私有测试的最后 166 项全量”宣称全部通过。
- 工作区兼容全量实际运行 186 项，185 PASS、1 ERROR/BLOCKED。新增旧许可阻塞/完整目标补齐的验收以独立公开快照为准，不用未提交代码的工作区 PASS 代替交付证据。
- 两个真实成对 Codex CLI 合成调用有不同 fresh thread ID、完成事件及任务摘要；初始独立探针也成功。没有声称这证明真实视频语义/视觉质量。
- 12 商品/4 组、单商品选择+独立终审+项目审核、总控无历史积累、跨窗口/全目标路径继承、第二组失败停止、有效完成不重算/不重复计数、粗选 PASS 后吃字拒绝、自然结论不按“因为”机械拒绝、新切点旧审失效、跨组实际源重叠、缺终审、双项目隔离、并发 checkpoint/product lease、封存篡改、无 CLI 任务包、旧消费者拒用均有合成证据。
- 质量链测试只替换外部 Codex 传输、本机 ASR/音频/帧率探测、人物 detector 和私有 workbook parser 的执行边界；调度、真实范围/候选/精修算法、不可变产物、审核导入、检查点和写前读取实际执行。替身不是生产测试模式，也不认证真实源片。
- Git diff checks、独立暂存清单和源码/报告隐私扫描通过。

FAIL：最终可用代码回归无失败。开发中发现并修复了嵌套封存读取锁、测试 fixture/源快照变动失效以及公开 ASR import 提前加载依赖问题；未保留已知失败作为 PASS。

BLOCKED：真实 Node→工作簿集成与 project doctor。工作区 artifact_tool_version_mismatch；独立快照缺失私有 artifact-tool 包。doctor 已运行，均非零；未降级门槛、换 parser、改依赖锁或安装私有包。真实人物证明、实际 ASR 环境/模型和 timeline/export 消费者没有实测许可；不称完整流程 READY。

未运行：真实素材 ASR/音频/人物、真实剪辑、MiMo、Resolve 连接/写入/回读、导出、实际全项目续剪或耗时对照。合成 preflight/质量接口已测，真实 workspace preflight 未跑。无准确率或性能提升结论。

# External actions
MiMo called: no
Resolve connected: no
export performed: no
只读核对 GitHub PR #1，仍 OPEN、未合并，远端仍 bad5a547da36；第 1 轮没有推送。查询官方 OpenAI 文档，使用正式 CLI 发出三个合成探针；不发送真实项目内容。仅本地提交；当前没有推送授权，不推送、不改 main、不更新/合并 PR。当前没有可用的对话附件上传接口，报告仅保存本地并提供准确文件链接；不声称已上传。

# Blockers
尚未解决的问题：
- 缓存与下游尚未全面迁移。已完成登记适配链、批准与写前验收；旧固定路径 range/override、候选 batch CLI、旧语义/修复/粗选批准、window quality/ASR/refine/person batch CLI、实际 Resolve/验证/export、attest/迁移/preflight/cleanup 等仍未完整接入。详细清单和行为在 PIPELINE_DEPENDENCIES。旧产物保留，不自动认证或清空。
- 自动创建独立 Codex 上下文：当前正式 CLI 可用且真实合成探针通过，不列为环境 BLOCKED；缺 CLI/认证/执行失败的其他环境已提供可恢复 REVIEW_PENDING 工作单，不能假装执行。自动分拆超大商品任务与完整语义返修循环仍待接入。
- 实际 detector 没有主播身份/完整覆盖的证明，真实 vision PASS 仍阻塞。第 3 轮必须接入可信主播判定、自适应保守抽样及对应精确方案证据。
- 本轮没有认证 actual ASR 依赖/模型权重或服务端默认 Codex 模型版本。当前配置模型标识与代码版本参与身份，精确模型分发/权重指纹、跨批次选择性物理计算复用和更细失效范围仍要补强。不能称缓存体系全面完成。
- VFR 的精确源帧时间证明、真实 Resolve 稳定 ID 回读/标记幂等性、同步写入验证、导出验证尚未迁移；交付完成明确阻塞。封存是本机执行完整性约定，不是抵御本机所有者的远端数字签名。
- 阶段/检查点的新代码摘要使旧审核过期，升级清单已记录；不写真实状态。多记录发布沿用第 1 轮失败标记，不保证跨文件/断电事务。

下一轮建议：第 3 轮集中处理自适应主播检测、阶段耗时和缓存效率、剩余消费者及全链路回归；补模型/检测器来源和帧域证据，保持最终 Codex 审核独立。第 2 轮结束立即停止，不启动第 3 轮或真实项目实测。

# Git state
本快照在后续提交之前，不用自引用提交号。
Branch: codex/repository-baseline
HEAD/拟用父提交: 3efd30cfc714
PR: #1，OPEN，未合并；远端 head bad5a547da36。第 1/2 轮均不推送。
拟提交 19 个公开文件，独立 index 选择上述源码、合成测试、文档及脱敏固定报告。主 index 本快照无暂存改动。原有未提交 AGENTS、规则、项目说明、模板、依赖锁、候选/范围、人物 probe、连接、导出等实现及本地 archive 保留；仅复用并复核必要函数，未认证其余真实运行实现。不存在 reset/clean/覆盖工作树或 main 修改。
