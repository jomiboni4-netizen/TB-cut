# TB Cut V2 — Persistent Editing Rules

## Working communication

- Report progress in Chinese.
- 成片按每组三个商品执行“检查 + 修复”：发现问题或重点复核项后直接核实并修复，全部硬审核通过后写入达芬奇目标时间线，回读验证后发送 Mac 系统通知；不得只报告待修问题就结束本组。
- Keep advancing through safe, in-scope steps without pausing; stop only for required authorization or a hard blocker.

## Git and public repository workflow

- 一个 Resolve 项目只允许一份长期项目状态记录：`.cache/projects/<project_key>/PROJECT_STATE.json`。它绑定稳定 Resolve 项目 ID、素材目录、视频/字幕/标题路径及源/目标时间线；所有商品批次从该记录继承，不重复注册或复制绑定字段。仓库根目录 `PROJECT_STATE.json` 是旧记录，不能用于选择活动项目。
- 商品批次是短期切片，按 `.cache/projects/<project_key>/batches/<batch_id>/` 隔离，只记录项目引用、商品范围、进度及本批派生产物，不持有独立项目身份或另一套素材路径。完成时间写入批次状态；完成满 7 天后可由清理器预览并删除该批切片，项目状态记录和达芬奇项目不随批次清理。活动/未完成批次不得自动删除。
- 新商品执行必须显式指定当前批次工作区；若还设置 `TB_CUT_WORKSPACE`，两者不同则停止，仅有环境变量也不能隐式选择旧工作区。冷启动必须显式选择本次输入清单，并将经确认的项目绑定写入项目级记录。迁移到该结构完成前，旧版批次工作区状态只能作为兼容数据，不能称为已经满足“一项目一记录”。无 `--workspace` 的 project doctor 只检查仓库结构，不能报告当前项目就绪。历史 RUN、旧报告不能作为默认新任务输入。

## Resolve project editorial mode routing

- Editing mode is selected from the live DaVinci Resolve project display name by `config/project_editing_modes.json`; stable Resolve project ID still controls project identity and file/path binding. The display name is a workflow selector, not authentication.
- A name containing `都督` uses `dudu_marked_range`: the user's product start/end markers define approximate source bounds, not frame-accurate cuts. Choose and semantically shape one approximately one-minute product video within each marked range; target 55–less than 60 seconds, with `PERSON_CLOSEUP` at most 40% of total duration.
- A name containing `淘宝` uses `taobao_legacy`: retain the established 45–less than 60 second range, target three distinct variants when the full candidate pool supports them (allow fewer only after exhaustion), and cap `PERSON_CLOSEUP` at 30%.
- Both modes retain shared semantic closure, banned-content, synchronization, source-bound, person-presence, and Resolve validation rules. If the project name matches both keywords or neither keyword, stop before selection or write and report the routing reason; never guess or use the other mode as fallback.

- Keep every Codex change to public repository files in the Git working tree and reviewable with `git diff` or `git diff --cached`. Do not restore or overwrite changes merely to erase their diff.
- Develop on a task branch, not directly on the default branch. Use pull requests as the GPT review entry point when a GitHub remote is configured.
- At the end of each stage, report the branch, latest commit, changed files, tests, and PR number when available.
- Commit only reviewed public source, configuration, synthetic tests, and documentation. Keep actual project state, runtime caches, reports, plans, exports, credentials, source media, subtitles, title workbooks, and Resolve projects or backups local and ignored.
- Review the staged file list and scan staged contents for private data and credentials before committing or pushing. Stop the commit if a risk is found and report it.
- Do not rewrite history or squash unless explicitly requested. Do not invent a remote repository or create one without a specified destination.

## Temporary public review report

