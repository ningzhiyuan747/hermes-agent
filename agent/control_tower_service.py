from __future__ import annotations

import os
import time
from typing import Any, Dict

from agent.operational_task_board_service import build_operational_task_snapshot
from agent.operator_worklist_service import list_operator_worklist
from agent.secretary_loop_service import build_secretary_loop_snapshot


def _task_status_counts(board: Dict[str, Any]) -> Dict[str, int]:
    counts = board.get("counts") if isinstance(board.get("counts"), dict) else {}
    task_counts = counts.get("tasks") if isinstance(counts.get("tasks"), dict) else {}
    return {str(key): int(value or 0) for key, value in task_counts.items()}


def _top_secretary_actions(secretary: Dict[str, Any], *, limit: int) -> list[Dict[str, Any]]:
    rows = secretary.get("actions") if isinstance(secretary.get("actions"), list) else []
    return [dict(item) for item in rows[: max(1, limit)] if isinstance(item, dict)]


def _top_operator_items(operator_worklist: Dict[str, Any], *, limit: int) -> list[Dict[str, Any]]:
    rows = operator_worklist.get("items") if isinstance(operator_worklist.get("items"), list) else []
    return [dict(item) for item in rows[: max(1, limit)] if isinstance(item, dict)]


def _follow_up_cooldown_seconds() -> int:
    raw = str(os.getenv("HERMES_SECRETARY_FOLLOW_UP_COOLDOWN_SECONDS") or "").strip()
    try:
        return max(300, int(raw or "14400"))
    except ValueError:
        return 14400


def build_control_tower_snapshot(*, limit: int = 8) -> Dict[str, Any]:
    board = build_operational_task_snapshot(limit=max(20, limit))
    secretary = build_secretary_loop_snapshot(limit=max(20, limit))
    operator_worklist = list_operator_worklist(limit=max(20, limit))

    secretary_actions = _top_secretary_actions(secretary, limit=limit)
    follow_up_items = [
        dict(item)
        for item in (secretary.get("follow_up_items") if isinstance(secretary.get("follow_up_items"), list) else [])[: max(1, limit)]
        if isinstance(item, dict)
    ]
    operator_items = _top_operator_items(operator_worklist, limit=limit)
    auto_safe_actions = [item for item in secretary_actions if bool(item.get("auto_safe"))]
    manual_actions = [item for item in secretary_actions if not bool(item.get("auto_safe"))]
    follow_up_actions = [item for item in secretary_actions if bool(item.get("follow_up_due"))]
    escalation_actions = [item for item in secretary_actions if bool(item.get("escalation_due"))]
    task_counts = _task_status_counts(board)
    task_units = [dict(item) for item in (board.get("units") if isinstance(board.get("units"), list) else []) if isinstance(item, dict) and str(item.get("unit_type") or "") == "task"]
    task_units_by_id = {
        str(item.get("related_ids", {}).get("task_id") or "").strip(): item
        for item in task_units
        if str(item.get("related_ids", {}).get("task_id") or "").strip()
    }
    cooldown_seconds = _follow_up_cooldown_seconds()
    now_unix = int(time.time())
    reminded_follow_up_items = []
    cooling_follow_up_items = []
    unsent_follow_up_items = []
    for item in follow_up_items:
        task_id = str(item.get("task_id") or "").strip()
        task_unit = task_units_by_id.get(task_id) if task_id else None
        last_action_id = str((task_unit or {}).get("last_follow_up_action_id") or "").strip()
        last_target_ref = str((task_unit or {}).get("last_follow_up_target_ref") or "").strip()
        last_follow_up_at_unix = int((task_unit or {}).get("last_follow_up_at_unix") or 0)
        last_follow_up_ok = bool((task_unit or {}).get("last_follow_up_ok"))
        matches_last = bool(
            task_unit
            and last_follow_up_ok
            and last_action_id == str(item.get("action_id") or "").strip()
            and last_target_ref == str(item.get("target_ref") or "").strip()
        )
        if matches_last:
            reminded_follow_up_items.append(item)
            if last_follow_up_at_unix and (now_unix - last_follow_up_at_unix) < cooldown_seconds:
                cooling_follow_up_items.append(item)
        else:
            unsent_follow_up_items.append(item)

    return {
        "generated_at_unix": int(time.time()),
        "summary": {
            "active_task_count": sum(
                int(task_counts.get(key) or 0)
                for key in ("open", "queued", "running", "pending_approval", "blocked", "paused")
            ),
            "task_status_counts": task_counts,
            "secretary_action_count": int(secretary.get("all_action_count") or 0),
            "auto_safe_action_count": len(auto_safe_actions),
            "manual_action_count": len(manual_actions),
            "follow_up_due_count": int(secretary.get("follow_up_due_count") or 0),
            "escalation_due_count": int(secretary.get("escalation_due_count") or 0),
            "follow_up_item_count": int(secretary.get("all_follow_up_item_count") or 0),
            "reminded_follow_up_count": len(reminded_follow_up_items),
            "cooling_follow_up_count": len(cooling_follow_up_items),
            "unsent_follow_up_count": len(unsent_follow_up_items),
            "operator_queue_count": int(operator_worklist.get("all_item_count") or 0),
            "derived_signal_count": len(list(board.get("derived_signals") or [])),
        },
        "secretary": {
            "action_counts": dict(secretary.get("action_counts") or {}),
            "follow_up_counts": dict(secretary.get("follow_up_counts") or {}),
            "actions": secretary_actions,
            "follow_up_actions": follow_up_actions,
            "escalation_actions": escalation_actions,
            "follow_up_items": follow_up_items,
            "reminded_follow_up_items": reminded_follow_up_items,
            "cooling_follow_up_items": cooling_follow_up_items,
            "unsent_follow_up_items": unsent_follow_up_items,
        },
        "operator_worklist": {
            "items": operator_items,
        },
        "board": {
            "reconcile_summary": dict(board.get("reconcile_summary") or {}),
            "derived_signals": list(board.get("derived_signals") or [])[: max(1, limit)],
        },
    }
