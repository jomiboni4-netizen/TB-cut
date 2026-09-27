# TB Cut V2 — Agent Contract

## 0. Mandatory startup protocol

For every new Codex window or new task in this project:

1. Read `AGENTS.md`.
2. Read `PROJECT_RULES.md`.
3. Read `PROJECT_STATE.json` if it exists; otherwise read `PROJECT_STATE.template.json`.
4. Run `python3 scripts/project_doctor.py --root .` before any destructive or Resolve-writing action.
5. Do not analyze media, call MiMo, or write Resolve until the above files have been read.

Rule priority:

`current user instruction > PROJECT_RULES.md > AGENTS.md > automation defaults`

If a current user instruction conflicts with a persistent rule, obey the current instruction and update the persistent rule only when the user clearly means it as a new default.

## 1. Role boundaries

- Codex is the orchestrator and exception handler, not the default worker for bulk subtitle/media analysis.
- Prefer local scripts/models whenever they can complete a task reliably.
- MiMo is an authorized semantic reviewer/selector, not the main conversation model and not the final hard-rule authority.
- Local hard rules have final veto power before Resolve write.
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

- User provides title/subtitle/video paths for each batch.
- Never hard-code old dates, old project names, or old media paths.
- Resolve project/source timeline/target timeline must come from current state or explicit user instruction.
- Missing path/state is a blocking configuration error; do not guess.

## 5. Data sent to MiMo

Long-term authorization covers only locally pre-cleaned:
- product title
- candidate subtitle text
- subtitle timecodes
- failure reasons
- variant/repair summaries

Do not send by default:
- video/audio
- whole raw SRT
- whole ASR transcript
- cookies/tokens/passwords
- unrelated local identifiers

Use request fingerprint + cache. Identical MiMo requests must not be resent.

## 6. Hard stop conditions

Do not write Resolve if any final variant fails:
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
- Never delete configured source videos, subtitles, title files, Resolve projects, config/rules/state files, or final exports.
- Cache and temporary outputs are disposable and reproducible.
