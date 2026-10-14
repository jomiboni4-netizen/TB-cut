# Migration from the old TB Cut project

## Default answer: migrate code selectively, not runtime data

Start the new project clean. Do **not** copy old cache folders or generated JSON as a bundle.

### Do not migrate by default

- `.cache/`
- old `product_*_rough/` / `product_*_windowed/`
- `mimo_request.json` / `mimo_result.json` from old runs
- old `cut_plan*.json`
- old manifests / fingerprints / write-state markers
- old audit reports
- path/date/project-name-specific JSON

Reason: old runtime artifacts may encode legacy assumptions that are intentionally removed in V2.

### Good candidates to port after inspection

Prefer porting **implementation code** only:

1. `connect_resolve.py` or the known-working Resolve connection/bootstrap logic.
2. `mimo_review.py` provider/API transport, request locking, request fingerprinting and cache handling — but update its request schema to V2.
3. Product-range localization logic that uses the global live timeline rather than title-row order.
4. Windowed local ASR/VAD and clean cut-point search logic.
5. Black/static-frame probes that operate on selected windows.
6. Semantic duplication utilities, after changing physical overlap tolerance to exactly zero.
7. Apple Vision local classifier adapter.
8. Qwen 4B/8B local vision adapters.
9. Resolve preflight/backup/post-write validation utilities that do not assume a fixed project/timeline name or exactly three variants.

### Code that must be changed before reuse

Do not copy unchanged code that assumes:

- exactly 3 variants must always exist
- rough/candidate duration is 20–55s
- source overlap up to 1.0s is allowed
- old Resolve project/timeline names
- old absolute title/subtitle/video paths
- three-variant-only append/replace functions

### Secrets

Do not migrate API keys into files. Keep `MIMO_API_KEY` and other secrets in the environment/keychain as before.

### Recommended migration order

1. Initialize and validate the clean V2 folder.
2. Port Resolve connection helper and run read-only timeline inspection.
3. Port subtitle ingest/range locator and confirm local fingerprint cache behavior.
4. Port candidate-pool builder with **no duration restriction**.
5. Port MiMo transport and adapt its input/output to semantic subrange selection.
6. Add semantic closure audit.
7. Port ASR/VAD/cut refinement.
8. Port Apple/Qwen visual audit.
9. Port generic 1–3-variant Resolve writer.
10. Run a full test product before batch processing.
