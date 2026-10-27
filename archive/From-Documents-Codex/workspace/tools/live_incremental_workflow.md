# Live Incremental Slicer Workflow

Use this mode for long live-shopping recordings where processing the whole
timeline at once is too expensive or too risky.

## Timeline Layout

- Keep original live material on `V1/A1`.
- Append finished product clips to `V2/A2` by default.
- Keep every finished clip group on the same timeline.
- Leave 5 seconds of black between finished positive clips.
- Track progress in a JSON state file so retries skip completed groups.

## Batch Unit

Process one product at a time:

1. Identify the product window from the source timeline or source timecode.
2. Extract/transcribe only that window with faster-whisper.
3. Detect silence only inside that window.
4. Build 3 finished clips for that product, each strictly over 50 seconds and
   under 60 seconds.
5. Write the 3 clips to `V2/A2` after the last finished batch.
6. Save project and update the state file.
7. Read back and verify item count, duration, 5-second black gaps, media online
   state, and audio/video alignment.

## Default Safety Policy

The default write mode preserves `V1/A1`.

Only delete source material after a product is verified when the user explicitly
chooses that mode. Deletion should be limited to already-processed source
windows and should not be used when the original source is still needed as a
full-length reference.

## Append Command Shape

Dry-run first:

```bash
python3 workspace/tools/resolve_append_live_products.py \
  --plan workspace/runs/<run>/plan/<product-plan>.json \
  --source /path/to/source.mp4 \
  --state workspace/runs/<run>/incremental/state.json \
  --report-dir workspace/runs/<run>/incremental/<product-id> \
  --project <project-name> \
  --timeline <timeline-name> \
  --product <product-name> \
  --dry-run
```

Write after the blueprint is accepted:

```bash
python3 workspace/tools/resolve_append_live_products.py \
  --plan workspace/runs/<run>/plan/<product-plan>.json \
  --source /path/to/source.mp4 \
  --state workspace/runs/<run>/incremental/state.json \
  --report-dir workspace/runs/<run>/incremental/<product-id> \
  --project <project-name> \
  --timeline <timeline-name> \
  --product <product-name>
```

