# Task
TB-cut 第 3 轮综合修正：自适应主播证据、统一最终门禁、共享底层计算及计时。父提交 cf955fdfb175；既定 3–5 轮中的第 3 轮。本轮结束停止，等待复核，不进入第 4 轮或真实实测。

实测准备结论：NOT_READY。已实现安全拒绝和合成正/负例，不具备真实主播证明、完整启动依赖及真实写入/回读/导出证据。

# Repo changes
本轮解决的问题（已编码、合成验证）：
- adaptive_vision 按最终物理片段累计时长分层。每 60 秒初始 16 点，其他时长向上取整；每段首尾有覆盖，极短段按可用帧封顶。片段多时最小覆盖预算可超过 16。确定种子抖动、最长空隙补样，最多五轮，不重复源帧，耗尽停止。60 秒仅为采样测试，成片时长仍严格 >45 且 <60。
- 六类分类证据接口：HOST_VISIBLE / CLOTHING_CLOSEUP / OTHER_PERSON / NO_PERSON / UNCERTAIN / DETECTOR_ERROR。仅带主播身份和主要主体证明的 HOST_VISIBLE 计入。Apple generic human/face adapter 实际只产生 NO_PERSON、UNCERTAIN 或 ERROR，不把人脸/人体等同主播；错误保留局部诊断、停止审核且不缓存为有效分类。Qwen 禁用，未下载/外发数据。
- 每片段按样本覆盖的时间层加权，再按片段真实时长汇总。Wilson 仅参考，有效数量受两秒时间块限制并扣除最大未采样空隙风险；不宣称连续视频独立样本或 95% 全片保证。关键不确定/错误、覆盖漏洞、保守值不严格 >70% 都拒绝 PASS；低值继续补样，最终 REPAIRABLE/UNCERTAIN/BLOCKED。
- 新 Swift 单帧协议独立于未审旧 probe。FFmpeg 精确源帧提取；ffprobe 全帧时间戳证明 CFR、不同 FPS 映射。VFR、不完整/非零首帧时间戳明确阻塞，不用 r_frame_rate 近似放行。首次全帧时钟验证成本记录并共享缓存。
- 人物执行证据封存，绑定精确方案摘要、分类器二进制/适配器、解码器、OS、采样参数及源媒体。final_quality_gate 升级 final-gate-v4-host-strata，重算比例、验证封存和当前身份；cut、顺序、分类器/参数/模型变化撤销许可。检查点、批准入口、项目调度和 Resolve loader/--check-only 沿用同一门禁，缺真正 Codex 终审或可信视觉任一均不 FINAL_PASS。旧 count CLI 直接阻塞。
- compute_store 的项目级 physical-compute-v3 共享 ASR、clock、源帧分类，不共享最终许可。键含项目 UUID、媒体路径/device/inode/大小/mtime/内容 SHA、精确范围或帧、权重/库版本、参数/协议。逐键锁、原子发布、输入复核、正文摘要及 HMAC 防止并发静默覆盖；损坏或无法证明来源的新版记录阻塞。旧目录保留不导入、不清空、不重新认证。
- ASR 合并相邻/重叠窗口，复用相同或被已验证大窗口包含的请求，保留完整词上下文；当前商品 ASR 另绑定粗选依赖。按需加载模型，local_files_only 解析本机权重，不下载；SHA、Whisper/CT2 版本与解码参数进入身份。许可及 cursor 复核模型/父阶段变化，只重建受影响下游，底层不因方案变动全部删除。
- compute_metrics 记录细分墙钟、调用/数据量、hits、失效、重试及错误，含候选、选择/终审/项目审核等待、ASR加载/转写/VAD、RMS、音视频解码、Vision、检查点/门禁及 cache/签名/clock。内置 ASR/VAD 为一体测量；没有虚构独立 VAD 时间。子阶段时间包含在外层，不相加当总耗时。MiMo/Qwen 未调用计零。

