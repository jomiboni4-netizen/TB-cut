# 2026-10-22 — manual cutpoint preview uses normal product markers
- Allow semantic-PASS products recorded as REPAIRABLE for local ASR edge work to enter the existing manual preview gate.
- Write standard blue product-start markers during preview so user cutpoint edits can be read back without a later marker-normalization pass; retain preview transaction status and readback checks.

# 2026-10-22 — mixed semantic and human-confirmed batch ranges
- Let the scheduler's existing range-publication stage accept an explicitly human-confirmed product range alongside semantic-inference ranges for the other products in the same batch.
- Keep the two proof types separate and validate all published product ranges together for overlap before recording their checkpoints.
- Re-validate and re-publish the batch manifest on scheduler resume so a corrected boundary cannot leave an older current range pointer in use.
- If a wider local ASR neighborhood still cannot align a selected subtitle edge, re-enter complete-pool selection and semantic review instead of repeating the unchanged physical plan.
- Preserve the failed source selection in the repair request so the worker can compare its replacement and verify that the unresolved edge was avoided.
- Ignore write-only user authorization fields when reading optional variant-count overrides for a product selection task.
- Honor the per-failure repair cap even when a local edge-repair request remains in the cursor, preventing unchanged retries after the limit.

## 2026-10-21 — semantic review uses practical listener threshold
- PASS when retained speech is understandable without a major audible/logical hole; do not demand formal discourse closure or an explicit “所以” after “因为” when the intended result is clear.
- Align persistent rules, final and project review instructions, and assembly review task; bump semantic contracts so older derived approvals are stale.
- Causal review now evaluates whether reasons support results and whether cuts introduce missing premises or false causal implications, including relations expressed without explicit connector words.

## 2026-10-21 — user-authorized semantic range publication
- Allow batch ranges to be inferred from title meaning, ordered subtitle context, and read-only product details after user authorization; retain exact subtitle/title anchors and semantic provenance, and reject unsupported or overlapping ranges.
- Keep human-confirmed and semantic-inferred evidence distinct through candidate selection and downstream checkpoints.
- Re-publishing an already stored valid range or plan now restores its current pointer when missing or stale, allowing scheduler recovery after input-context changes without manual cache edits.
- Expose the existing VFR average-rate mapping as an explicit run-only scheduler option so a user can waive exact source-frame mapping for one batch without changing project defaults.
- Audio boundary refinement honors the already-proven half-frame ASR rounding allowance when adjacent word intervals contain no whole frame, while keeping the cut within that bound.
- Add a scheduler-routed semantic-review preview append for explicitly approved variants, with a project backup, product/variant markers, two-second empty black gaps, and readback verification; it remains labeled as preview and does not assert overall batch approval.
- Project registry reads use explicit UTF-8 decoding so Resolve scheduler subprocesses work under non-UTF-8 system locales.
- Product checkpoint artifacts and pointers also use explicit UTF-8 decoding after Resolve API initialization changes the process locale.
- Pending review-preview recovery verifies the original immutable plan body digest and existing Resolve clip rows before marker-only repair; it never replays the append.
## 2026-10-10 — within-variant repeated-source guard
- Reject overlapping source-frame ranges inside a single final variant during cut-plan refinement and before Resolve writes. This catches repeated footage even when cross-variant checks pass.
- For the current batch, removed the duplicate product 3 variant 1 closing segment from the approved plan and Resolve timeline; the remaining variant duration is 49.96 seconds.

# Change Log

## 2026-10-22 — 语义通过、ASR 待修的时间线预审
- 按用户要求，语义组合通过但局部 ASR 修边失败时，可通过淘宝调度器将本批语义片段标记为待手动修边预审，保留两秒黑场、写前备份及写后回读；预审不等于正式成片通过。
- 预审基线记录源切点和时间线位置，待用户手改后对照实际修改，提炼未来可复用的切点规则；取消旧预审入口仅许可上一批固定商品编号的限制。

## 2026-10-22 — 调度器人物检测器与 Codex worker 启动诊断
- 淘宝调度器默认在当前 Resolve 项目绑定目录的依赖区自动构建/加载并验证 Apple Vision 人物检测器，跨批次复用，减少因遗漏 `--detector` 造成的重复阻塞；检测任意人物，不做身份匹配。
- 独立 Codex worker 将常见沙盒拒绝、认证、服务限额、超时和启动错误分类为稳定的可恢复原因；权限受限时明确要求从沙盒外按调度器恢复，不暴露原始诊断输出，也不绕过审核。

