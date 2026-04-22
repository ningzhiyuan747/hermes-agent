#!/usr/bin/env python3
"""Resolve an operator queue item on a task."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.operator_worklist_service import complete_operator_queue_item


def main() -> None:
    parser = argparse.ArgumentParser(description="Resolve a Hermes operator queue item.")
    parser.add_argument("--task-id", required=True, help="Control task id.")
    parser.add_argument("--queue-item-id", required=True, help="Operator queue item id.")
    parser.add_argument("--resolution", default="completed", help="Resolution label.")
    parser.add_argument("--note", default="", help="Optional resolution note.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    result = complete_operator_queue_item(
        task_id=str(args.task_id or "").strip(),
        queue_item_id=str(args.queue_item_id or "").strip(),
        resolution=str(args.resolution or "").strip(),
        note=str(args.note or "").strip(),
    )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
    else:
        print(result.get("message") or "")
        if isinstance(result.get("details"), dict):
            print(json.dumps(result["details"], ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
