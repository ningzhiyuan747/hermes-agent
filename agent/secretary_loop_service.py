from __future__ import annotations

import time
from collections import Counter
from typing import Any, Dict

from agent.operational_task_board_service import build_operational_task_snapshot


_ACTION_PRIORITY = {
    "wait_approval": 95,
    "retry_delivery": 90,
    "switch_executor": 85,
    "retry_same_executor": 75,
}

_FOLLOW_UP_RULES = {
    "wait_approval": {
        "kind": "approval_follow_up",
        "target": "owner_admin",
        "report_channel": "origin_chat",
        "remind_after_seconds": 30 * 60,
        "escalate_after_seconds": 2 * 60 * 60,
    },
    "switch_executor": {
        "kind": "operator_follow_up",
        "target": "operator",
        "report_channel": "operator_queue",
        "remind_after_seconds": 30 * 60,
        "escalate_after_seconds": 2 * 60 * 60,
    },
    "retry_same_executor": {
        "kind": "operator_follow_up",
        "target": "operator",
        "report_channel": "operator_queue",
        "remind_after_seconds": 20 * 60,
        "escalate_after_seconds": 90 * 60,
    },
}


def _priority_for_action(action: str) -> int:
    return int(_ACTION_PRIORITY.get(str(action or "").strip(), 50))


def _auto_safe(action: str) -> bool:
    return str(action or "").strip() in {"retry_delivery"}


def _reason_for_action(unit: Dict[str, Any]) -> str:
    action = str(unit.get("dispatch_action") or "").strip()
    failure_kind = str(unit.get("failure_kind") or "").strip()
    recovery_hint = str(unit.get("recovery_hint") or "").strip()
    status = str(unit.get("status") or "").strip()
    if action == "wait_approval":
        return f"Task is {status} and waiting for approval resolution."
    if action == "retry_delivery":
        return f"Delivery failed ({failure_kind or 'delivery_failed'}) while execution output already exists."
    if action == "switch_executor":
        return f"Current executor is no longer preferred after {failure_kind or status}; switch executors."
    if action == "retry_same_executor":
        return f"Current executor should retry after {failure_kind or status}."
    return recovery_hint or f"Secretary should inspect task status {status}."


def _proposed_operator_action(unit: Dict[str, Any]) -> str:
    action = str(unit.get("dispatch_action") or "").strip()
    suggested_executor = str(unit.get("suggested_executor") or "").strip()
    if action == "wait_approval":
        return "Notify the owner/admin to approve or deny the pending task."
    if action == "retry_delivery":
        return "Retry delivery without rerunning the task executor."
    if action == "switch_executor":
        return f"Re-dispatch the task to executor '{suggested_executor or 'hermes'}'."
    if action == "retry_same_executor":
        return f"Retry the task with executor '{suggested_executor or 'hermes'}'."
    return "No operator action proposed."


def _follow_up_plan(unit: Dict[str, Any], *, action: str, now_unix: int) -> Dict[str, Any]:
    rules = _FOLLOW_UP_RULES.get(action) or {}
    updated_at_unix = int(unit.get("updated_at_unix") or 0)
    stale_for_seconds = max(0, int(now_unix or 0) - updated_at_unix) if updated_at_unix else 0
    remind_after_seconds = int(rules.get("remind_after_seconds") or 0)
    escalate_after_seconds = int(rules.get("escalate_after_seconds") or 0)
    follow_up_due = bool(remind_after_seconds and stale_for_seconds >= remind_after_seconds)
    escalation_due = bool(escalate_after_seconds and stale_for_seconds >= escalate_after_seconds)
    follow_up_level = "escalate" if escalation_due else "nudge" if follow_up_due else ""
    task_scope_key = str(unit.get("task_scope_key") or "").strip()
    task_id = str((unit.get("related_ids") if isinstance(unit.get("related_ids"), dict) else {}).get("task_id") or "").strip()
    follow_up_channel = task_scope_key or (f"task:{task_id}" if task_id else "")
    if action == "wait_approval":
        summary = (
            f"Approval has been pending for {stale_for_seconds // 60} minutes; "
            f"{'escalate' if escalation_due else 'nudge'} owner/admin on {follow_up_channel or 'task board'}."
            if follow_up_level
            else ""
        )
    elif action == "switch_executor":
        summary = (
            f"Executor switch has been waiting for operator review for {stale_for_seconds // 60} minutes; "
            f"{'escalate' if escalation_due else 'nudge'} the operator queue."
            if follow_up_level
            else ""
        )
    elif action == "retry_same_executor":
        summary = (
            f"Executor retry has not been handled for {stale_for_seconds // 60} minutes; "
            f"{'escalate' if escalation_due else 'nudge'} the operator queue."
            if follow_up_level
            else ""
        )
    else:
        summary = ""
    return {
        "stale_for_seconds": stale_for_seconds,
        "follow_up_due": follow_up_due,
        "escalation_due": escalation_due,
        "follow_up_level": follow_up_level,
        "follow_up_kind": str(rules.get("kind") or "").strip(),
        "follow_up_target": str(rules.get("target") or "").strip(),
        "follow_up_report_channel": str(rules.get("report_channel") or "").strip(),
        "follow_up_channel": follow_up_channel,
        "follow_up_summary": summary,
    }


