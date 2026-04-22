#!/usr/bin/env python3
"""Render pending operator queue items from the unified task layer."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.operator_worklist_service import list_operator_worklist


def _short(text: Any, limit: int = 96) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def build_report(snapshot: dict[str, Any]) -> str:
    items = snapshot.get("items") if isinstance(snapshot.get("items"), list) else []
    lines = ["Operator Worklist", ""]
    lines.append(f"- 待处理总数: {int(snapshot.get('all_item_count') or 0)}")
    if not items:
        lines.append("当前没有待处理操作。")
        return "\n".join(lines)
    lines.append("")
    lines.append("待处理项:")
    for item in items:
        lines.append(
            f"- {str(item.get('queue_item_id') or '-').strip()} | {str(item.get('kind') or '-').strip()} | "
            f"{str(item.get('requested_executor') or '-').strip()} | {str(item.get('task_id') or '-').strip()} | "
            f"{_short(item.get('task_title') or '-', 64)}"
        )
        lines.append(f"  摘要: {_short(item.get('summary') or '-', 108)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Hermes operator worklist.")
    parser.add_argument("--limit", type=int, default=8, help="How many queue items to show.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()
    snapshot = list_operator_worklist(limit=max(1, args.limit))
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(build_report(snapshot))


if __name__ == "__main__":
    main()
