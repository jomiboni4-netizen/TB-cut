# TB Cut V2 Starter

## Start here

Read in this order before changing the project:

1. `README.md` — setup and command entry points.
2. `PROJECT_OVERVIEW_FOR_GPT.md` — architecture and data flow.
3. `PROJECT_RULES.md` — lasting editing rules.
4. `AGENTS.md` — agent startup and safety rules.
5. `docs/REPOSITORY_AUDIT.md` — public repository boundary and known issues.
6. `docs/PIPELINE_DEPENDENCIES.md` — stage inputs, outputs, and cache identity checks.

`PROJECT_STATE.json` is local runtime state, **not part of the Git repository**. Start from `PROJECT_STATE.template.json` and supply paths and Resolve names for your own batch. `.cache/`, `reports/`, `plans/`, and `exports/` also stay in the local runtime workspace. Do not publish real subtitles, ASR, MiMo responses, product titles, media paths, or exported videos.

A clean starter project for Taobao live-slice editing with local-first processing, MiMo semantic review, hard local audits and automatic DaVinci Resolve write.

## 1. First use

Copy/unzip this folder to your desired project location, then enter it:

```bash
cd /path/to/TB_CUT_V2
```

Initialize current paths/state (example; `tbcut.py` is the shorter wrapper):

```bash
python3 tbcut.py init \
  --root . \
  --title "/path/to/title.xlsx" \
  --subtitles "/path/to/001.srt" "/path/to/002.srt" \
  --videos "/path/to/001.mp4" "/path/to/002.mp4" \
  --resolve-project "your Resolve project" \
  --source-timeline "素材" \
  --target-timeline "成片"
```

Then run:

```bash
python3 tbcut.py doctor
python3 tbcut.py fingerprint
```

Open the folder in Codex. The first instruction for every new window is already defined in `AGENTS.md`: read AGENTS → rules → state before work.

## 2. Cleanup commands

Default is dry-run; nothing is deleted unless `--apply` is added.

Temporary junk only:
```bash
python3 tbcut.py clean --light
python3 tbcut.py clean --light --apply
```

Selected cache folders (`.cache/subtitles`, `products`, `asr`, `vision`, `mimo`, `tmp`):
```bash
python3 tbcut.py clean --cache
python3 tbcut.py clean --cache --apply
```

Reports only:
```bash
python3 tbcut.py clean --reports
python3 tbcut.py clean --reports --apply
```

Those cache folders, reports, and temporary exports:
```bash
python3 tbcut.py clean --all
python3 tbcut.py clean --all --apply
```

Protected by design: configured source videos, subtitles, title file, rules/state files and normal final exports.

## 3. What NOT to migrate from the old project

Do not copy by default:
- old `.cache/`
- old product/rough/windowed JSON
- old cut plans
- old manifests
- old audit reports
- old path/date-specific config
- old Resolve write-state markers

Those encode old assumptions such as fixed 3 variants, old rough-duration limits and legacy overlap tolerances.

## 4. What may be migrated selectively

Review and copy only after inspection:
- working MiMo API wrapper/provider code (without secrets)
- proven Resolve connection helper
- proven clean cut-point/ASR/VAD utilities
- proven product-range locating logic
- proven semantic dedup logic
- Apple Vision / Qwen local inference adapters

Prefer copying code, not runtime JSON/cache.

Do not copy API keys into the repository. Continue using environment variables such as `MIMO_API_KEY`.

## 5. Runtime data philosophy

Keep runtime JSON minimal:
- `PROJECT_STATE.json`: current paths/project/batch state
- candidate-pool JSON: locally useful source ranges
- MiMo result JSON: semantic selection/review result
- final-plan JSON: only audited ranges that may be written to Resolve
- concise reports only for errors/audits

The raw SRT should be indexed locally once and then referenced by fingerprint. Codex should not repeatedly load full subtitle files.

## 6. Pipeline target

1. Initialize state and fingerprint inputs.
2. Local subtitle ingest/index and product-range localization.
3. Local banned-content filtering → candidate pool (no candidate duration limit).
4. MiMo semantic selection/assembly recommendation.
5. Local semantic closure audit.
6. Local ASR/VAD/frame cut refinement.
7. Local Apple Vision → Qwen 4B → Qwen 8B fallback visual audit.
8. Final hard audit: >45s <60s, host >70%, zero source overlap, zero semantic duplication.
9. Automatic Resolve write + post-write validation.

The implemented stages live in `scripts/`; `tbcut.py` is a maintenance wrapper, not a one-command pipeline. Check each stage's inputs before running it. See `docs/REPOSITORY_AUDIT.md` for known hidden dependencies and cache risks.

## 7. Migration

See `MIGRATION.md` before copying anything from the old project. The default is code-only selective migration, not old runtime JSON/cache.
