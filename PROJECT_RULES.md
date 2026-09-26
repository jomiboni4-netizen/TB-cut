# TB Cut V2 — Persistent Editing Rules

## Working communication

- Report progress in Chinese.
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
- 工作簿中第一个有效商品数据行定义为 `product_index=1`，对应 1号链接；其后按有效商品行顺序连续递增，不使用 Excel 物理行号。
- 仅完全空白行跳过且不占编号。非空行缺少编码 ID 或标题时，整个生成过程失败，不允许静默跳过或跳号。编码 ID 必须唯一，标题必须为非空文本。
- `encoding_id` 保持字符串语义。文本编码原样保留，前导零不得丢失；拒绝编码前后空白。数字编码仅接受可无损表示的非负整数（少于 16 位），允许 General、文本格式或纯零填充格式；按零填充宽度保留前导零。精度、格式或类型不明确时拒绝生成，不猜测业务 ID。
- 为避免隐式取值，当前生成器拒绝合并单元格、公式及隐藏行列，不自动展开或忽略这些结构。
- 标题工作簿顺序定义商品编号，但不定义直播中的商品时间边界；范围定位仍以真实链接锚点和时间轴规则为准。

## Final deliverables

- Target: up to 3 finished variants per product.
- Normal expectation is 3; if only 2 or 1 can pass all hard rules, output fewer.
- Never fabricate a third variant by repetition or filler.
- Each final variant must be strictly `> 45.0s` and `< 60.0s`.
- Recommended internal assembly target: roughly 48–56s to leave cut-point margin.

## Candidate pool (“粗选”) definition

- Candidate pool items are useful source segments before final assembly, not rough finished videos.
- Candidate segments have no minimum or maximum duration.
- A candidate may be a few seconds or longer than 60 seconds.
- Candidate selection asks only: “Is there useful product-selling or wearing-experience content here?”
- MiMo may select only the most meaningful subrange from a long candidate and discard the rest.
- Do not force candidate blocks to resemble final duration.
- Build the pool only after locating the complete product range. Title matches and link announcements are anchors, never the product range itself.
- The complete range runs from the current high-confidence product start to the next real high-confidence product start or boundary; title-sheet row order is supporting evidence, not live-order truth.
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

- Final `host_visible_duration / final_duration` must be strictly greater than 70%.
- The visible person is normally the host.
- Clothing/product close-up without the host as the main visible subject does not count.
- No-person shots do not count.
- Long close-up/no-person sections should be removed when they are not required for semantic continuity.

Local visual model order:
1. Apple local Vision/model as primary classifier.
2. Qwen 4B Vision only for low-confidence/uncertain samples.
3. Qwen 8B Vision only if still uncertain.

Suggested labels:
- `host_visible`
- `clothes_closeup`
- `no_person`
- `uncertain`

FFmpeg/ffprobe handle sampling, timing, segment extraction and ratio aggregation; the local vision model supplies visual classification.

## Audio/cut-point rule

- MiMo chooses semantic ranges; local tools choose final physical cut points.
- Use SRT + local ASR/VAD + pauses/energy + frame boundaries for clean starts/ends.
- Never hard-cut a half word or half sentence.
- Local ASR should be windowed around selected ranges, not full-media retranscription by default.
- MiMo timecodes are rough semantic bounds. If a natural sentence ending lies after the rough end, extend to the sentence boundary first; if this would exceed 60 seconds, remove or replace another low-value complete segment. Never meet duration by cutting a sentence tail.
- Tail closure risks include unfinished cause, condition, contrast, enumeration, reference, promise, and speech that continues as the same sentence for the next 1–3 seconds.

## Repair and exhaustion

- Variant states are `PASS`, `REPAIRABLE`, and `EXHAUSTED`.
- Duration misses, incomplete openings/endings, closure gaps, 65–70% host visibility, overlap, duplication, poor visuals, weak seams, and low-value blocks are repairable by default.
- Repair must return to the complete remaining candidate pool, lock already-passing variants, record attempts and changes, and retry up to the configured limit.
- Only a complete-range scan, complete candidate-pool search, and exhausted repair limit may reduce the target from 3 to 2, 1, or 0.

## Physical clip normalization

- Before Resolve write, merge adjacent clips from the same source when timeline and source are contiguous within 1–2 frames and no source content is intentionally removed.
- Candidate IDs, semantic blocks, pauses, and roles do not create physical cut points. Preserve multiple roles in audit metadata on the merged clip.

## Resolve

- Automatically write only after all hard audits pass.
- Source timeline is read-only.
- Resolve project/source timeline/target timeline are user-specified per project/batch state.
- Validate before write, back up when appropriate, write, then validate after write.
- Final reports include product-range and candidate-pool coverage, repair attempts/status, duration, host ratio, closure, overlap/duplication, meaningless-cut count, and seam-tail risk count.
