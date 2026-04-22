#!/usr/bin/env python3
"""Render the secretary loop's next-action queue from the unified task board."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.secretary_loop_service import build_secretary_loop_snapshot


def _short(text: Any, limit: int = 88) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def collect_snapshot(limit: int = 8) -> dict[str, Any]:
    return build_secretary_loop_snapshot(limit=max(1, limit))


def build_report(snapshot: dict[str, Any], *, limit: int = 8) -> str:
    actions = snapshot.get("actions") if isinstance(snapshot.get("actions"), list) else []
    follow_up_items = snapshot.get("follow_up_items") if isinstance(snapshot.get("follow_up_items"), list) else []
    action_counts = snapshot.get("action_counts") if isinstance(snapshot.get("action_counts"), dict) else {}
    follow_up_counts = snapshot.get("follow_up_counts") if isinstance(snapshot.get("follow_up_counts"), dict) else {}
    lines = ["Secretary Loop 动作建议", ""]
    lines.append(f"- 动作总数: {int(snapshot.get('all_action_count') or 0)}")
    lines.append(f"- 动作分布: {action_counts or {'none': 0}}")
    lines.append(f"- 催办到期: {int(snapshot.get('follow_up_due_count') or 0)}")
    lines.append(f"- 升级到期: {int(snapshot.get('escalation_due_count') or 0)}")
    if follow_up_counts:
        lines.append(f"- 催办分布: {follow_up_counts}")
    reconcile_summary = snapshot.get("reconcile_summary") if isinstance(snapshot.get("reconcile_summary"), dict) else {}
    if reconcile_summary:
        lines.append(
            "- 最新回灌: "
            f"runs {int(reconcile_summary.get('runs_linked') or 0)}/{int(reconcile_summary.get('runs_scanned') or 0)}, "
            f"jobs {int(reconcile_summary.get('jobs_linked') or 0)}/{int(reconcile_summary.get('jobs_scanned') or 0)}, "
            f"delegations {int(reconcile_summary.get('delegations_linked') or 0)}/{int(reconcile_summary.get('delegations_scanned') or 0)}, "
            f"tasks {int(reconcile_summary.get('tasks_terminalized') or 0)}/{int(reconcile_summary.get('tasks_scanned') or 0)}"
        )
    lines.append("")
    if not actions:
        lines.append("当前没有需要秘书层介入的动作。")
        return "\n".join(lines)

    lines.append("建议动作:")
    for item in actions[: max(1, limit)]:
        scope_bits = []
        task_scope_key = str(item.get("task_scope_key") or "").strip()
        person_memory_key = str(item.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_bits.append(f"task_scope={task_scope_key}")
        if person_memory_key:
            scope_bits.append(f"person_memory={person_memory_key}")
        scope_suffix = f" | {' | '.join(scope_bits)}" if scope_bits else ""
        lines.append(
            f"- {str(item.get('dispatch_action') or '-').strip()} -> {str(item.get('suggested_executor') or '-').strip()} | "
            f"{str(item.get('task_id') or '-').strip()} | {_short(item.get('task_title') or '-', 64)}{scope_suffix}"
        )
        lines.append(f"  原因: {_short(item.get('reason') or '-', 108)}")
        lines.append(f"  建议: {_short(item.get('proposed_operator_action') or '-', 108)}")
        if str(item.get("recovery_hint") or "").strip():
            lines.append(f"  恢复: {_short(item.get('recovery_hint') or '-', 108)}")
        if bool(item.get("follow_up_due")):
            lines.append(
                f"  跟进: {str(item.get('follow_up_level') or 'nudge').strip()} | "
                f"{_short(item.get('follow_up_summary') or '-', 108)}"
            )
        lines.append(f"  自动安全: {'yes' if bool(item.get('auto_safe')) else 'no'} | 优先级: {int(item.get('priority') or 0)}")
    if follow_up_items:
        lines.append("")
        lines.append("提醒草案:")
        for item in follow_up_items[: max(1, limit)]:
            target = str(item.get("target_ref") or item.get("target_kind") or "-").strip()
            lines.append(
                f"- {str(item.get('follow_up_level') or '-').strip()} | {target} | "
                f"{str(item.get('task_id') or '-').strip()} | {_short(item.get('task_title') or '-', 64)}"
            )
            lines.append(f"  摘要: {_short(item.get('summary') or '-', 108)}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render Hermes secretary loop next actions.")
    parser.add_argument("--limit", type=int, default=8, help="How many suggested actions to show.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()
    snapshot = collect_snapshot(limit=max(1, args.limit))
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(build_report(snapshot, limit=max(1, args.limit)))


if __name__ == "__main__":
    main()
