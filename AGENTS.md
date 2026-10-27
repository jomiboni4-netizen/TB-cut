# TB Cut V2 — Agent Contract

## 0. Mandatory startup protocol

For every new Codex window or new task in this project:

1. Read `AGENTS.md`.
2. Read `PROJECT_RULES.md`.
3. Read `PROJECT_STATE.template.json` for the schema. Treat root `PROJECT_STATE.json` as a legacy record only; never use it to select the active project, media, batch, or Resolve timeline.
4. Before media analysis or Resolve action, read the live stable Resolve project ID and use its one bound project workspace under `.cache/projects/<project_key>/` (`PROJECT_STATE.json` and `PROJECT_CONFIG.json`). The live Resolve project ID is the sole project authenticator; folder paths, file availability, project display names, and timeline bindings are configuration or safety checks, not project identity. New batch workspaces contain only `PROJECT_REF.json`, `BATCH_STATE.json`, and batch artifacts; media/title paths and dependencies load from the bound project workspace through `RuntimePaths.load_state()`. Never seed a new batch from an old workspace or root legacy state.
   For future cutpoint work, read that project's `MANUAL_CUTPOINT_LESSONS.json` when present; treat it as observations from user edits, not a universal frame offset or an ASR PASS certificate.
   If that project has `PROJECT_TASKS.json`, read its `PROJECT.md`, `ACCEPTANCE.md`, `PLAN.md`, `STATUS.md`, and `DECISIONS.md`. Run `project_task.py refresh` to regenerate the handoff from actual records. User holds and task budgets are enforced by the scheduler; changing Markdown or switching batches never lifts a hold. `STATUS.md` is a derived view, never a source of approval. New long tasks are enrolled with `project_task.py init`, which authenticates the fresh live stable project ID.
5. Run `python3 scripts/project_doctor.py --root . --workspace <workspace>` before destructive or Resolve-writing actions. Running the doctor without `--workspace` is structural-only and does not validate an active project.
6. Before Resolve writing, run `python3 scripts/resolve_connection_doctor.py --root . --workspace <workspace>` outside the sandbox and confirm the live stable Resolve project ID matches that workspace. Validate configured timeline IDs separately as write-target safety checks.
7. Do not analyze media, call MiMo, or write Resolve until the applicable state and identity checks above have passed.
8. For an editing batch, Codex must start with `scripts/taobao_scheduler.py` or `scripts/dudu_scheduler.py` according to the uniquely matched Resolve project mode. Run its `plan` command before `run`; use `diagnose` after a failure and resume only from its reported valid rollback point. Do not manually chain stage CLIs around the scheduler. A mode mismatch or reported unmigrated delivery stage is a real blocker, not permission to bypass the scheduler.

Rule priority:

`current user instruction > PROJECT_RULES.md > AGENTS.md > automation defaults`

If a current user instruction conflicts with a persistent rule, obey the current instruction and update the persistent rule only when the user clearly means it as a new default.

## 1. Role boundaries

- Resolve 连接统一走 `scripts/resolve_connection.py`；只读入口为 `scripts/resolve_connection_doctor.py`。遵循 `PROJECT_RULES.md` 的沙盒外运行、自动网络回退和重连诊断要求；不要另写仅测试本机的临时连接脚本。
- Codex is the orchestrator and exception handler, not the default worker for bulk subtitle/media analysis.
- Prefer local scripts/models whenever they can complete a task reliably.
- Codex owns all semantic understanding, candidate selection, and semantic review for editing tasks. Do not call MiMo for editing semantics; only change this persistent rule after the user explicitly asks to revise it.
- Local hard rules have final veto power before Resolve write.
- Routine technical failures must use the configured bounded recovery queue before any code change: retry only failed evidence jobs, preserve successful/previously written variants, and distinguish WAITING_EVIDENCE from confirmed quality failures requiring semantic reselection. Do not reset budgets, rerun entire products, or replay Resolve writes to bypass a blocked job.
- Resolve source timeline is read-only. Only the explicitly configured target timeline may be written.

## 2. Raw subtitle access policy

- Codex must not repeatedly read complete raw SRT files.
- Raw SRT is ingested locally and indexed by fingerprint.
- If the subtitle fingerprint is unchanged, later windows/tasks must query derived local indexes/summaries instead of loading the whole SRT into Codex context.
- Codex may read a minimal local excerpt only when an unresolved exception requires it.
- Never dump complete ASR, word-level timing, full candidate text, or full logs into Codex by default.

## 3. Persistent vs batch-only user requirements

Treat phrases such as “以后 / 默认 / 从现在开始 / 都这样” as persistent unless context says otherwise:
- update `PROJECT_RULES.md`
- append a short line to `CHANGELOG.md`

Treat phrases such as “这次 / 这一批 / 先” as batch-only:
- update `PROJECT_STATE.json`
- do not modify persistent rules

## 4. Paths and project identity

- Register confirmed title/subtitle/video paths once per Resolve project in its project record; later windows and batches inherit that verified configuration. Ask only for missing or changed inputs. Legacy root/workspace state is not an authenticated registration.
- Never hard-code old dates, old project names, or old media paths.
- Resolve project/source timeline/target timeline must come from that project's record or explicit user instruction.
- Before product selection, resolve the live project name through `scripts/project_editing_modes.py`; use only the matching mode's input-range, duration, variant-count, and close-up policies. Ambiguous/unmatched names are blockers.
- Missing path/state is a blocking configuration error; do not guess.

## 5. External semantic services

- Do not send editing content to MiMo or another external semantic-review service. Codex performs semantic understanding and review locally within the project workflow.
- This prohibition covers product titles, candidate subtitles, timecodes, failure reasons, repair summaries, and media descriptions.

## 6. Hard stop conditions

Never write a failing variant. Independently passing variants must be approved and delivered without waiting for other variants of the same product; retain explicit pending-variant records and do not mark the whole product complete. Apply all gates below to each delivered variant and the delivered set:
- final duration rule
- semantic closure
- banned-content filter
- host-visible ratio
- physical cross-variant overlap
- semantic cross-variant duplication
- seam/cut-point quality
- source/target timeline validation

If 3 valid variants cannot be produced, reduce to 2 or 1. Never fill time with weak, repeated, promotional, or irrelevant content.

Variant audit states are `PASS`, `REPAIRABLE`, and `EXHAUSTED`.
- A first audit failure never authorizes reducing variant count.
- `REPAIRABLE` must enter the local repair loop and search the complete remaining candidate pool.
- Only `EXHAUSTED`, after the full product range and candidate pool were scanned and the configured repair limit was reached, may reduce variant count.
- Codex intervenes only for genuinely ambiguous product boundaries, exhausted repair, local-script faults, or external-system faults.
- Manual intervention still reads only the smallest necessary subtitle window, never the whole SRT by default.

Every pipeline/rule upgrade must be recorded in state or a manifest. Derived artifacts whose semantics changed must be marked stale so an older plan cannot be reused as current.

Before Resolve write, normalize physical clips. Adjacent clips from the same source that are contiguous within 1–2 frames and delete no source content must be merged; semantic roles remain audit metadata, not physical cut points.

## 7. Cleanup safety

Only `scripts/cleanup.py` may perform project cleanup by default.
- Always support `--dry-run`.
- A completed batch slice is eligible for cleanup seven days after its recorded completion time; retain the per-project record. Cleanup must preview exact batch targets and refuse active/incomplete batches.
- Never delete configured source videos, subtitles, title files, Resolve projects, config/rules/state files, or final exports.
- Cache and temporary outputs are disposable and reproducible.
