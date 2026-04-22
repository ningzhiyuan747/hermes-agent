#!/usr/bin/env python3
"""Execute a secretary-loop action against the unified task state layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.secretary_action_service import execute_secretary_action


def main() -> None:
    parser = argparse.ArgumentParser(description="Execute a Hermes secretary action.")
    parser.add_argument("--task-id", required=True, help="Control task id.")
    parser.add_argument("--action", required=True, help="Secretary dispatch action to execute.")
    parser.add_argument("--auto-safe-only", action="store_true", help="Refuse actions that are not marked auto_safe.")
    parser.add_argument("--allow-unsafe", action="store_true", help="Allow non-auto-safe actions for manual operator use.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()

    result = execute_secretary_action(
        task_id=str(args.task_id or "").strip(),
        action=str(args.action or "").strip(),
        auto_safe_only=bool(args.auto_safe_only and not args.allow_unsafe),
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