公开提交范围：scripts/{adaptive_vision,compute_store,compute_metrics,asr_identity,registered_pipeline,final_quality_gate,product_checkpoints,project_scheduler,person_coverage_audit_v2}.py；scripts/apple_frame_probe.swift；tests/{quality_fixtures,test_project_scheduler,test_adaptive_vision}.py；docs/{PIPELINE_DEPENDENCIES,CODEX_CORRECTION_PLAN}.md；docs/ADAPTIVE_VISION_UPGRADE.json；CHANGELOG.md；本固定报告。共 18 文件。

独立 index 只选择本轮 hunk。既有人物 probe、连接器、旧 cache helper、ASR/window/refine batch、规则/模板/依赖锁等无关未提交改动原位保留，不随本轮整包发布。

# Runtime changes
仅临时合成 workspace、合成字幕/标题/媒体占位、范围和审核记录。额外以 FFmpeg 生成 1 秒 10 帧黑屏，沙盒外验证真实新 Swift/ffprobe/FFmpeg 负例。没有分析真实媒体，没有修改真实素材、字幕、工作簿、state、现有缓存或时间线。没有缓存清理。

效率对照（单次合成观测，非真实剪辑速度）：
- 60 秒可信合成分类边界：冷运行 0.0244 秒，首轮 16 帧、16 次分类、0 hits；暖运行 0.0115 秒，新增分类 0 次，16 帧+1 clock 共 17/17 cache hits。时间主要是控制器/cache，分类/解码执行边界是替身，不能外推模型提速。
- 50 秒低/临界合成样本：每轮 14 点，共五轮 70 个不同源帧；没有重复检测/解码有效点。每分钟最多约 80 为设计预算，短片段最低覆盖例外。
- 旧每 0.5 秒密度对应每分钟约 120 点，是理论预算；未运行旧模型密集实测，不作准确率/总耗时/倍速结论。
- 冻结公开快照真实黑屏 smoke：1.3785 秒，5 轮共 10 个可用帧，10 次解码/Vision、1 次 clock；全部 NO_PERSON，REPAIRABLE。仅证明协议和错误/负例链路，不能证明真实主播正例或最终成片质量。

# Validation
PASS：
- 冻结独立公开快照全量 unittest discover 实际运行 190 项：189 PASS，1 ERROR 对应已知私有 workbook BLOCKED，命令非零。之后仅追加一个跨商品底层帧共享与批准拒绝的消费者测试，单独纳入最终 25 项新测试；没有把尚未重跑的 191 项全量写成全通过。独立用例证据累计 190 PASS、1 BLOCKED。
- 工作区兼容全量实际运行 212 项：211 PASS、1 同类依赖 ERROR/BLOCKED，命令非零；不以含旧未提交实现的工作区结果代替公开快照。
- 25 项本轮定向合成测试：最后独立公开快照全部 PASS。首轮16、五轮独立、70%临界拒绝、长短片段权重、特写/其他人、error不缓存、Qwen未验证、cut失效、源帧缓存、参数/二进制更新、双项目、旧count/旧新版并存、损坏cache、短帧耗尽、多FPS/VFR阻塞、并发和失败恢复、统计、缺终审、模型缺失、ASR包含窗口、权重更新重建与跨商品共享/重叠拒绝。
- 第 1/2 轮回归保留：12商品4组、独立终审、无商品跳过、重试不重复计数、词中段吃字、因果不机械误拒、最终切点/模型失效、项目覆盖与跨组重叠、批准和只读写前消费者。合成替身只覆盖外部 Codex 传输、ASR/音频/帧率/分类与私有 parser 边界；采样/cache/调度/词精修/批准/门禁实际执行，无生产测试模式。
- Swift 编译、真实黑屏负例 smoke、Python compileall、Git diff checks 与暂存文件/隐私扫描通过。签名与锁、未认证旧数据均有实际代码路径测试。