def _parse_task_scope_key(task_scope_key: str) -> Dict[str, str]:
    normalized = str(task_scope_key or "").strip()
    if not normalized:
        return {"platform": "", "chat_id": "", "thread_id": "", "target_ref": ""}
    parts = normalized.split(":")
    if len(parts) >= 3 and parts[1] == "chat":
        platform = str(parts[0] or "").strip().lower()
        chat_id = str(parts[2] or "").strip()
        thread_id = ""
        if len(parts) >= 5 and parts[3] == "thread":
            thread_id = str(parts[4] or "").strip()
        target_ref = f"{platform}:{chat_id}"
        if thread_id:
            target_ref += f":{thread_id}"
        return {
            "platform": platform,
            "chat_id": chat_id,
            "thread_id": thread_id,
            "target_ref": target_ref,
        }
    return {"platform": "", "chat_id": "", "thread_id": "", "target_ref": ""}


def _follow_up_message(item: Dict[str, Any]) -> str:
    level = "升级提醒" if bool(item.get("escalation_due")) else "继续提醒"
    title = str(item.get("task_title") or item.get("task_id") or "任务").strip()
    stale_minutes = max(1, int(int(item.get("stale_for_seconds") or 0) // 60))
    next_step = str(item.get("next_step") or "").strip()
    reason = str(item.get("reason") or "").strip()
    lines = [
        f"{level}: {title}",
        f"- 已等待: {stale_minutes} 分钟",
        f"- 当前动作: {str(item.get('dispatch_action') or '').strip() or '-'}",
    ]
    if reason:
        lines.append(f"- 原因: {reason}")
    if next_step:
        lines.append(f"- 下一步: {next_step}")
    return "\n".join(lines)


def _follow_up_item_from_action(item: Dict[str, Any]) -> Dict[str, Any] | None:
    if not bool(item.get("follow_up_due")):
        return None
    task_id = str(item.get("task_id") or "").strip()
    action_id = str(item.get("action_id") or "").strip()
    if not task_id or not action_id:
        return None
    scope = _parse_task_scope_key(str(item.get("task_scope_key") or "").strip())
    follow_up_kind = str(item.get("follow_up_kind") or "").strip()
    if follow_up_kind == "approval_follow_up":
        target_kind = "origin_chat"
        target_ref = str(scope.get("target_ref") or "").strip()
    elif follow_up_kind == "operator_follow_up":
        target_kind = "operator_queue"
        target_ref = "operator_queue"
    else:
        target_kind = "task_board"
        target_ref = ""
    return {
        "item_id": f"follow-up:{action_id}",
        "action_id": action_id,
        "task_id": task_id,
        "task_title": str(item.get("task_title") or "").strip(),
        "dispatch_action": str(item.get("dispatch_action") or "").strip(),
        "follow_up_level": str(item.get("follow_up_level") or "").strip(),
        "follow_up_kind": follow_up_kind,
        "follow_up_target": str(item.get("follow_up_target") or "").strip(),
        "report_channel": str(item.get("follow_up_report_channel") or "").strip(),
        "target_kind": target_kind,
        "target_ref": target_ref,
        "target_platform": str(scope.get("platform") or "").strip(),
        "target_chat_id": str(scope.get("chat_id") or "").strip(),
        "target_thread_id": str(scope.get("thread_id") or "").strip(),
        "stale_for_seconds": int(item.get("stale_for_seconds") or 0),
        "summary": str(item.get("follow_up_summary") or "").strip(),
        "message_preview": _follow_up_message(item),
    }


def _action_from_task_unit(unit: Dict[str, Any], *, now_unix: int) -> Dict[str, Any] | None:
    if str(unit.get("unit_type") or "").strip() != "task":
        return None
    action = str(unit.get("dispatch_action") or "").strip()
    status = str(unit.get("status") or "").strip().lower()
    delivery_status = str(unit.get("delivery_status") or "").strip().lower()
    failure_kind = str(unit.get("failure_kind") or "").strip().lower()
    if action not in {"wait_approval", "retry_delivery", "switch_executor", "retry_same_executor"}:
        return None
    if action == "wait_approval" and status != "pending_approval":
        return None
    if action == "retry_delivery" and delivery_status != "failed" and failure_kind not in {"credential_failed", "routing_failed", "delivery_failed"}:
        return None
    if action in {"switch_executor", "retry_same_executor"} and status not in {"failed", "blocked", "paused", "queued", "running"}:
        return None
    related_ids = unit.get("related_ids") if isinstance(unit.get("related_ids"), dict) else {}
    task_id = str(related_ids.get("task_id") or "").strip()
    if not task_id:
        return None
    suggested_executor = str(unit.get("suggested_executor") or "").strip()
    follow_up_plan = _follow_up_plan(unit, action=action, now_unix=now_unix)
    return {
        "action_id": f"{action}:{task_id}",
        "task_id": task_id,
        "task_title": str(unit.get("title") or "").strip(),
        "status": str(unit.get("status") or "").strip(),
        "dispatch_action": action,
        "suggested_executor": suggested_executor,
        "priority": _priority_for_action(action),
        "auto_safe": _auto_safe(action),
        "task_scope_key": str(unit.get("task_scope_key") or "").strip(),
        "person_memory_key": str(unit.get("person_memory_key") or "").strip(),
        "failure_kind": str(unit.get("failure_kind") or "").strip(),
        "reason": _reason_for_action(unit),
        "proposed_operator_action": _proposed_operator_action(unit),
        "current_focus": str(unit.get("current_focus") or "").strip(),
        "next_step": str(unit.get("next_step") or "").strip(),
        "recovery_hint": str(unit.get("recovery_hint") or "").strip(),
        "updated_at_unix": int(unit.get("updated_at_unix") or 0),
        **follow_up_plan,
    }


def plan_secretary_actions(snapshot: Dict[str, Any]) -> list[Dict[str, Any]]:
    now_unix = int(time.time())
    units = snapshot.get("units") if isinstance(snapshot.get("units"), list) else []
    actions = [item for item in (_action_from_task_unit(unit, now_unix=now_unix) for unit in units) if isinstance(item, dict)]
    actions.sort(
        key=lambda item: (
            -int(bool(item.get("escalation_due"))),
            -int(bool(item.get("follow_up_due"))),
            -int(item.get("priority") or 0),
            -int(item.get("updated_at_unix") or 0),
            str(item.get("action_id") or ""),
        ),
    )
    return actions


def build_secretary_loop_snapshot(*, limit: int = 20) -> Dict[str, Any]:
    board = build_operational_task_snapshot(limit=max(20, limit))
    actions = plan_secretary_actions(board)
    action_counts = Counter(str(item.get("dispatch_action") or "").strip() for item in actions if str(item.get("dispatch_action") or "").strip())
    follow_up_counts = Counter(
        str(item.get("follow_up_level") or "").strip()
        for item in actions
        if str(item.get("follow_up_level") or "").strip()
    )
    follow_up_due_count = sum(1 for item in actions if bool(item.get("follow_up_due")))
    escalation_due_count = sum(1 for item in actions if bool(item.get("escalation_due")))
    follow_up_items = [item for item in (_follow_up_item_from_action(action) for action in actions) if isinstance(item, dict)]
    return {
        "generated_at_unix": int(time.time()),
        "board_generated_at_unix": int(board.get("generated_at_unix") or 0),
        "action_counts": dict(sorted(action_counts.items(), key=lambda item: (-item[1], item[0]))),
        "follow_up_counts": dict(sorted(follow_up_counts.items(), key=lambda item: (-item[1], item[0]))),
        "follow_up_due_count": follow_up_due_count,
        "escalation_due_count": escalation_due_count,
        "follow_up_items": follow_up_items[: max(1, limit)],
        "all_follow_up_item_count": len(follow_up_items),
        "actions": actions[: max(1, limit)],
        "all_action_count": len(actions),
        "derived_signals": list(board.get("derived_signals") or []),
        "reconcile_summary": dict(board.get("reconcile_summary") or {}),
        "board": board,
    }