## 2026-10-22 — 修复首帧偏移并收紧重复返修
- 纯排布修复从目标时间线起始帧重建商品块；空时间线的首次写入不再额外留出 2 秒前导空白，只有已有内容时才追加间隔。
- 同一商品、同一候选/方案身份和同一失败原因的返修次数跨调度器调用累计，到达配置上限后暂停，避免每次重启又获得一轮相同返修额度。
- 商品阶段耗时保留最近 100 次调用；调度事件写入实际持续时间，便于区分本地计算、Codex 调用及重复调度。历史记录没有的耗时不作追补或估算。
- 淘宝顺序调整为“字幕组合 → Codex 语义终审 → 切点局部 ASR → 人物复核”；ASR 仅处理字幕范围首尾附近窗口，人物比例超限时只修复受影响版本并复用仍有效的缓存。

## 2026-10-21 — 双项目调度器入口
- 增加淘宝、都督专用调度器与共享模式路由，记录每次委派的阶段结果；未连接的交付阶段明确阻断。
- Codex 批次执行先查看对应调度器计划，再从对应入口继续。
- 增加 `diagnose` 恢复建议，报告商品可复用检查点、失败原因和保守回退点；写入事务先恢复核对，不自动重放。
- 标题索引生成器升版以强制重建旧缓存，兼容当前工作簿“ID”表头；字幕分段按数字区间排序，确保全局时间与 Resolve 素材时间线一致。

## 2026-10-20 — Resolve 写入器按事务用途排序
- 冷启动“清空登记目标后逐商品追加并回读”固定使用 `reusable_test_delivery.py`；`resolve_write_v2.py` 作为不同的追加式登记路径保留，不自动接替该事务，也不在部分写入后切换重放。
- 新入口单测通过不代表 Resolve 实际写入已验证；旧入口及其消费者仅在各自契约下使用，迁移完所有调用点前不删除旧模块。

## 2026-10-20 — 剪辑语义审核由 Codex 独立负责
- 商品语义理解、候选选段和成片语义审核统一由 Codex 完成；剪辑任务不调用 MiMo 或其他外部语义审核服务。

## 2026-10-20 — explicit marked-test timeline reuse
- Add a test-only Resolve transaction for an explicitly authorized existing target: back up the project, clear only the recognized prior test clips/markers, append one selected product, then require a fresh-process readback before the next product.
- Keep test clips labeled `Codex测试｜待审核`; record subtitle-based approximate VFR ranges and seam markers without claiming audio or framewise certification.

## 2026-10-20 — REPAIRABLE 必须回退重跑，不得终止
- `REPAIRABLE` 进入有界回退状态机：记录失败阶段、证据、回退阶段、应失效的下游产物、应保留的有效上游产物和尝试次数，再从最近有效上游阶段重跑。
- 环境故障改用有界工具/运行时回退，只重跑失败阶段；真实缺输入/授权保留恢复点。达到重试上限时标为可恢复暂停或耗尽并继续独立商品，禁止无变化重复执行。
- `BLOCKED`、`REPAIRABLE` 和 `EXHAUSTED` 分开表示外部阻塞、可修复失败与已耗尽，调度器不得因单商品返修而静默终止后续独立商品。

## 2026-10-20 — 商品级即时交付与批次排序
- 商品逐个运行；每个商品通过硬门禁后立即写入达芬奇并回读核实，持久化完成检查点，再处理下一个商品。批次级审核不得延迟已通过商品的交付。
- 全部请求商品完成或明确阻塞后，只对本轮写入的商品块按工作簿物理行 ID 排序；保留内部剪辑次序、音视频关联、标记及其他人工/历史内容，并回读核验。
- 将语义复核限定为对实际保留语句、最小原源上下文和真实段间关系的判断；连接词、字幕分行或局部残句不再作为自动拒绝理由，同时保留真实语义闭合硬门禁。
- 按 P0–P5 分级新增下游集成就绪检查、逐商品进度账与重复失败止损、跨对话身份连续性、证据有效性、音画源时钟/人工补画判读及效率报告要求；不将所有整改项统一标成 P0。

## 2026-10-20 — explicit VFR average-rate audit path
- When a batch explicitly authorizes approximate VFR mapping, source clock evidence now uses stream average-rate, duration, frame-count, and start-time metadata; it does not decode or scan every frame timestamp.
- The evidence records the measured average rate separately from the preferred target timeline rate, labels source frame-to-time conversion approximate, and uses a new versioned cache identity. Default CFR certification still inspects decoded timestamps.
- This does not certify individual frame timestamps; use only under an explicit batch override.
- The scheduler uses the freshly read project rate to validate compatibility, while source frame-to-time conversion uses the measured mean rate. The target timeline still uses its registered project rate; no full frame timestamp scan is performed.
- Apple Vision pose-model setup failures now retain human-rectangle and face detection. If both lack person evidence while pose is unavailable, the sample is `UNCERTAIN` and blocks the audit; configured thresholds and sampling counts are unchanged.

