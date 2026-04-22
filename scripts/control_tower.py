#!/usr/bin/env python3
"""Render a compact control tower snapshot for Hermes operators."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.control_tower_service import build_control_tower_snapshot


def _short(text: Any, limit: int = 88) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def build_report(snapshot: dict[str, Any], *, limit: int = 6) -> str:
    summary = snapshot.get("summary") if isinstance(snapshot.get("summary"), dict) else {}
    secretary = snapshot.get("secretary") if isinstance(snapshot.get("secretary"), dict) else {}
    operator_worklist = snapshot.get("operator_worklist") if isinstance(snapshot.get("operator_worklist"), dict) else {}
    board = snapshot.get("board") if isinstance(snapshot.get("board"), dict) else {}
    lines = ["Control Tower", ""]
    lines.append(f"- 活跃任务: {int(summary.get('active_task_count') or 0)}")
    lines.append(f"- 秘书动作: {int(summary.get('secretary_action_count') or 0)}")
    lines.append(f"- 自动安全动作: {int(summary.get('auto_safe_action_count') or 0)}")
    lines.append(f"- 人工动作: {int(summary.get('manual_action_count') or 0)}")
    lines.append(f"- 催办到期: {int(summary.get('follow_up_due_count') or 0)}")
    lines.append(f"- 升级到期: {int(summary.get('escalation_due_count') or 0)}")
    lines.append(f"- 提醒草案: {int(summary.get('follow_up_item_count') or 0)}")
    lines.append(f"- 已提醒: {int(summary.get('reminded_follow_up_count') or 0)}")
    lines.append(f"- 冷却中: {int(summary.get('cooling_follow_up_count') or 0)}")
    lines.append(f"- 待首次提醒: {int(summary.get('unsent_follow_up_count') or 0)}")
    lines.append(f"- Operator 待办: {int(summary.get('operator_queue_count') or 0)}")
    lines.append(f"- 任务状态分布: {dict(summary.get('task_status_counts') or {})}")
    lines.append("")
    secretary_actions = secretary.get("actions") if isinstance(secretary.get("actions"), list) else []
    if secretary_actions:
        lines.append("秘书动作:")
        for item in secretary_actions[: max(1, limit)]:
            line = (
                f"- {item.get('dispatch_action')} -> {item.get('suggested_executor')} | {item.get('task_id')} | {_short(item.get('task_title') or '-', 64)}"
            )
            if bool(item.get("follow_up_due")):
                line += f" | 跟进={item.get('follow_up_level')}"
            lines.append(line)
    follow_up_items = secretary.get("follow_up_items") if isinstance(secretary.get("follow_up_items"), list) else []
    if follow_up_items:
        lines.append("")
        lines.append("提醒草案:")
        for item in follow_up_items[: max(1, limit)]:
            lines.append(
                f"- {item.get('follow_up_level')} | {item.get('target_ref') or item.get('target_kind')} | "
                f"{item.get('task_id')} | {_short(item.get('summary') or '-', 84)}"
            )
    reminded_items = secretary.get("reminded_follow_up_items") if isinstance(secretary.get("reminded_follow_up_items"), list) else []
    if reminded_items:
        lines.append("")
        lines.append("已提醒:")
        for item in reminded_items[: max(1, limit)]:
            lines.append(
                f"- {item.get('follow_up_level')} | {item.get('target_ref') or item.get('target_kind')} | "
                f"{item.get('task_id')} | {_short(item.get('summary') or '-', 84)}"
            )
    operator_items = operator_worklist.get("items") if isinstance(operator_worklist.get("items"), list) else []
    if operator_items:
        lines.append("")
        lines.append("Operator 待办:")
        for item in operator_items[: max(1, limit)]:
            lines.append(
                f"- {item.get('kind')} | {item.get('requested_executor')} | {item.get('task_id')} | {_short(item.get('summary') or '-', 84)}"
            )
    signals = board.get("derived_signals") if isinstance(board.get("derived_signals"), list) else []
    if signals:
        lines.append("")
        lines.append("系统判断:")
        for signal in signals[: max(1, limit)]:
            lines.append(f"- {_short(signal, 120)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Hermes control tower snapshot.")
    parser.add_argument("--limit", type=int, default=6, help="How many items to show per section.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()
    snapshot = build_control_tower_snapshot(limit=max(1, args.limit))
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(build_report(snapshot, limit=max(1, args.limit)))


if __name__ == "__main__":
    main()
