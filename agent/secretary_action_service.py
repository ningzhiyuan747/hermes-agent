from __future__ import annotations

import time
from typing import Any, Dict, Iterable

from agent.background_job_delivery import deliver_job_result
from agent.background_jobs import get_job, list_jobs, update_job
from agent.business_db import get_task, update_task
from agent.executor_registry import get_executor_spec
from agent.task_panel_service import sync_task_control_state


_AUTO_SAFE_ACTIONS = {"retry_delivery"}
_TERMINAL_JOB_STATUSES = {"completed", "failed", "cancelled"}


def _normalize(value: Any) -> str:
    return str(value or "").strip().lower()


def _control_plane(task: Dict[str, Any]) -> Dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    return dict(control_plane)


def _metadata(task: Dict[str, Any]) -> Dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return dict(metadata)


def _sort_jobs(rows: Iterable[Dict[str, Any]]) -> list[Dict[str, Any]]:
    return sorted(
        [dict(row) for row in rows if isinstance(row, dict)],
        key=lambda item: (
            int(item.get("updated_at_unix") or item.get("finished_at_unix") or item.get("created_at_unix") or 0),
            str(item.get("job_id") or ""),
        ),
        reverse=True,
    )


def _candidate_delivery_job(task_id: str) -> Dict[str, Any] | None:
    rows = [
        row
        for row in list_jobs(limit=200, active_only=False)
        if str(row.get("task_id") or "").strip() == task_id
        and _normalize(row.get("status")) in _TERMINAL_JOB_STATUSES
        and (
            _normalize(row.get("delivery_status")) == "failed"
            or str(row.get("delivery_error") or "").strip()
        )
    ]
    ordered = _sort_jobs(rows)
    return ordered[0] if ordered else None


def _record_secretary_action(
    task: Dict[str, Any],
    *,
    action: str,
    ok: bool,
    details: Dict[str, Any],
) -> Dict[str, Any] | None:
    metadata = _metadata(task)
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    action_record = {
        "action": str(action or "").strip(),
        "ok": bool(ok),
        "recorded_at_unix": int(time.time()),
        **{key: value for key, value in details.items() if value not in {None, ""}},
    }
    metadata["secretary_action"] = {
        "last_action": action_record,
    }
    metadata["control_plane"] = {
        **control_plane,
        "last_secretary_action": str(action_record.get("action") or "").strip(),
        "last_secretary_action_ok": bool(action_record.get("ok")),
        "last_secretary_action_at_unix": int(action_record.get("recorded_at_unix") or 0),
        "last_secretary_action_summary": str(action_record.get("summary") or action_record.get("message") or "").strip(),
        "requested_executor_override": str(action_record.get("requested_executor") or "").strip(),
    }
    return update_task(str(task.get("task_id") or "").strip(), metadata=metadata)


def _queue_operator_action(
    task: Dict[str, Any],
    *,
    kind: str,
    requested_executor: str,
    summary: str,
) -> Dict[str, Any] | None:
    metadata = _metadata(task)
    queue = metadata.get("operator_queue") if isinstance(metadata.get("operator_queue"), dict) else {}
    pending = list(queue.get("pending") or []) if isinstance(queue.get("pending"), list) else []
    executor_spec = get_executor_spec(requested_executor)
    item = {
        "queue_item_id": f"{kind}:{str(task.get('task_id') or '').strip()}:{int(time.time())}",
        "kind": str(kind or "").strip(),
        "status": "pending",
        "requested_executor": str(executor_spec.get("key") or requested_executor or "").strip(),
        "route_behavior": str(executor_spec.get("route_behavior") or "").strip(),
        "fallback_executor_key": str(executor_spec.get("fallback_executor_key") or "").strip(),
        "supports_background_job": bool(executor_spec.get("supports_background_job")),
        "summary": str(summary or "").strip(),
        "created_at_unix": int(time.time()),
    }
    pending.insert(0, item)
    metadata["operator_queue"] = {
        "pending": pending[:10],
        "last_item": item,
    }
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    metadata["control_plane"] = {
        **control_plane,
        "operator_queue_count": len(pending[:10]),
        "operator_queue_next": str(item.get("summary") or "").strip(),
    }
    return update_task(str(task.get("task_id") or "").strip(), metadata=metadata)