## 2026-10-19 — ASR 改为最终切点附近核验
- 由 Codex 先依据完整字幕索引与原源上下文确定最终成片组合；本机 ASR 不再对候选正文或整段成片做转写。
- ASR 仅核验最终选段的开头和结尾附近是否吞字、半字或错位；默认每个切点窗口不超过前后各 2.5 秒，只有边界证据含糊时才局部扩窗并记录原因。
- 该规则改变 ASR 依赖语义，旧宽窗口 ASR 方案不能作为新逻辑的当前证据。固定交付入口仍需迁移到 cue-based 选段与 edge-only ASR；迁移完成前不得宣称运行时已执行新逻辑。

## 2026-10-19 — 工作簿依赖取消日常版本号门禁
- 日常信任依据改为 Codex 托管运行时解析出的 `@oai/artifact-tool` 包身份和实际 API 能力，不再按 Node 或依赖 semver 阻断升级。
- 保留工作簿真实结构/读写保护验证；精确版本与分发摘要仅用于明确的历史复现。

## 2026-10-19 — 切点声音提示与局部核实
- 首尾电平检查只判断紧邻切点的声音连续性，修复后句起声和浮点窗口误分类造成的风险提示；不据此宣称吞字或字音完整。
- 增加只读、身份校验及源级缓存支持的局部诊断入口，保留原方案与原ASR，分别记录新计算、命中、失败及耗时。诊断结果不提供最终声音通过或Resolve写入许可。

## 2026-10-15 — workbook runtime compatibility records
- Keep the 2.8.93 exact distribution locks for strict historical replay; allow daily use only for Node/artifact-tool environments reviewed against the workbook contract and an exact bundled distribution digest.
- Verify an approved distribution once per repository runtime environment, reuse its local identity record across Resolve workspaces, and recheck its lightweight file stamp and contract revision on each run.
- Make Project Doctor warn when workbook operations are unverified or incompatible while keeping unrelated checks available; workbook readers and writers still fail closed.
- Validate count backfills on an exported temporary file, reopen it, verify non-target values/formulas/styles and counts, then atomically replace the requested file. Recover zero-padded numeric IDs from OOXML evidence.

## 2026-10-13 — 商品语义定位与 MiMo 两阶段分析
- Excel 商品编号与编码只标识 SKU；链接号是辅助线索。新增只供本地语义筛选的宽窗口，不自动发布正式商品范围。
- 禁用内容改为句内子句过滤；保留有效片段、原 cue 来源，并标记混合句切点复核。MiMo 拆成商品归属/句群提取与版本组装两阶段。
- 返修按通过版本实际使用的源时间区间扣除；独立审查不再采信 MiMo 自报的 `closure_check`，没有独立 Codex 记录保持 `REVIEW_PENDING`。
- 本轮本地文字预览只供当前商品审阅，不进入 Git；Resolve 与实际剪辑未执行。

## 2026-10-12 — one-project path binding and cache-safe grouped editing
- Persist requirement to bind a complete Resolve project's input folder/configuration once and inherit it across Codex windows and product groups, asking only for missing/changed inputs.
- Add P0 design for stable project identity, project/run/product/stage cache namespaces and signatures, concurrency-safe checkpoints and idempotent Resolve resumption. Do not re-attest or delete stale caches to hide conflicts.
- Treat whole-project requests as full product coverage, processed in small quality-gated groups (initial proposal 2–3 products) with a final cross-group audit; a subset PASS never means whole-project completion.
- Fold these changes into the existing 3–5 code-correction-round cap; this commit is documents/rules only and does not count as an implementation round. Runtime rule identity changes again; no real cache or Resolve mutations.

## 2026-10-12 — bounded correction plan and semantic/vision priorities
- Save user-approved requirement for 3–5 reviewable Codex correction cycles before a separately authorized real-media acceptance test; stop after five and report unresolved blockers.
- Prioritize final-cut speech continuity (especially middle seams), independent Codex final semantic review, practical causal sentence closure, measured runtime reduction and adaptive person sampling.
- This persistent-rule addition changes the runtime rules fingerprint. Existing derived runtime caches must be treated as stale until properly rebuilt; no real state/cache/Resolve changes were made. The algorithm documented here is a design target, not released code.

## 2026-10-11 — Apple Vision person probe sandbox handling
- Diagnose sandbox ANE model-load and CoreVideo `-6662` pixel-buffer errors as execution failures rather than absent people; rerun Vision outside the sandbox and fail closed with the detector errors visible.
- Report individual Vision request failures, remove deprecated CPU-only forcing, and rebuild the cached probe when its Swift source is newer.
- Verified all nine active variants outside the sandbox at 0.5-second sampling; each returned 100% person visibility with no black/freeze ranges.

## 2026-10-10 — Vision frame extraction reliability
- Extract video frames through ffmpeg before Apple Vision classification, preventing AVFoundation decode failures from being counted as no-person samples.
- Run available Vision person detectors independently and fail closed if all detectors fail on a frame.