- At the end of every Codex task, update only `tmp/review_reports/LATEST_CODEX_REPORT.md`; do not create per-task historical report files. Include it in the Git diff whenever public files change. Runtime-only tasks may commit this summary to the current PR branch when authorized; this rule does not itself authorize pushing.
- Use exactly these report sections: `Task`, `Repo changes`, `Runtime changes`, `Validation`, `External actions`, `Blockers`, and `Git state`.
- Describe the task and actual public-file changes. Summarize runtime actions without copying runtime content. Validation must state results for project doctor, tests, preflight, Git diff checks, and other checks actually run; label unrun checks explicitly.
- External actions must include `MiMo called: yes/no`, `Resolve connected: yes/no`, and `export performed: yes/no`. Record unresolved blockers and the branch, HEAD, changed files, and staged/unstaged status. Identify the Git state as a snapshot taken before any subsequent commit, avoiding a self-referencing commit hash.
- Allow logical artifact paths, statuses, counts, test results, pipeline stages, rerun reasons, and SHA prefixes of 8–12 characters. Never include real absolute paths, credentials, environment file contents, raw subtitles/ASR, raw MiMo requests/responses, actual product titles, business data, Resolve project contents, or cache JSON bodies.
- This sanitized summary is the only public report exception. Actual state, caches, `reports/`, plans, exports, media, and Resolve backups remain local and ignored. Scan the report and staged diff before sharing.
- Do not delete or clear the report automatically. Only when the user explicitly says the report has been reviewed and may be cleaned may this single report be deleted or cleared; keep that cleanup in the Git diff. This explicit report-cleanup request is an exception to the default cleanup script rule and to recreating the report during the same cleanup task.
- End task replies with only these sections: `Report updated`, `Branch`, `HEAD`, `PR`, `Tests`, and `Runtime not committed`. Put detailed results and Git file status in the report.

## 标题数据与商品编号

### 工作簿依赖信任

- 日常工作簿操作信任 Codex 托管运行时解析到的 `@oai/artifact-tool` 包身份，不因 Node 或该依赖升级版本号而阻断；不得把 semver 白名单作为日常门禁。
- 每次运行仍确认包身份，并验证 `FileBlob.load`、`SpreadsheetFile.importXlsx`、`SpreadsheetFile.exportXlsx` 等实际需要的能力；真实工作簿读写继续执行结构、导出回读和非目标单元格保护检查。
- `strict-history` 仅用于明确的历史复现，保留精确版本和分发摘要。禁止通过改版本号或伪造兼容记录绕过能力检查。



- 标题来源仅为当前 `PROJECT_STATE.json.title_path` 指定的工作簿；正式生成器不以旧 `titles.json` 为输入。
- 当前生成路径要求工作簿恰有一个可见工作表；第一行必须各有一个精确命名的“编码 ID”和“标题”表头。`sheet` 保存实际读取的工作表名，不使用历史缓存名称。
- 第一行是表头，商品编号按工作簿原数据行位置确定：`product_index=Excel物理行号-1`，不得因空标题而压缩编号。
- 空标题行不剪辑，但必须记录原编号、工作表行号及可用编码 ID 的占位；完全空白数据行也保留编号占位。有标题但缺少编码 ID 时生成失败；所有非空编码 ID 必须唯一。
- 默认启用 `batch_overrides.skip_blank_titles=true` 和 `batch_overrides.preserve_blank_row_indices=true`，两项均纳入输入身份指纹。占位不进入剪辑商品列表。旧压缩编号成果按编码 ID 对照原行号，禁止直接用旧编号匹配；旧编号派生方案须标记过期后重新核验。
- `encoding_id` 保持字符串语义。文本编码原样保留，前导零不得丢失；拒绝编码前后空白。数字编码仅接受可无损表示的非负整数（少于 16 位），允许 General、文本格式或纯零填充格式；按零填充宽度保留前导零。精度、格式或类型不明确时拒绝生成，不猜测业务 ID。
- 为避免隐式取值，当前生成器拒绝合并单元格、公式及隐藏行列，不自动展开或忽略这些结构。
- 标题工作簿顺序定义商品编号，但不定义直播中的商品时间边界；范围定位仍以真实链接锚点和时间轴规则为准。

## 项目输入登记

- 素材文件夹、字幕来源、标题工作簿和 Resolve 项目/时间线按整个项目登记一次。后续窗口和商品批次继承经身份核验的项目配置，仅对缺失、改变或身份不匹配的输入补充确认；不得仅凭项目名称匹配，也不得把旧状态自动认证为新登记。切换项目使用独立工作区。

## Final deliverables

