# Change Log

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