## 2026-10-10 — MiMo model endpoint
- Switch the user-authorized semantic-review model from `mimo-v2.5-pro` to `mimo-v2.6-pro`; official pay-as-you-go token rates are unchanged.

## 2026-10-10 — workbook runtime alignment and per-batch inputs
- Align the pinned workbook distribution with the available Codex runtime: Node 24.19.0 and `@oai/artifact-tool` 2.8.89, with its verified full-tree digest.
- Make Project Doctor and title indexing prefer the bundled compatible Node when the shell default is older.
- Require each new editing batch to use paths supplied for that batch, including a freshly specified title workbook; never reuse prior-batch paths.
- Add a batch-scoped blank-title skip policy, included in the input fingerprint, so untitled products are excluded without changing numbering or reusing stale title indexes.
- Bind batch product IDs into the input fingerprint and scope accepted range manifests to the current batch while retaining source-local human confirmation and overlap checks.

## 2026-09-26 — authenticated range override safety
- Allow missing ranges to be added only for authenticated current titles; reject duplicate, unknown and invalid source/time overrides.
- Stage and validate outputs under a fixed input snapshot and shared generator lock; publish with fail-closed rollback handling.
- Add synthetic input-mutation and publication-fault tests. No real overrides applied; downstream reuse requires separate recovery review.

## 2026-09-26 — cache generation safety v2
- Remove legacy titles attestation; enforce title generator/schema provenance in readers and preflight.
- Freeze subtitle inputs, lock generators, stage outputs, recheck identities, and fail closed on publication/rollback faults without hiding the first error.
- Parse the same bytes authenticated by metadata SHA; add synthetic mutation, concurrency and fault tests.
- Pin the private Node workbook distribution and add read-only dependency checks without changing the XLSX parser.
- Record stale generator artifacts and separate recovery authorization in docs/CACHE_SAFETY_UPGRADE.json; no real runtime changes.

## 2026-09-26 — deterministic title index generation
- Define effective workbook row order as product/link numbering; reject partial rows and duplicate IDs, preserve string IDs and actual sheet names.
- Add Node machine-readable workbook inspection and Python title validation/publication with current identity metadata, synthetic regression tests, and rollback on publication errors.
- This runtime-effective rule addition changes the rules fingerprint. Existing derived identities are stale until explicitly rebuilt; no real runtime generation or metadata refresh is part of this code update.

## 2026-09-26 — runtime-only rules fingerprint
- Exclude explicitly named communication, Git and review-report sections from cache rules identity; retain all other rules and config files by default, with a versioned digest and no automatic legacy acceptance.

## 2026-09-26 — temporary public review report
- Require one fixed, sanitized task summary in `tmp/review_reports/LATEST_CODEX_REPORT.md`, with validation and Git state; keep actual runtime data ignored and clean the summary only on explicit user request.

## 2026-09-25 — public Git workflow
- Require reviewable public-file diffs, task branches, pre-commit privacy checks, PR-based review, and stage-end Git status reports; keep runtime and business data local.

## 2026-09-25 — legacy runtime migration preparation
- Added `migrate_runtime_layout.py`: dry-run by default, explicit apply, provenance checks, local backups and report, no automatic attestation of unknown artifacts.
- Added provenance-checked conversion of runtime references to workspace-relative paths without changing plan clips or audit decisions.
- Scoped relocation preflight to configured batch and runtime fields, excluding legitimate external media paths and stale historical batches. Legacy artifacts fail identity validation until rebuilt or explicitly attested.

## 2026-09-25 — runtime workspace path abstraction
- Added a shared repo/workspace path resolver and a read-only relocation preflight. Existing commands default to the repo directory; CLI workspace overrides environment configuration.
- New plan manifests store workspace-relative references; old absolute references are identified explicitly and rejected when outside the active workspace. Runtime metadata still uses schema 1 and pipeline 2.1.
- Path abstraction preserves editing rules, MiMo prompts, and Resolve write semantics; it does not perform physical workspace movement.

## 2026-09-25 — runtime cache identity
- Documented actual stage dependencies and hidden inputs in `docs/PIPELINE_DEPENDENCIES.md`.
- Added sidecar metadata checks for key cache reads and writes. Legacy files without metadata remain local but fail closed until explicitly reviewed or refreshed.
- Metadata format starts at schema 1 and preserves editing rules, MiMo prompts, cut semantics, and Resolve append behavior. Legacy artifacts are not automatically rewritten or deleted.

## 2026-09-10
- Default progress updates to Chinese and continue autonomously until completion or a hard blocker.
- Upgrade pipeline/rules to V2.1: complete anchored product ranges, wide candidate pools, dependency-aware connector checks, sentence-tail protection, physical clip normalization, and PASS/REPAIRABLE/EXHAUSTED repair flow.
- Mark product ranges and all downstream derived V2 artifacts stale while preserving raw subtitle/video fingerprints, subtitle index, base media metadata, and local model weights.