- Target: up to 3 finished variants per product.
- Normal expectation is 3; if only 2 or 1 can pass all hard rules, output fewer.
- Stage B must examine the full verified sentence-group pool once and jointly allocate content and nonoverlapping source time toward 3 distinct variants; finding the first valid variant does not end the search. If fewer than 3 survive, record a concrete reason for each missing variant. Zero variants are allowed when none pass.
- Never fabricate a third variant by repetition or filler.
- Each final variant must be strictly `> 45.0s` and `< 60.0s`.
- Recommended internal assembly target: roughly 48–56s to leave cut-point margin.

## Candidate pool (“粗选”) definition

- Candidate pool items are useful source segments before final assembly, not rough finished videos.
- Candidate segments have no minimum or maximum duration.
- A candidate may be a few seconds or longer than 60 seconds.
- Candidate selection asks only: “Is there useful product-selling or wearing-experience content here?”
- Codex performs all semantic review and selection, including choosing the most meaningful subranges from long candidates. Do not call MiMo or local semantic-review models for this work unless the user explicitly changes this default.
- When the user explicitly authorizes MiMo semantic review, use `mimo-v2.6-pro`; it replaces the deprecated `mimo-v2.5-pro` identifier in project requests.
- Do not force candidate blocks to resemble final duration.
- For text analysis, first scan continuous subtitles for product attributes and create one or more broad semantic windows. Exact product start/end seconds are not required for this rough locator, and no rough window is a `product_ranges.json` certification.
- Excel product number and encoding identify the catalog item; they do not identify its live position. Link numbers are auxiliary mentions only and have zero boundary/ranking authority. Use category, fabric, cut, color, structure and surrounding speech to locate formal explanations, teasers, outfit mentions and later supplements.
- Compare title, spoken claims and current product evidence. If similar products, stale links, multi-SKU pages, or outfit references conflict, retain candidate products and mark `PRODUCT_UNCERTAIN`; do not auto-admit those sentences to a product pool. A confirmed range for physical editing remains a separate, explicitly validated artifact.
- Accepted ranges and human boundary manifests cover exactly the current batch's `batch.product_ids`; each selected product still needs human-confirmed source-local start and end points. Other title rows are outside the batch and do not block it.
- Apply clear banned-content cleaning across the complete range, then admit remaining useful content broadly. Candidate pool is wide-in; final output is strict-out.

## Allowed content

Keep only content directly useful for selling/understanding the current product, including:
- product selling points
- material/fabric
- fit/silhouette
- construction/craft
- wearing feel/comfort
- on-body effect
- directly relevant styling explanation

## Banned content

Delete locally before MiMo whenever possible:
- price/amount
- coupons/discounts/subsidies/promotions
- red packets/lotteries/giveaways
- link-number talk / “which link” talk
- greetings / welcoming viewers
- casual chat
- backend/process/setup talk
- focus/camera/setup chatter
- unrelated products
- empty filler
- purely promotional urgency without product value

## Semantic closure

Every final variant must stand alone and remain logically complete after cuts.

Hard checks include:
- Enumeration closure: “three colors / two points / first / another” must be fully fulfilled, or remove/rewrite the lead-in by choosing a different complete source range.
- Cause-effect closure: “because…” requires its result; “so…” requires an understandable cause/context.
- Condition-result closure: “if / when / as long as…” requires the consequence.
- Contrast closure: “but / however / instead…” must retain both sides needed for meaning.
- Reference closure: “this / it / this one / this fabric” must have an identifiable antecedent inside the finished variant.
- Promise closure: “later I’ll show / next look at / another one” must be fulfilled or removed.
- Independent opening: the first retained sentence cannot depend on deleted previous context.
- Complete ending: do not cut away the conclusion just to hit duration.
- Do not start a retained segment with dangling connectors such as “然后/因为/但是/所以/而且/另外/接着/同时” unless the preceding dependency is retained in the same continuous semantic unit.
- Connector words trigger a dependency check, not automatic rejection. Normal Chinese orders such as “结果，因为原因” and “结论，因为解释” are valid when independently understandable and complete.

## Cross-variant uniqueness

For variants of the same product:
- Physical source-frame overlap must be zero.
- No source segment may be reused in another variant.
- Even with different source time, identical or highly similar speech/content is a hard duplicate and must be removed.
- Prefer different selling-point emphasis across variants.

## Visual/person rule

