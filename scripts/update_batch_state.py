#!/usr/bin/env python3
"""Update compact batch progress in PROJECT_STATE.json."""

from __future__ import annotations

import argparse
import json
from pathlib import Path
from runtime_paths import resolve_runtime_paths


def main() -> None:
    parser = argparse.ArgumentParser()
    parser.add_argument("--root", default=".")
    parser.add_argument("--workspace")
    parser.add_argument("--completed", default="")
    parser.add_argument("--failed", default="")
    parser.add_argument("--recovered", default="")
    parser.add_argument("--stage")
    args = parser.parse_args()
    root = Path(args.root).resolve()
    paths = resolve_runtime_paths(root, args.workspace)
    path = paths.state_path
    state = json.loads(path.read_text(encoding="utf-8"))
    batch = state.setdefault("batch", {})
    completed = set(int(value) for value in batch.get("completed_product_ids") or [])
    failed = set(int(value) for value in batch.get("failed_product_ids") or [])
    completed.update(int(value) for value in args.completed.split(",") if value.strip())
    failed.update(int(value) for value in args.failed.split(",") if value.strip())
    recovered = {int(value) for value in args.recovered.split(",") if value.strip()}
    failed -= recovered
    completed.update(recovered)
    completed -= failed
    batch["completed_product_ids"] = sorted(completed)
    batch["failed_product_ids"] = sorted(failed)
    if args.stage:
        state.setdefault("runtime", {})["last_successful_stage"] = args.stage
    path.write_text(json.dumps(state, ensure_ascii=False, indent=2) + "\n", encoding="utf-8")
    print(f"批次状态：完成{len(completed)} 失败{len(failed)}")


if __name__ == "__main__":
    main()
