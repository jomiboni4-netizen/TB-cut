## 2026-10-10 — within-variant repeated-source guard
- Reject overlapping source-frame ranges inside a single final variant during cut-plan refinement and before Resolve writes. This catches repeated footage even when cross-variant checks pass.
- For the current batch, removed the duplicate product 3 variant 1 closing segment from the approved plan and Resolve timeline; the remaining variant duration is 49.96 seconds.

# Change Log

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

# 2026-10-14 — 单商品三版本联合规划默认目标

- 阶段A统一收集合法完整句群；阶段B基于全池联合分配来源时间与卖点，默认寻找3条内容不同、源时间零正重叠的成片，不能找到首条即停。
- 允许最终0–2条真正合格成片；不足3条须逐项记录素材、内容、时长、来源、语义或技术门禁的实际原因，不以低质量内容凑数。
- 当前工作簿运行时统一锁定 Node 24.19.0 与 artifact-tool 2.8.93；修复依赖测试只因测试夹具缺配置而误报通过的问题。