## v2-initial
- New clean project architecture; do not inherit old cache/plan JSON by default.
- Final output target up to 3 variants; reduce count if hard rules cannot be satisfied.
- Final duration strictly >45s and <60s.
- Candidate pool has no duration constraint.
- Zero physical source overlap and zero semantic duplication across variants.
- Added semantic dependency closure rules.
- Added >70% host-visible hard gate with local Apple/Qwen vision cascade.
- Added raw-SRT context protection and cleanup tooling.
## 2026-10-10 — Codex semantic review default
- Codex now performs all semantic review and selection by default; MiMo and local semantic-review models are not called unless the user explicitly changes this preference.

## 2026-10-11 — 空标题占位与成片核对
- 空标题不剪辑，但保留原数据行编号并记录占位；旧压缩编号按编码 ID 对照，派生方案过期后重新核验。每组三个商品核对完成后发送 Mac 系统通知。
- 成片核对改为检查后直接修复，全部硬审核通过后写入达芬奇并回读验证，再通知本组完成。
- 补全成品结尾碰到下一条时，允许后续成品整体后移，音视频与关联标记同步移动；写入前重新核验位置，保留顺序、素材范围及原有间隔。

- 2026-10-11：统一达芬奇连接入口；新增动态网络回退、有限重试、只读超时检查和本地诊断，固定新窗口连接规则。
- 2026-10-11：检查修复增加按需加载局部 ASR、带素材/检测器指纹的人物检测缓存与命中计数；先定语义修复再集中复核，保留完整方案审核及写入门禁。

## 2026-10-12 — 第 1 轮项目登记与断点基础
- 项目配置只登记一次；新合同 project-resume-v2 用项目 UUID、目录身份和离线确认的 Resolve 稳定 ID，旧登记不自动认证。
- 分离项目上游索引、批次路径、不可变商品阶段与当前方案/范围；新增带证据和版本的检查点、锁、revision 和失败标记。
- 接入真实初始化、字幕/标题生成与状态更新；未迁移的下游拒绝新合同。真实状态/缓存未改动，剪辑硬规则未改变。
- 合成测试覆盖双项目、跨窗口/批次、旧审核、新方案/范围、并发、中断、旧新版共存；私有工作簿依赖检查仍阻塞。

## 2026-10-12 — 第 2 轮独立上下文与质量链
- 接入全目标分组调度、单商品 lease/检查点、独立 Codex CLI 选择/终审及项目级语义复核；无可信执行记录保持待审核。
- 接入现有候选、ASR、词/帧/音频算法与精确终审、批准、Resolve 写前只读接口；旧消费者继续阻塞。
- final-gate-v3 及代码摘要使旧审核失效，未认证或清空真实缓存；主播证据与真实写入/导出仍阻塞。未修改剪辑硬阈值。

## 2026-10-12 — 第 3 轮自适应主播证据与计算缓存

- 升级 host-strata-v3、physical-compute-v3、final-gate-v4-host-strata；代码摘要撤销旧质量许可，未知旧缓存不迁移/认证。
- 最终物理片段分层采样、最多五轮新帧、时长加权保守判定；Apple generic person 不能证明主播，旧 count CLI 阻塞。
- 源帧/ASR 窗口计算共享封存缓存，CFR 时间戳证明、模型权重/参数复核和包含窗口复用；锁及失败保护。
- 统一细分墙钟/计算量/hits/失效/错误统计；仅合成测试及黑屏 smoke，NOT_READY，真实写入/导出仍阻塞。
# 2026-10-13 — 第 4 轮人物出镜定义纠正

- `HOST_VISIBLE` 统一解释为明显人物主体出镜，不进行主播身份认证、跨镜头身份匹配或 Qwen 阻断。

- Apple Vision 人体框、面部框和姿态几何采用可配置的主体阈值；分类算法和审核门禁升版，旧分类证据失效。
- 保留严格大于 70% 规则及 16 点/分钟、最多五轮、不重复、时间加权和错误 fail-closed。

# 2026-10-19 — 人物存在与特写占比分离
- 人物检测不再要求正面、全身或较大画面占比；可靠的人体、人脸或姿态信号均计为有人，方向与身份不参与判定。
- 单独标记大脸/近满高人体框为特写，最终特写时长占比上限 30%；任何被抽样确认的无人画面都阻止通过。
- 人物证据与评分协议升版，旧人物审核不能复用；审核仍是分层抽样，不能伪称逐帧证明。

# 2026-10-14 — 单商品三版本联合规划默认目标