- The subject-presence rule is independent of facing direction, identity, full-body visibility, and the person's pixel share of the frame. Any reliable Apple Vision person rectangle, face rectangle, or body-pose signal meeting the presence confidence settings in `config/vision_rules.json` counts as a person. Small or partial people are accepted; clothing-only/product-only footage and empty frames do not count.
- `PERSON_CLOSEUP` is a separate shot category, identified by a large face rectangle or a human rectangle near frame height. Enforce the project-mode cap: at most 40% for `都督`, at most 30% for `淘宝`. Small people in the frame are not automatically close-ups. `PERSON_VISIBLE` covers other confirmed person footage; `HOST_VISIBLE` remains a legacy alias and does not mean a registered or identity-verified host.
- Allow at most 10% estimated no-person duration in each final variant. Sample 10 equal time strata on the rendered variant, independent of its physical clip count. If a stratum midpoint is classified as no-person or clothing-only, refine that stratum with 10 evenly spaced samples. Its estimated no-person share is the fraction of refined samples without a person multiplied by that stratum's 10% weight. Sum across all flagged strata; multiple individually acceptable gaps can exceed the total 10% cap.
- A refinement stratum is `min(6 seconds, 10% of the rendered variant duration)`, centered on the initial sample and clipped to the variant bounds. Final variants are under 60 seconds, so this equals one 10% stratum and never exceeds 6 seconds. All ten initial person samples mean 0% *sampled* no-person time. Exactly 10% estimated no-person time is allowed; a larger estimate is repairable.
- Unresolved positive signals are `UNCERTAIN`; detector execution errors are `BLOCKED`, never no-person evidence. Never require face registration, person tracking, host identity matching, or Qwen. Independently enforce the close-up cap selected by the project mode.
- This is a deterministic engineering sample audit, not framewise certification: it cannot prove that no unsampled frame is personless. Report `strict_framewise_certified=false`; do not describe sampled PASS as a mathematical guarantee over every frame.
- Classifier protocol, geometry configuration, detector binary, media identity, and exact final plan are part of the evidence identity. A change to cut points or the classifier invalidates the prior audit. Old classifier results cannot be reused as current evidence.
- Long close-up/no-person sections should be removed when they are not required for semantic continuity.

Local visual classifier:
1. Use Apple Vision person, face and pose observations to classify any reliable person presence, separately marking close-ups.
2. Identity recognition and Qwen are not part of this audit. Unresolved visual evidence remains `UNCERTAIN`; do not escalate it to an identity check.

Apple Vision person detection must run outside the Codex filesystem sandbox on this Mac. An ANE model-load failure or CoreVideo `-6662` / `CVPixelBuffer` allocation failure is an execution-environment error, not a no-person result; stop the audit and rerun outside the sandbox. Rebuild the cached probe whenever its Swift source is newer, and fail closed with the underlying Vision error instead of recording zero-person samples.

Suggested labels:
- `person_visible`
- `person_closeup`
- `clothes_closeup`
- `no_person`
- `uncertain`

FFmpeg/ffprobe handle sampling, timing, segment extraction and ratio aggregation; the local vision model supplies visual classification.

## Audio/cut-point rule

- Codex first freezes the complete semantic combination from the current subtitle index and source context. Local ASR must not rescan full selected clips or broad candidate ranges after that decision.
- Local ASR is for checking only the proposed opening and ending cut neighborhoods for swallowed, partial, or misaligned words. Start with a small window around each cut (normally no more than 2.5 seconds on either side); expand only that cut's window when the result is ambiguous, and record the reason.
- Use subtitle cue timing, local cut-neighborhood ASR, pauses/energy, and source frame boundaries together to validate clean starts and ends. ASR does not choose the content, rewrite the Codex combination, or certify semantic closure.
- Never hard-cut a half word or half sentence.
- Do not transcribe the body of a selected clip solely to obtain word alignment. If an edge check finds a likely swallowed word, repair or move that edge and recheck only the affected neighborhood.
- Semantic timecodes are rough bounds. If a natural sentence ending lies after the rough end, extend to the sentence boundary first; if this would exceed 60 seconds, remove or replace another low-value complete segment. Never meet duration by cutting a sentence tail.
- Tail closure risks include unfinished cause, condition, contrast, enumeration, reference, promise, and speech that continues as the same sentence for the next 1–3 seconds.

## Repair and exhaustion