def execute_secretary_action(
    *,
    task_id: str,
    action: str,
    auto_safe_only: bool = True,
) -> Dict[str, Any]:
    normalized_task_id = str(task_id or "").strip()
    normalized_action = _normalize(action)
    if not normalized_task_id:
        return {
            "ok": False,
            "message": "Missing task_id.",
            "details": None,
        }
    if not normalized_action:
        return {
            "ok": False,
            "message": "Missing action.",
            "details": None,
        }
    if auto_safe_only and normalized_action not in _AUTO_SAFE_ACTIONS:
        return {
            "ok": False,
            "message": f"Action '{normalized_action}' is not marked auto_safe.",
            "details": None,
        }

    task_snapshot = sync_task_control_state(normalized_task_id)
    task = (task_snapshot or {}).get("task") if isinstance(task_snapshot, dict) else None
    if not isinstance(task, dict):
        task = get_task(normalized_task_id)
    if not isinstance(task, dict):
        return {
            "ok": False,
            "message": f"Task '{normalized_task_id}' not found.",
            "details": None,
        }

    control_plane = _control_plane(task)
    current_dispatch_action = _normalize(control_plane.get("dispatch_action"))
    if current_dispatch_action and current_dispatch_action != normalized_action:
        return {
            "ok": False,
            "message": f"Task '{normalized_task_id}' no longer requires '{normalized_action}' (current: '{current_dispatch_action}').",
            "details": {
                "task_id": normalized_task_id,
                "current_dispatch_action": current_dispatch_action,
            },
        }

    suggested_executor = _normalize(control_plane.get("suggested_executor"))
    if normalized_action == "switch_executor":
        requested_executor = suggested_executor or "hermes"
        updated_task = _record_secretary_action(
            task,
            action=normalized_action,
            ok=True,
            details={
                "requested_executor": requested_executor,
                "summary": f"Requested executor switch to '{requested_executor}'.",
                "message": "Manual operator follow-up is required before redispatch.",
            },
        ) or task
        _queue_operator_action(
            updated_task,
            kind="switch_executor",
            requested_executor=requested_executor,
            summary=f"Switch task to executor '{requested_executor}'.",
        )
        refreshed_snapshot = sync_task_control_state(normalized_task_id)
        refreshed_task = (refreshed_snapshot or {}).get("task") if isinstance(refreshed_snapshot, dict) else get_task(normalized_task_id)
        refreshed_control_plane = _control_plane(refreshed_task or {})
        return {
            "ok": True,
            "message": f"Recorded secretary action '{normalized_action}' for task '{normalized_task_id}'.",
            "details": {
                "task_id": normalized_task_id,
                "action": normalized_action,
                "requested_executor": requested_executor,
                "current_dispatch_action": str(refreshed_control_plane.get("dispatch_action") or ""),
                "suggested_executor": str(refreshed_control_plane.get("suggested_executor") or ""),
                "recovery_hint": str(refreshed_control_plane.get("recovery_hint") or ""),
                "operator_queue_count": int(refreshed_control_plane.get("operator_queue_count") or 0),
                "operator_queue_next": str(refreshed_control_plane.get("operator_queue_next") or ""),
            },
        }

    if normalized_action != "retry_delivery":
        return {
            "ok": False,
            "message": f"Unsupported secretary action '{normalized_action}'.",
            "details": {
                "task_id": normalized_task_id,
            },
        }

    job = _candidate_delivery_job(normalized_task_id)
    if not isinstance(job, dict):
        return {
            "ok": False,
            "message": f"Task '{normalized_task_id}' has no failed delivery candidate.",
            "details": {
                "task_id": normalized_task_id,
                "dispatch_action": current_dispatch_action,
            },
        }

    failed = _normalize(job.get("status")) != "completed"
    prepared_job = update_job(
        str(job.get("job_id") or ""),
        delivery_status="retrying",
        delivery_error="",
        delivered_at_unix=0,
    ) or get_job(str(job.get("job_id") or "")) or dict(job)
    updated_job = deliver_job_result(prepared_job, failed=failed, force=True) or get_job(str(job.get("job_id") or ""))
    _record_secretary_action(
        task,
        action=normalized_action,
        ok=bool(updated_job),
        details={
            "job_id": str((updated_job or prepared_job).get("job_id") or ""),
            "delivery_status": str((updated_job or prepared_job).get("delivery_status") or ""),
            "delivery_error": str((updated_job or prepared_job).get("delivery_error") or ""),
            "summary": (
                f"Retried delivery for job '{str((updated_job or prepared_job).get('job_id') or '')}' -> "
                f"{str((updated_job or prepared_job).get('delivery_status') or '') or 'unknown'}."
            ),
        },
    )
    refreshed_snapshot = sync_task_control_state(normalized_task_id)
    refreshed_task = (refreshed_snapshot or {}).get("task") if isinstance(refreshed_snapshot, dict) else get_task(normalized_task_id)
    refreshed_control_plane = _control_plane(refreshed_task or {})

    return {
        "ok": True,
        "message": f"Executed secretary action '{normalized_action}' for task '{normalized_task_id}'.",
        "details": {
            "task_id": normalized_task_id,
            "action": normalized_action,
            "job_id": str((updated_job or prepared_job).get("job_id") or ""),
            "job_status": str((updated_job or prepared_job).get("status") or ""),
            "delivery_status": str((updated_job or prepared_job).get("delivery_status") or ""),
            "delivery_error": str((updated_job or prepared_job).get("delivery_error") or ""),
            "current_dispatch_action": str(refreshed_control_plane.get("dispatch_action") or ""),
            "recovery_hint": str(refreshed_control_plane.get("recovery_hint") or ""),
        },
    }
