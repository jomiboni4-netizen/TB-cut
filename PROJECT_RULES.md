# TB Cut V2 — Persistent Editing Rules

## Working communication

- Report progress in Chinese.
- 成片按每组三个商品执行“检查 + 修复”：发现问题或重点复核项后直接核实并修复，全部硬审核通过后写入达芬奇目标时间线，回读验证后发送 Mac 系统通知；不得只报告待修问题就结束本组。
- Keep advancing through safe, in-scope steps without pausing; stop only for required authorization or a hard blocker.

## Git and public repository workflow

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

- Final `host_visible_duration / final_duration` must be strictly greater than 70%. `HOST_VISIBLE` remains a compatibility label meaning a prominent visible person subject; it does not mean a registered or identity-verified host.
- Count a clear full-body or half-body person in front of the camera, whether standing or seated, when they present clothing or explain the product. A sufficiently clear, prominent face or body pose can independently establish the person as a visible subject; identity consistency across shots is irrelevant.
- Clothing/product close-ups without a prominent person, empty shots, and isolated hands/arms or other small body parts do not count. Use `UNCERTAIN` when the person-subject decision cannot be made reliably. Never require a face registry, person tracking, a host identity match, or Qwen to count a visible person.
- Apple Vision human rectangles, face rectangles, and pose points feed the configurable geometry thresholds in `config/vision_rules.json`. A positive human signal below all subject thresholds remains non-counting and uncertain; detector execution errors are `BLOCKED`, never no-person evidence.
- Sample each final physical plan with 16 initial points per minute, stratified over time and clips; add distinct samples for up to five passes. Weight by covered source time and apply the conservative lower-bound gate. `PASS` requires that lower bound to be strictly above 70%; after the cap, a measured ratio at or below 70% is `REPAIRABLE`, unresolved samples are `UNCERTAIN`, and decode/model/runtime failures are `BLOCKED`. Do not rescan forever or lower the threshold.
- Classifier protocol, geometry configuration, detector binary, media identity, and exact final plan are part of the evidence identity. A change to cut points or the classifier invalidates the prior audit. Old classifier results cannot be reused as current evidence.
- Long close-up/no-person sections should be removed when they are not required for semantic continuity.

Local visual classifier:
1. Use Apple Vision person, face and pose geometry to classify whether a prominent person is visibly in frame.
2. Identity recognition and Qwen are not part of this audit. Unresolved visual evidence remains `UNCERTAIN`; do not escalate it to an identity check.

Apple Vision person detection must run outside the Codex filesystem sandbox on this Mac. An ANE model-load failure or CoreVideo `-6662` / `CVPixelBuffer` allocation failure is an execution-environment error, not a no-person result; stop the audit and rerun outside the sandbox. Rebuild the cached probe whenever its Swift source is newer, and fail closed with the underlying Vision error instead of recording zero-person samples.

Suggested labels:
- `host_visible`
- `clothes_closeup`
- `no_person`
- `uncertain`

FFmpeg/ffprobe handle sampling, timing, segment extraction and ratio aggregation; the local vision model supplies visual classification.

## Audio/cut-point rule

- Codex chooses semantic ranges; local tools choose final physical cut points.
- Use SRT + local ASR/VAD + pauses/energy + frame boundaries for clean starts/ends.
- Never hard-cut a half word or half sentence.
- Local ASR should be windowed around selected ranges, not full-media retranscription by default.
- Semantic timecodes are rough bounds. If a natural sentence ending lies after the rough end, extend to the sentence boundary first; if this would exceed 60 seconds, remove or replace another low-value complete segment. Never meet duration by cutting a sentence tail.
- Tail closure risks include unfinished cause, condition, contrast, enumeration, reference, promise, and speech that continues as the same sentence for the next 1–3 seconds.

## Repair and exhaustion

- 成片检查提速默认顺序：先用派生字幕索引和轻量硬规则确定语义修复方案，再集中进行局部 ASR 与画面复核；明显将被替换的片段不提前做昂贵检测。失败后只重新计算受影响的素材窗口，最终所有硬审核仍须通过。
- 局部 ASR 合并同素材重叠窗口，每批仅使用一个按需加载的模型实例；缓存齐全时不加载模型，不通过降低识别质量提速。
- 人物检测可复用 `scripts/review_compute_cache.py` 的物理范围缓存，键必须包含素材身份、大小/修改时间、精确检测范围及检测器内容指纹。说明、语义角色和时间线位置变化不要求重复计算相同源画面，但必须重算当前方案审核与完整方案摘要；缓存检测数据不代表写入授权。旧无指纹缓存不可直接提升为新缓存。
- 每轮记录新计算量、缓存命中量和阶段耗时；只报告实测整轮提速，不将减少窗口数量当作实际耗时降幅。

- Variant states are `PASS`, `REPAIRABLE`, and `EXHAUSTED`.
- Duration misses, incomplete openings/endings, closure gaps, 65–70% host visibility, overlap, duplication, poor visuals, weak seams, and low-value blocks are repairable by default.
- Repair must return to the complete remaining candidate pool, lock already-passing variants, record attempts and changes, and retry up to the configured limit.
- Only a complete-range scan, complete candidate-pool search, and exhausted repair limit may reduce the target from 3 to 2, 1, or 0.