- 成片检查提速默认顺序：Codex先用派生字幕索引和原源上下文冻结版本组合；之后只对最终选段的首尾切点做局部ASR核验，再做画面复核。明显将被替换的片段不提前做昂贵检测。失败后只重新计算受影响切点附近的素材窗口，最终所有硬审核仍须通过。
- 局部 ASR 只合并相邻或重叠的切点窗口，每批仅使用一个按需加载的模型实例；缓存齐全时不加载模型，不通过降低识别质量提速。窗口总量和扩窗原因纳入运行记录。
- 人物检测可复用 `scripts/review_compute_cache.py` 的物理范围缓存，键必须包含素材身份、大小/修改时间、精确检测范围及检测器内容指纹。说明、语义角色和时间线位置变化不要求重复计算相同源画面，但必须重算当前方案审核与完整方案摘要；缓存检测数据不代表写入授权。旧无指纹缓存不可直接提升为新缓存。
- 每轮记录新计算量、缓存命中量和阶段耗时；只报告实测整轮提速，不将减少窗口数量当作实际耗时降幅。

- Variant states are `PASS`, `REPAIRABLE`, and `EXHAUSTED`.
- Duration misses, incomplete openings/endings, closure gaps, more than 10% estimated no-person duration, overlap, duplication, poor visuals, weak seams, audible or transcribed stumbles, and low-value blocks are repairable by default.
- Repair must return to the complete remaining candidate pool, lock already-passing variants, record attempts and changes, and retry up to the configured limit.
- Only a complete-range scan, complete candidate-pool search, and exhausted repair limit may reduce the target from 3 to 2, 1, or 0.

## Fixed execution and P0 time budget

- Use the versioned `delivery_pipeline.py` stages and local JSON recipes/rules for routine product work. A product-specific selection, correction, manual frame confirmation or exception is recorded in its workspace JSON; do not create or edit Python code for an individual product.
- P0 delivery and data integrity: process products independently. As soon as a product's required hard audits pass, append it to the explicitly registered target timeline, immediately read it back and verify clip identity, order, duration, source ranges and audio/video linkage; persist a `written_and_verified` checkpoint before starting the next product. A batch-level review must never delay an already passing product. After requested products are written or explicitly blocked, reorder only this run's product blocks by ascending workbook physical-row `product_index`; preserve internal clip order, synchronized media, markers and unrelated/manual material, then re-read the affected timeline.
- P0 target readiness: before expensive selection or media analysis, verify the chosen workspace, current Resolve project/timeline identities, source and target identities, fixed pipeline entrypoint, registered downstream consumer integration, and write/readback capability. A structural doctor PASS alone is not runtime integration evidence. If integration cannot be invoked, report the exact blocker before broad media, ASR or vision computation.
- P1 progress and recovery: maintain a durable per-product ledger with current stage, last checkpoint, elapsed time by selection/compute/repair/wait/write/verification, and next action. `REPAIRABLE` must create a structured repair request (`failed_stage`, exact issue/evidence, `rollback_to`, `invalidate`, `preserve`, attempt number) and re-enter the nearest valid upstream stage; it must not return as a terminal product result. Recompute only that stage and its dependents, preserving valid upstream inputs and completed products. Environment faults use a bounded fallback tool/runtime and then rerun only the failed stage. True missing-input/authorization faults remain resumable blockers with a stored resume point, not exhausted products. After the configured repair/fallback limit, mark `PAUSED_RETRYABLE` or `EXHAUSTED`, preserve every artifact, report the next action, and continue independent products when safe. A repeated identical failure must not rerun unchanged inputs without a changed repair request or runtime identity.
- P2 semantic review: judge retained speech's actual meaning in original context, not connector keywords, transcript fragments, or subtitle cue boundaries. For each opening, transition and ending, compare retained words with minimum surrounding context and decide whether the cut is naturally understandable, complete and product-relevant. Preserve sensible clauses containing “因为/所以/但是”; repair only real dangling dependencies, meaning changes, or unusable cuts. Record concise reasons; do not loosen genuine hard rules to force delivery.
- P3 project continuity: persist stable project/media/subtitle/workbook/Resolve timeline identities and batch progress. Later conversations should validate identity changes and resume from the ledger, not silently select stale roots or repeatedly request already confirmed configuration.
- P4 evidence correctness: freeze code/rule versions during production; a shared implementation change enters a separate development/validation step and invalidates only dependent evidence. Treat media-embedded audio/video offsets and user-declared visual placeholders as source/editing facts, not semantic defects. Compare streams using separately validated source clocks; unequal source-frame numbers alone do not prove an editorial error. Store intentional repairs in the edit manifest.
- P5 efficiency and reporting: log stage durations and cache hits, optimize only from measured bottlenecks, and report edited/written/verified/exported as separate states. Warm-run time targets are measurement goals, not grounds to skip required checks or claim unmeasured speedups.
- Change shared code only to fix a reusable workflow defect. Add synthetic regression coverage, bump the affected contract/producer identity, and record the behavior in `CHANGELOG.md` before returning to product work. Preserve runtime evidence and user diffs.
- A content correction invalidates only dependent evidence: regenerate the exact plan and semantic task; recompute audio boundaries only for changed clips; rerun the final-variant person audit. Reuse unchanged ASR windows, source clocks, frame classifications, project rates and timeline registration by identity. Do not rerun a whole source or candidate pool for a local change.
- Target approximately five minutes per product for a warm, non-blocked execution. Record selection/review, local compute, repair and external wait separately. Report measured elapsed time, cache hits and unassigned wall time; do not claim the target was met when mandatory review, repair, first-time computation or authorization wait exceeded it.