- 阶段A统一收集合法完整句群；阶段B基于全池联合分配来源时间与卖点，默认寻找3条内容不同、源时间零正重叠的成片，不能找到首条即停。
- 允许最终0–2条真正合格成片；不足3条须逐项记录素材、内容、时长、来源、语义或技术门禁的实际原因，不以低质量内容凑数。
- 当前工作簿运行时统一锁定 Node 24.19.0 与 artifact-tool 2.8.93；修复依赖测试只因测试夹具缺配置而误报通过的问题。

# 2026-10-14 — 历史运行逻辑隔离

- 新任务要求明确 `--workspace` 和冷启动输入清单；环境变量仅用于冲突检查，不能隐式选择旧批次。旧语义预览不再读取固定历史候选范围，Resolve 只读诊断按选定工作区核对项目。历史缓存和冻结证据原位保留。

- 2026-10-18：通用句首改为原句锚定与有界词间修边，静音不再改选句首；终审协议升级，旧切点证据不可复用。

- 2026-10-18：按用户澄清区分语义与定位：取消以半帧取整误差和中段承接词自动否定句子的拦截；原ASR不改写，声学风险保留；ASR前真实音频窗口起点对齐20ms网格。

- 2026-10-19：增加句子拥有范围内的本机静音修边，保留原词时间，未决声音风险不放行；Resolve源起点整数API出现浮点截断时，仅在精确时间和左偏移共同确认后通过回读。

## 2026-10-19 — 固定执行与分项计时

- 将句子选词后的组装、片段级声音缓存、独立语义/人物并行检查及新建合并时间线事务收敛到delivery_pipeline固定入口；正常商品改JSON参数，不再生成本轮专用执行器。
- 源级ASR继续按模型/参数/素材/窗口封存复用，只有miss才转写；组装、逐段声音、最终语义、人物和写入按职责失效。旧证据保留，不自动升级。
- 增加创建/追加前事务记录、核验式恢复和新OS进程独立回读，部分写入禁止自动重放；保留所有既有线。新增显式计算/选段/开发/等待/验证与返工时间账，未知间隔不补造。

- 2026-10-19：按用户要求固定复用已登记的测试时间线；全部审核和备份完成后仅清空该线片段/标记再写入，保留其他时间线，避免按商品新建。
- 2026-10-19：扩展Resolve结束帧精度回读：精确结束时间与源左偏移加时长必须同时吻合，才解释整数API少一帧的取整误差；实际偏移继续阻断，原始读数保留。

- 2026-10-19：组合阶段增加绑定原词/上下文/逐接缝的主Codex细审记录，先阻断残句与删首词问题；项目帧率显式读取登记、同项目继承复用，写前只读核对，禁止反复设置既有时间线帧率。保留独立终审、逐帧时钟与源端精确回读；不重写已验收成品。
# 2026-10-19 — P0 固定两阶段人物门禁与商品返修流程

- 人物门禁改为每版10个等时初筛点；无人/衣物点触发其所在10%时段的10点细查，聚合无人估计占比不得超过10%。细查时段最多6秒，并随短片缩短。抽样与物理切片数无关；一般全有人成片从多轮抽样降为10帧/版，检测数据缓存身份保持不变。
- 门禁/特写/置信度约束统一由本地 `config/vision_rules.json` 提供；审核契约升级，旧汇总不能重新盖章，底层未变帧的身份匹配分类缓存可复用。采样结果明确为工程估计，不作逐帧保证。
- 固化约五分钟的单商品优化目标，明确商品级选择/返修/人工确认通过工作区JSON记录，禁止为单个商品改脚本。共享规则修正需一次性更新契约、回归、规则和变更日志；返修只重算依赖变更的阶段。
- 主Codex段落初审与独立终审加入明显卡壳、假起句、重复词音节、无效填充检查，以单独 JSON 项和理由记录；旧初审契约失效。
- 合成回归覆盖多缺口累计、10%边界、短片复查窗、拼接段均匀采样、错误与不确定状态。真实媒体耗时改善尚未测量，不宣称五分钟目标已达成。

# 2026-10-19 — 清理旧人物门槛冲突
- 发现依赖总览、独立终审任务和未登记旧写入路径仍引用旧 `>70%` 可见比例。统一终审任务为当前≤10%估算无人时长与≤30%特写时长；未登记旧路径明确阻断，不能继续消费旧门槛证据。
- 删除无调用方的旧70%视觉评分实现；历史审计方案注明已废止。历史记录和本机运行资料保留。

# 2026-10-20 — 商品返修回退闭环
- 调度器遇到本地计划门禁或语义终审给出 `REPAIRABLE` 时，带失败项回退到选段阶段；候选/范围证据保留，只重算选段及其下游，并以返修序号形成新的任务身份。
- 每个商品每次运行最多返修两轮（可用 `--repair-limit` 调整）；返修次数、失败项和恢复阶段写入商品游标。达到上限后保留 `REPAIRABLE` 检查点，后续运行可从最近问题继续。
- 单商品异常不再让调度器跳过后续商品组。此改动不代表已验证 Resolve 写入/回读，也不改变 package 模式下需先生成审核任务的行为。