FAIL：最终已跑代码用例无未修复失败。开发中出现初始边界间隙、媒体 mock 改 FPS 未换媒体身份、缺记录异常类型和 ASR 替身生命周期不足，均修正并复跑；不放松生产检查。

BLOCKED：project doctor 两处都非零：工作区 artifact_tool_version_mismatch；公开快照 workbook_node_version_mismatch 且实际 parser 确认 ERR_MODULE_NOT_FOUND/@oai/artifact-tool。没有改锁、降级 parser、安装私有依赖或跳过后报 PASS。默认 Python 无 faster_whisper；已知专用 ASR 环境未认证接入，本轮没转写真实媒体。Qwen 常见本机缓存目录没有发现模型，未声称其他安装位置绝不存在，也未验证/授权任何 Qwen 执行。

未运行：真实主播正例/难负例/准确率与密集对照，真实 ASR/RMS/VAD、实际 Codex/MiMo 模型质量/服务器分发版本、真实项目 preflight、Resolve连接/稳定ID/写入/回读/导出、真实跨窗口续剪或整项目性能。新门禁与 --check-only loader 的合成消费路径已测；真实安全写保护未迁移。

# External actions
MiMo called: no
Resolve connected: no
export performed: no
本轮 Codex 传输是合成替身，没有调用远端审核真实素材。只读核对 PR #1，OPEN、未合并，远端 head bad5a547da36；查询 Apple/NIST 主来源公式与能力边界。没有推送、修改 main 或 PR。没有下载模型或向第三方传输项目内容。对话没有附件上传接口，报告保存在本地并提供准确文件链接，不声称已上传。

# Blockers
尚未解决的问题（按实测风险）：
1. 主播身份/主体可信分类器未实现并验证。Apple 只 generic person；私有 Apple 权重指纹未知，不能当主播许可来源。Qwen 安装/可用性/授权未验证。真实 vision PASS 仍阻塞；六类协议不等于六类真实识别质量已完成。
2. 真正 Resolve 稳定 ID 校验、锁/备份/幂等写入、失败回读与 export_verified 消费者尚未迁移，实际写入入口仍拒绝，delivery_complete 保持 false。
3. 私有 workbook/锁定 Node 依赖阻塞启动；指定 ASR 环境和真实模型、远端 Codex 精确版本尚未验证。不能称模型身份体系全面解决。
4. VFR 精确映射尚未编码；时间戳证明首次需全解码，源帧抽取仍可能多次从文件头解码、媒体 SHA 有首次成本。没有真实性能基线，未证明吞吐或漏检率。
5. 旧固定范围/candidate/ASR/refine/window/person batch、语义返修、validate/export/attest/migration/preflight/cleanup 等仍未全面接入，清单见 PIPELINE_DEPENDENCIES。新版封存有问题时安全阻塞，需要明确修复路径，不能重新认证旧数据。代码摘要升级会让旧阶段失效；不写真实 state 迁移。HMAC 不抵御本机密钥持有者，多文件发布不是断电事务。

下一轮建议（仅 NOT_READY）：第 4 轮限于可信本地主播身份/主体模型与难负例验证、恢复锁定工作簿启动依赖、Resolve 稳定 ID/精确质量许可/并发幂等写回读的合成安全链；核对拟用 ASR/审核环境。VFR 若不支持继续拒绝。授权后的真实质量/性能验证需单独指令，不在本轮启动，不展开无限优化。

# Git state
本快照在后续提交之前，避免自引用新提交号。
Branch: codex/repository-baseline
HEAD/父提交: cf955fdfb175
PR: #1，OPEN，未合并；远端 head bad5a547da36。
拟提交本轮 18 个公开文件；独立 index 暂存，主 index 原有无暂存。旧未提交 AGENTS/规则/说明/模板/依赖锁/人物probe/缓存/ASR/连接/导出等修改及 archive 全部保留。只选择本轮 hunk，没有 reset/clean、覆盖工作树或修改 main。仅本地独立提交，不推送。