## Physical clip normalization

- Before Resolve write, merge adjacent clips from the same source when timeline and source are contiguous within 1–2 frames and no source content is intentionally removed.
- Candidate IDs, semantic blocks, pauses, and roles do not create physical cut points. Preserve multiple roles in audit metadata on the merged clip.

## Resolve

- 所有连接、检查、写入和导出入口统一使用 `scripts/resolve_connection.py`，不得临时只调用 `scriptapp("Resolve")` 后就宣布未连接。Codex 在此 Mac 上于沙盒外运行 `python3 scripts/resolve_connection_doctor.py --root . --workspace <本次明确选定的工作区>` 做只读检查，再判断项目是否就绪。
- 重连每次重新获取 API 对象并检查可读性；默认依次尝试本机、回环地址和当次 `en0` / `en1` 地址，最多四轮，不固定旧 IP。设置 `RESOLVE_HOST` 时尊重明确主机，不回退到其他网络主机。不得缓存跨达芬奇进程生命周期的对象。
- 达芬奇关闭时无法连接；打开但尚未就绪时自动重试。检查进程默认二十秒超时；连接成功后仍验证当前项目、源/目标时间线。仅重试连接，绝不自动重放时间线写入。
- 连接诊断保存在所选工作区忽略的 `.cache/resolve_connection_status.json`。重连失败时先区分程序未运行、API 不可用、项目未打开或不匹配、检查超时，再说明实际阻塞；未经完整回退尝试不得要求用户改偏好设置或找旧记录。新窗口以本规则和统一脚本为准。
- Automatically write only after all hard audits pass.
- Source timeline is read-only.
- Resolve project/source timeline/target timeline are user-specified per project/batch state.
- During finished-variant repair, if a complete ending would collide with the following finished variant, the user authorizes shifting the subsequent finished clips later as one synchronized block, with their associated markers shifted by the same frame offset. Preserve clip order, source ranges, inter-variant gaps, audio/video synchronization, and properties; back up before writing and verify the shifted clips and markers afterward. Recheck pending placement plans against the latest timeline under this policy.
- Validate before write, back up when appropriate, write, then validate after write.
- Final reports include product-range and candidate-pool coverage, repair attempts/status, duration, host ratio, closure, overlap/duplication, meaningless-cut count, and seam-tail risk count.

## Bounded Codex correction rounds and real-world test gate (2026-10-12)

