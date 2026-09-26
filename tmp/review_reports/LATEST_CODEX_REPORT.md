### Task

Review and publish the already completed five cache integrity/workbook dependency fixes to existing PR #1. This task changes only this sanitized report beyond the reviewed patch; no unrelated refactoring or real runtime recovery.

### Repo changes

- Remove titles from input attestation and correct migration guidance. Readers and workspace preflight require current title generator provenance/schema and validate product rows; generic identity plus SHA is insufficient.
- Freeze subtitle state/identity and parse buffered SRT bytes authenticated against that identity. Use a shared generator directory lock, temporary outputs and repeated input/policy checks before publication. Record stale generator versions in `docs/CACHE_SAFETY_UPGRADE.json` without editing real runtime.
- Authenticate and parse the same artifact bytes; no check-then-reopen JSON loading.
- Publish artifact and metadata with independent renames; subtitle state uses a separate rename. Keep an incomplete marker on publication errors, including rollback failures or byte-identical leftovers. Preserve the original publication error and attempt state rollback too. This is not a cross-file or power-loss transaction.
- Pin Node 24.19.0 and artifact-tool 2.8.59 from the authorized darwin arm64 runtime distribution. The distribution lock covers the complete package tree and bundled transitive dependencies. Doctor and the real workbook parser enforce a read-only dependency check. No substitute XLSX parser.

### Runtime changes

None. No real subtitle_index, titles, product_ranges, workspace preflight or recovery command ran. Real PROJECT_STATE was only read for startup/doctor. No runtime caches were written, migrated, attested or removed. Generator and fault-injection execution used temporary synthetic workspaces only.

### Validation

- This task reran project doctor before commit: PASS with the pinned Node executable. It checks four required repository paths, state JSON parsing and configured field presence, python3/ffmpeg/ffprobe command availability, pinned Node/platform/module versions, full module distribution digest and importable workbook API. It does not validate config schemas, media contents, cache readiness, editing rules or Resolve.
- This task reran the complete local suite before commit: 73/73 PASS, no skips. Covers titles, subtitle mutation/concurrency/publication, runtime identity/TOCTOU, workspace paths/migration, existing V2.1 acceptance checks and dependency failures. Earlier dependency fault tests exposed a macOS path-alias CLI entry issue; corrected before the final full run.
- This task separately reran real Node-to-Python synthetic workbook integration before commit: 1/1 PASS. It uses the production artifact-tool parser and checks repeated generation plus string/numeric zero preservation.
- Real workspace preflight: NOT RUN, as instructed. Synthetic preflight rejection checks passed. No complete runtime READY claim.
- Unstaged/cached diff whitespace checks: PASS before staging. Reviewed all 20 files against the five requested fixes; no unrelated changes. Full staged content and credential/private-data scans are required again immediately before commit.
- GitHub CI: no configured Actions workflows (API count 0), and PR check rollup empty at the pre-push check. Local PASS results are not GitHub CI results.
- Reviewed changed-file list and full patch; scanned additions and the report for real local paths, credentials and private business data: PASS.
- PROJECT_RULES.md/config unchanged. Final rules fingerprint independently recomputed; prefix `b7f8f8b8aecc`, matches the supplied full digest. Full digest is reported to the user, not copied into this sanitized summary.

### External actions

- MiMo called: no
- Resolve connected: no
- export performed: no
- GitHub PR metadata read only; PR #1 OPEN, non-draft, remote head `ccd21813e9fc`, auto-merge disabled.
- Git commit/push: no. Merge: no. Auto-merge enabled: no. PR body unchanged; it describes the earlier remote code, not these local fixes.

### Blockers

- Publication awaits the final staged-data gate. After push, fixes await independent code review; local validation is not runtime approval.
- The workbook package is private. Reviewers require the exact authorized distribution; public npm installation or another platform is not certified. Missing/mismatched dependencies fail closed.
- Two/three file renames are not transactional. The directory lock coordinates participating generators, not unrelated external editors. Video identity uses path/size/mtime, not video-content hashing. These limits are documented.
- Recovery remains separately authorized: subtitle_index -> titles -> read-only workspace preflight -> STOP/report. Record each stage's before/after identity, fingerprints, file summaries, semantic differences and remaining blockers. READY from that limited preflight is not complete runtime READY. product_ranges needs a later separate authorization.

### Git state

Snapshot before any subsequent commit; no commit made in this task.

- Branch: `codex/repository-baseline`
- HEAD: `ccd21813e9fc`
- PR: #1 OPEN; remote head equals local HEAD; local fixes not pushed.
- Pre-staging snapshot: no staged content; seven new files have intent-to-add entries. The 20 reviewed files listed below are the only authorized commit scope.
- Modified: `CHANGELOG.md`, `docs/PIPELINE_DEPENDENCIES.md`, `scripts/attest_runtime_cache.py`, `scripts/index_subtitles.py`, `scripts/index_titles.py`, `scripts/inspect_title_workbook.mjs`, `scripts/migrate_runtime_layout.py`, `scripts/project_doctor.py`, `scripts/runtime_meta.py`, `scripts/workspace_preflight.py`, `tests/test_index_titles.py`, `tests/test_runtime_meta.py`, `tmp/review_reports/LATEST_CODEX_REPORT.md`.
- Added: `docs/CACHE_SAFETY_UPGRADE.json`, `package.json`, `scripts/check_workbook_dependencies.mjs`, `scripts/runtime_publication.py`, `tests/test_index_subtitles.py`, `tests/test_workbook_dependencies.py`, `workbook-dependencies.lock.json`.
- Existing local report updated for this task. Runtime not committed.
