#!/usr/bin/env python3
"""Send a manually confirmed secretary follow-up reminder."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.secretary_follow_up_service import execute_due_secretary_follow_ups, execute_secretary_follow_up


def main() -> None:
    parser = argparse.ArgumentParser(description="Send or preview a Hermes secretary follow-up.")
    parser.add_argument("--task-id", default="", help="Control task id.")
    parser.add_argument("--action-id", default="", help="Specific follow-up action id.")
    parser.add_argument("--all-due", action="store_true", help="Process a batch of due follow-up items.")
    parser.add_argument("--limit", type=int, default=3, help="Max number of due follow-ups to process in batch mode.")
    parser.add_argument("--levels", default="", help="Comma-separated follow-up levels to include in batch mode.")
    parser.add_argument("--dry-run", action="store_true", help="Preview the follow-up without sending.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    if args.all_due:
        result = execute_due_secretary_follow_ups(
            limit=max(1, int(args.limit or 1)),
            levels=str(args.levels or "").strip(),
            dry_run=bool(args.dry_run),
        )
    else:
        result = execute_secretary_follow_up(
            task_id=str(args.task_id or "").strip(),
            action_id=str(args.action_id or "").strip(),
            dry_run=bool(args.dry_run),
        )
    if args.json:
        print(json.dumps(result, ensure_ascii=False, indent=2))
        return
    print(result.get("message") or "")
    details = result.get("details")
    if isinstance(details, dict) and details:
        print(json.dumps(details, ensure_ascii=False, indent=2))


if __name__ == "__main__":
    main()