- A correction cycle is one reviewable, committed code-and-synthetic-test iteration with a defect list, diff, test results, remaining risks and round number. Documentation-only updates do not count as a correction cycle.
- Complete at least 3 and at most 5 Codex correction cycles before requesting the user to run a real-media / Resolve acceptance test. Synthetic, unit and dry-run checks may run during correction cycles. Never repeatedly add rounds without a fixed stop: after cycle 5, stop changing code and provide a test-ready candidate or explicitly report blockers for the user's decision.
- A real-world test is a separate user-authorized stage. Do not read real media, call remote services, write Resolve or export merely because a correction cycle or this rule was saved.
- P0: After physical cut-point refinement, check every retained segment's opening, middle seams and ending against the final speech, including clipped syllables/words, dependencies and actual final clip order; original selection PASS never substitutes for final Codex semantic PASS. Bind the final review to a digest of exact source ranges, order and audit inputs. Any change invalidates final approval and reruns impacted duration, content, semantic, person-ratio, overlap and synchronization checks.
- P0: Favor preserving independently intelligible cause/effect sentences over mechanically retaining or deleting words such as “因为/所以”. Removing “因为” or a redundant lead-in may be appropriate only if the remaining speech is naturally complete and the actual audio cut has no eaten syllables; check meaning rather than keyword presence.
- P1: Codex is the default semantic decision-maker for this correction effort. Treat MiMo as an optional, explicitly authorized comparison/assistive service, not as a proven replacement for final semantic review. Record failure cases using privacy-safe synthetic or locally approved excerpts; do not attribute quality differences to a model without matched-input evaluation.
- P1: Optimize elapsed time using measured per-stage timing, repeated-input caching, batched/localized ASR and cut review, and adaptive person sampling; report baseline and after timings, quality regressions and cache hit counts rather than assuming most time is text processing.
- P0: Use the fixed two-stage final-variant person audit defined in `config/vision_rules.json`; do not use per-clip 16-points-per-minute rounds, a 70% host threshold, or an any-no-person hard veto. Its exact 10% aggregate no-person duration and 10-point local refinement rules are specified in the Visual/person rule above.

## One Resolve project, one path registration and isolated resumable processing (2026-10-12)

- The user-provided folder/path describes **one entire DaVinci Resolve project**, not one product, one Codex window or one temporary batch. Bind the confirmed project workspace, media/subtitle/title inputs, and source/target timelines only once per project; reuse that registered configuration in later windows and product groups. Ask only for genuinely missing/changed inputs and explicitly validate any update. Never silently switch projects, invent paths or request the same complete folder repeatedly.
- Separate the stable project identity (project configuration and confirmed media source set) from mutable processing runs/batches and from per-product/variant/stage outputs. Persist a project-scoped manifest and resumable product progress; namespace or content-address artifacts to prevent product-number, run and global-report filename collisions. Do not reuse a cache merely because its filename or product number matches.
- Cache lookup must validate relevant input, source media, full accepted product range, semantic/physical plan revision, rules, code/pipeline stage version, model/detector version, and producer dependencies. Keep independent project-level indexes, product-level derivations, content-addressed ASR/vision samples and run-specific aggregate reports; reuse unchanged compatible upstream work where proven. Missing/legacy/stale provenance fails closed; do not delete or re-attest old runtime data to conceal conflicts.
- Use cooperative locks and immutable/staged publication with post-write identity validation for concurrent workers. Each stage records its inputs, outputs, digest, status and parent dependencies. Crash/retry must be idempotent; never rewrite a passing final plan with an earlier candidate or double-append a Resolve marker/clip. Existing final timeline must be read back before resume/write.
- Accept whole-project editing requests as the **complete target product set**, but execute automatically in quality-gated small product groups (initial proposed size 2–3; configurable after measurement). Do not silently lower the standard, skip products, declare all done after processing a subset, or ask the user to restate the root folder for each group.
- For each group: verify complete accepted boundaries/candidates, perform Codex selection, refine and independently review final audio/semantic seams, check every applicable hard rule, and record immutable PASS/REPAIRABLE/BLOCKED checkpoints before eligible Resolve write. Proceed to the next group only after quality gate passes; a blocker must be reported with affected products, dependencies and safe resumption instructions. Preserve completed valid groups and revalidate on upstream change rather than recomputing the entire project.
- Project-wide completion requires an explicit per-product/per-variant ledger and a final cross-group consistency pass (source overlaps and relevant duplicates, durations, semantic review revisions, host ratio, cuts, audio/video source synchronization, and Resolve readback). Mark "edited", "written", "exported" and "verified" separately. Never claim the whole project completed from one group PASS.
- The 3–5 Codex correction-cycle limit is a **code-development cap across these requirements**, not a limit of product groups or normal per-product repair attempts. The full-project processing quality gate must not be bypassed to fit a correction round.