# 2026-10-20 — 显式工作区与旧状态隔离
- 根目录 `PROJECT_STATE.json` 明确仅作历史记录，不再作为当前项目/批次输入；新窗口必须显式选择工作区并读取该工作区状态。
- `project_doctor.py` 未收到 `--workspace` 时仅报告仓库结构，明确提示未检查活动批次；Resolve 写入流程将当前工作区传给 doctor。
- 更新启动协议和项目概览，要求 Resolve 写入前使用同一工作区做只读身份检查。

# 2026-10-20 — 项目状态与批次生命周期约定
- 明确目标数据模型：每个 Resolve 项目一份长期状态，绑定稳定项目身份和素材/时间线；批次切片只存引用、商品范围、进度及派生产物。
- 约定完成批次保留 7 天后才可清理；清理默认预览，并保护项目记录、素材和 Resolve 工程。
- 新增生命周期文档；旧批次与历史数据不会被自动迁移或删除。

# 2026-10-20 — 项目级状态登记接入
- `init_project.py` 现按稳定 Resolve 项目 ID 创建或复用唯一项目级配置与状态；新工作区通过 `PROJECT_REF.json` 引用登记，并将本批范围和进度放入 `BATCH_STATE.json`。
- `RuntimePaths.load_state()/write_state()` 组合读取/分离写入项目绑定与批次状态；项目缓存和 worker 记录使用项目级目录，批次缓存按批次身份隔离。Project doctor 与 Resolve doctor 核对项目登记，支持记录的时间线稳定 ID检查。
- 旧工作区不自动迁移、不覆盖、不删除。新工作区可凭实时稳定 Resolve 项目 ID 复用项目记录；根目录旧状态和历史批次不会作为输入。

# 2026-10-20 — Resolve 项目名分流口播模式
- 新增严格项目名分流：`都督` 使用用户标记的粗略商品范围、单条 55–<60 秒视频及≤40%特写；`淘宝` 保留既有 45–<60 秒、目标三版及≤30%特写规则。
- Resolve 只读检查报告活动项目的分流结果；选段计划绑定分流策略，时长和特写审核按策略执行。双匹配或无匹配时阻断，不自动回退。

# 2026-10-20 — 连续字幕语义证据与返修继承
- 候选阶段保留原字幕连续文本，用来源引用标注禁用子句；语义判断不再因预清洗丢失相邻上下文，最终选入片段仍由本地禁用内容门禁拦截。
- 首轮选段回执新增商品卖点证据清单，逐字引用候选字幕；返修任务继承该清单，最终计划绑定其摘要，独立终审按同一语义依据复核。
- 固定交付入口的组合细审也产出并核验同结构卖点证据，供终审复用；都督模式可读取跨粗略标记字幕作上下文，但候选时间仍受范围约束。
- 都督时长从任意的 55 秒硬下限调整为目标约 60 秒、有效范围 >45 且 <60 秒；语义完整优先于贴近目标时长。
- 候选语义协议与旧产物不兼容；旧缓存保留，必须按新生产者版本重建受影响候选和审核。

# 2026-10-20 — 波形优先的局部切点返修
- 音频切点阶段保存20ms RMS、峰值和样本范围；在词语归属边界内先选静音/低能量帧，连续口播则选词界间最低能量帧。
- 连续口播回退只调整物理切点并明确标记为 ASR 词界估计，不重选语义、不改ASR词；无合法词间帧时保留上游结果并生成音频专属返修记录。
- 调度器识别音频返修请求后直接复用原选段与ASR重跑物理计划，不再调用语义选择 worker；生产者契约升级使旧物理计划失效。

# 2026-10-20 — 修复终审禁用词过滤器导入
- 候选禁用词过滤器改从 `semantic_evidence` 的定义处导入；终审契约升至 v8，使旧审核记录不会被误认为经过当前入口验证。

# 2026-10-20 — 冷启动标记范围候选与逐款交付门禁
- 冷启动候选直接由本轮用户确认的源范围生成；候选只收录范围内完整字幕，并绑定范围与Resolve preflight摘要，不再依赖全局标题定位或额外时长/关键词预筛。
- Resolve写入与回读能力及当前目标清空授权前移到字幕索引之前；视频完整指纹延迟到 preflight 通过后。
- 逐款交付器接受当前批次商品集合，在已登记目标时间线按顺序追加，每款记录回读状态，商品之间默认留25帧空白并校验。
# 2026-10-21 — 返修意见映射到源时间点
- 选段返修任务要求逐条落实终审核给出的源时间边界与方向，并复查相邻片段拼接，减少重复出现同一多余字/断句问题。
- 本批偏好：切点略紧以减少多余字词，同时保留完整卖点、有效音节和必要因果闭合。