## Physical clip normalization

- Before Resolve write, merge adjacent clips from the same source when timeline and source are contiguous within 1–2 frames and no source content is intentionally removed.
- Candidate IDs, semantic blocks, pauses, and roles do not create physical cut points. Preserve multiple roles in audit metadata on the merged clip.

## Resolve

- 所有连接、检查、写入和导出入口统一使用 `scripts/resolve_connection.py`，不得临时只调用 `scriptapp("Resolve")` 后就宣布未连接。Codex 在此 Mac 上直接于沙盒外运行 `python3 scripts/resolve_connection_doctor.py --root .` 做只读检查，再判断项目是否就绪。
- 重连每次重新获取 API 对象并检查可读性；默认依次尝试本机、回环地址和当次 `en0` / `en1` 地址，最多四轮，不固定旧 IP。设置 `RESOLVE_HOST` 时尊重明确主机，不回退到其他网络主机。不得缓存跨达芬奇进程生命周期的对象。
- 达芬奇关闭时无法连接；打开但尚未就绪时自动重试。检查进程默认二十秒超时；连接成功后仍验证当前项目、源/目标时间线。仅重试连接，绝不自动重放时间线写入。
- 连接诊断保存在忽略的 `.cache/resolve_connection_status.json`。重连失败时先区分程序未运行、API 不可用、项目未打开或不匹配、检查超时，再说明实际阻塞；未经完整回退尝试不得要求用户改偏好设置或找旧记录。新窗口以本规则和统一脚本为准。
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
- P1: Replace the fixed 0.5-second person-detection cadence with stratified, adaptive sampling on final clip duration (initial default N=16 points per 60 seconds, proportional for shorter/longer clips). Additional disjoint N-point rounds focus on uncertain/near-threshold results, with up to five sampling passes and a conservative pass gate strictly over 70%. Never count vision exceptions/uncertain frames as confirmed visible-person time, and never treat an inconclusive sample as PASS. Sample across all constituent clips, record points and detector version, and bind results to the exact final plan. Validate synthetic edge cases and separately report authorized local real-frame observations. No identity model is required.

## One Resolve project, one path registration and isolated resumable processing (2026-10-12)

- The user-provided folder/path describes **one entire DaVinci Resolve project**, not one product, one Codex window or one temporary batch. Bind the confirmed project workspace, media/subtitle/title inputs, and source/target timelines only once per project; reuse that registered configuration in later windows and product groups. Ask only for genuinely missing/changed inputs and explicitly validate any update. Never silently switch projects, invent paths or request the same complete folder repeatedly.
- Separate the stable project identity (project configuration and confirmed media source set) from mutable processing runs/batches and from per-product/variant/stage outputs. Persist a project-scoped manifest and resumable product progress; namespace or content-address artifacts to prevent product-number, run and global-report filename collisions. Do not reuse a cache merely because its filename or product number matches.
- Cache lookup must validate relevant input, source media, full accepted product range, semantic/physical plan revision, rules, code/pipeline stage version, model/detector version, and producer dependencies. Keep independent project-level indexes, product-level derivations, content-addressed ASR/vision samples and run-specific aggregate reports; reuse unchanged compatible upstream work where proven. Missing/legacy/stale provenance fails closed; do not delete or re-attest old runtime data to conceal conflicts.
- Use cooperative locks and immutable/staged publication with post-write identity validation for concurrent workers. Each stage records its inputs, outputs, digest, status and parent dependencies. Crash/retry must be idempotent; never rewrite a passing final plan with an earlier candidate or double-append a Resolve marker/clip. Existing final timeline must be read back before resume/write.
- Accept whole-project editing requests as the **complete target product set**, but execute automatically in quality-gated small product groups (initial proposed size 2–3; configurable after measurement). Do not silently lower the standard, skip products, declare all done after processing a subset, or ask the user to restate the root folder for each group.
- For each group: verify complete accepted boundaries/candidates, perform Codex selection, refine and independently review final audio/semantic seams, check every applicable hard rule, and record immutable PASS/REPAIRABLE/BLOCKED checkpoints before eligible Resolve write. Proceed to the next group only after quality gate passes; a blocker must be reported with affected products, dependencies and safe resumption instructions. Preserve completed valid groups and revalidate on upstream change rather than recomputing the entire project.
- Project-wide completion requires an explicit per-product/per-variant ledger and a final cross-group consistency pass (source overlaps and relevant duplicates, durations, semantic review revisions, host ratio, cuts, audio/video source synchronization, and Resolve readback). Mark "edited", "written", "exported" and "verified" separately. Never claim the whole project completed from one group PASS.
- The 3–5 Codex correction-cycle limit is a **code-development cap across these requirements**, not a limit of product groups or normal per-product repair attempts. The full-project processing quality gate must not be bypassed to fit a correction round.