## 通用句首与切点约束（2026-10-18）

- 所有新商品使用sentence-owned-edges-v2-frame-estimate：Codex结合原素材前文选择独立完整句首，本地声音修边不能改选句子或越入未保留的前后词。静音、字幕分行与拼接后ASR均不是独立句首证明。
- 语义独立、残句、依赖关系由Codex结合原源上下文判断；中段有承接词不自动拒绝。ASR与帧格半帧以内的取整误差只记为定位估计，不据此否定句子或声称精确音素认证。显著词时间冲突及真实声音风险仍待修复；禁止改写原ASR时间或无限扩边伪造通过。终审语义角色不重复要求真人试听、画面或本机检测已负责的证据。
- 旧切点方案及旧终审不兼容新协议，需重建受影响方案与审核；源字幕索引、模型权重和符合身份校验的源级证据不因本次逻辑升级自动作废。
- 新商品试跑用事件账记录阶段起止、失败、等待与未归因时间，代码开发耗时与商品运行耗时分开。

## 正常剪辑与开发分离（2026-10-19）

- 句子选择后的正常执行优先使用 `scripts/delivery_pipeline.py` 固定入口及工作区JSON配方；只更换输入、商品、范围证明和Codex选段，不为每个商品生成临时Python组装/写入/回读脚本。
- 当前可用切点算法保持 `owned-gap-local-audio-v1`。发现具体新缺陷才进入单独计时的开发阶段、记录开发与返工时间并验证受影响阶段；不在正常剪辑中顺手重构或放宽门禁。
- ASR、源级帧时钟、逐帧人物证据及每段声音按实际依赖复用；变化撤销受影响审核，精确方案的最终语义和人物汇总必须重新绑定。旧trial与旧成品证据只读保留，不自动盖成新入口的当前证据。
- 分开记录compute、editorial、development、waiting、verification和返工；并行/嵌套耗时不相加当总耗时，未计时的空档保留未归因。没有真实对照测量不承诺提速。
- 固定入口的新建时间线模式只在当前用户明确要求新建时使用；不继承上一任务的一次性文本外发或草稿豁免授权，不解锁其他旧写入入口。

## 循环测试时间线（2026-10-19）

- 测试剪辑默认复用本项目已登记稳定ID的“测试”时间线，不按商品反复新建时间线。每次替换前全部门禁通过、保存旧测试线快照并验证项目备份，然后仅清空该测试线的片段和标记再写入。
- 素材、正式成片及其他历史/人工时间线始终只读。测试线名称与稳定ID必须同时匹配当前项目登记，丢失、重名或误指向源/正式目标时停止，不猜测清空对象。新工作区继承同一项目的明确测试线绑定；不继承一次性文本外发授权。

## 组合语义前移与项目帧率固定（2026-10-19）

- 新组合在声音修边、人物计算前，由主Codex按 `ASSEMBLY_TASK.json` 逐段审读实际保留原词、删除首尾及最小原源上下文，明确检查句首主语/对象、段内未完动作/条件/列举、句尾闭合、跨片接缝依赖及跨版信息重复。记录逐项具体理由；上下文不足先扩查，不用关键词或脚本自动授予语义PASS。选词、上下文或组合改变后旧记录失效，独立最终语义终审仍保留。
- 每个项目首次读取时通过统一Resolve连接器读取项目、全部时间线及素材帧率，登记项目稳定ID、时间线ID、输入摘要和素材身份。后续商品/窗口继承显式同项目登记，只核对身份，不重复探测或设置帧率。素材容器标称/平均帧率均记录，不能拿平均值差异代替逐帧时钟证明；实际选中素材继续复用身份匹配的完整帧时钟。
- 写入前只读核对项目/时间线帧率是否与登记一致；外部变化、真实源/目标帧率不兼容即在清空前停止，不自动改帧率或转换。新建时间线继承项目帧率并验证；既有时间线不调用帧率设置。源起止帧的精确双证据回读继续保留，不能把接口取整误差说成帧率不一致。
- 新契约仅用于未来自动执行；旧成品、人工修改和旧审核证据原样保留，不用新门禁给旧成品重新盖章。