# 2026-10-21 — 移除重复项目级语义终审
- 商品级 Codex 语义终审和本地硬门禁通过后，不再重复调用项目级 Codex 审核；移除项目层字符串相似度语义否决。项目级保留商品清单及跨商品物理素材重叠检查；Resolve 身份核对、写前门禁和写后回读继续执行。

# 2026-10-22 — 成片逐条视频间隔与待审清理
- Resolve 注册追加器在每条最终视频之间留 2 秒黑场，商品交界复用同一间隔；同一视频的素材段保持连续。
- 增加调度器布局修复事务：只从已核验的时间线回读快照识别并清除待审预览，保留正式源范围和顺序，备份后重排并逐段回读，不重新读取标题或调用语义审核。

# 2026-10-22 — 切点优先干净字间，不在连续发音里追低能量
- 有清晰静音边界时优先选择静音；无清晰静音且附近音频证据充分时，才在相邻ASR词界中点折中，不再因波形某一帧较低而移动到发音内部。
- 切点算法契约升级；旧局部音频计划必须重新生成，不能复用旧的低能量回退结论。

# 2026-10-22 — Resolve 项目 ID 是唯一身份认证
- 每个 Resolve 稳定项目 ID 唯一绑定一个长期工作区；启动时先识别当前打开的 Resolve 项目，再加载该项目的素材路径、依赖和已确认范围。
- 文件夹 inode 和显示名称不再参与身份判断；路径可用性、项目模式和时间线目标分别检查。
## 2026-10-22 — VFR source mapping default
- Use average-rate approximate source-frame mapping by default for VFR material. Do not require a complete exact frame clock before editing; record the approximation and verify the Resolve append.
- A visible person need not be the speaker. Mild lip-sync differences alone do not block a cut.
## 2026-10-22 — manual cutpoint feedback and product markers
- Read back user edits to the semantic preview, store per-project cutpoint observations, and avoid a blanket frame offset from a small sample.
- Replace only the batch's red ASR preview markers with normal blue product-start markers after a backup and readback; leave manually edited clips unchanged.

## 2026-10-22 — 字幕边缘文字就近对齐与局部 ASR 恢复
- 剪映字幕时间改作附近搜索中心，首尾字幕文字在局部 ASR 中寻找完整词界；允许少量识别错字，但不把波形低谷当作发音边界。
- 首次前后 2.5 秒窗口找不到或有歧义时，仅对受影响切点扩到前后 6 秒；语义 PASS 后的文字对齐失败保存局部回滚请求和诊断，不重新进行商品语义选择。
- 升级 ASR 范围及物理方案版本，旧切点计划不作为新算法的审核结果。既有 Resolve 时间线与人工调整片段不变。

## 2026-10-22 — 下一批真实剪辑中的范围与审核恢复
- 商品 7–9 的范围沿用全表初扫锚点并用当前连续字幕、换款口播和标题特征复核；调度器负责发布与后续剪辑。
- 字幕与 ASR 的品牌拼写、错别字及相近发音允许由更长的相邻口播确认；匹配失败时再退回靠近切点的短锚，不以字幕时间作为准确发音边界。
- 修复局部 ASR 返修检查点误传 ASR 为物理方案，以及已通过一次语义终审加本地硬门禁的证据被误拒为非最终审核的错误。
- 剪映字幕未经校准时，在邻近 ASR 词界以本机拼音核对同音/近音品牌与口播；句首连接词转写不同可由后续连续词组确认，仍以实际说话词首和局部声音证据修边。
- 局部重试从已审核粗选恢复卖点映射；已写入事务若回执文本解码失败，经调度器只读回读恢复，不重复追加。调度器子进程统一启用 UTF-8。

## 2026-10-22 — 每窗口两批的连续剪辑交接
- 每个 Codex 窗口连续剪两批、每批最多三个未完成商品；第二批写入并回读后，若仍有商品待剪，创建不继承对话历史的新窗口继续两批循环。
- 新窗口必须重新读取项目规则并按实时 Resolve 稳定 ID 认证绑定工作区；硬阻塞商品保留回滚点，不视为已完成或跳过。

## 2026-10-22 — 字幕首字替换的局部词界修复
- 字幕首字误写而后续口播高度吻合时，可将句首定位在完整 ASR 词的词首；不得因字幕从词内开始而截断发音。升级句子切点契约，旧物理方案自动失效并重算，后续仍须通过局部声音审核。
- 调度器恢复时检查已缓存物理方案及其返修记录的句子切点契约；算法升级产生的旧证明不能触发选段返工，应由调度器重算物理方案和下游审核。
- 独立 Codex 审核员偶发回传错误任务摘要时仅重试一次；只有摘要和裁决类型完全匹配才封存回执，第二次仍不符则继续阻塞。
