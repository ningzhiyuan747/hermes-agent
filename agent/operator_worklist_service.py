from __future__ import annotations

import time
from typing import Any, Dict

from agent.business_db import get_task, list_tasks, update_task
from agent.task_panel_service import sync_task_control_state


def _metadata(task: Dict[str, Any]) -> Dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    return dict(metadata)


def _operator_queue(task: Dict[str, Any]) -> Dict[str, Any]:
    metadata = _metadata(task)
    queue = metadata.get("operator_queue") if isinstance(metadata.get("operator_queue"), dict) else {}
    return dict(queue)


def list_operator_worklist(*, limit: int = 20) -> Dict[str, Any]:
    items: list[Dict[str, Any]] = []
    tasks = list_tasks(limit=max(50, limit * 5))
    for task in tasks:
        queue = _operator_queue(task)
        pending = list(queue.get("pending") or []) if isinstance(queue.get("pending"), list) else []
        for item in pending:
            if not isinstance(item, dict):
                continue
            items.append(
                {
                    "queue_item_id": str(item.get("queue_item_id") or "").strip(),
                    "task_id": str(task.get("task_id") or "").strip(),
                    "task_title": str(task.get("title") or task.get("goal") or "").strip(),
                    "kind": str(item.get("kind") or "").strip(),
                    "status": str(item.get("status") or "pending").strip(),
                    "requested_executor": str(item.get("requested_executor") or "").strip(),
                    "route_behavior": str(item.get("route_behavior") or "").strip(),
                    "fallback_executor_key": str(item.get("fallback_executor_key") or "").strip(),
                    "supports_background_job": bool(item.get("supports_background_job")),
                    "summary": str(item.get("summary") or "").strip(),
                    "created_at_unix": int(item.get("created_at_unix") or 0),
                }
            )
    items.sort(
        key=lambda item: (
            -int(item.get("created_at_unix") or 0),
            str(item.get("queue_item_id") or ""),
        )
    )
    return {
        "generated_at_unix": int(time.time()),
        "all_item_count": len(items),
        "items": items[: max(1, limit)],
    }


def complete_operator_queue_item(
    *,
    task_id: str,
    queue_item_id: str,
    resolution: str = "completed",
    note: str = "",
) -> Dict[str, Any]:
    normalized_task_id = str(task_id or "").strip()
    normalized_queue_item_id = str(queue_item_id or "").strip()
    normalized_resolution = str(resolution or "completed").strip().lower() or "completed"
    if not normalized_task_id:
        return {"ok": False, "message": "Missing task_id.", "details": None}
    if not normalized_queue_item_id:
        return {"ok": False, "message": "Missing queue_item_id.", "details": None}
    task = get_task(normalized_task_id)
    if not isinstance(task, dict):
        return {"ok": False, "message": f"Task '{normalized_task_id}' not found.", "details": None}

    metadata = _metadata(task)
    queue = metadata.get("operator_queue") if isinstance(metadata.get("operator_queue"), dict) else {}
    pending = list(queue.get("pending") or []) if isinstance(queue.get("pending"), list) else []
    completed = list(queue.get("completed") or []) if isinstance(queue.get("completed"), list) else []

    matched: Dict[str, Any] | None = None
    remaining: list[Dict[str, Any]] = []
    for item in pending:
        if not isinstance(item, dict):
            continue
        if str(item.get("queue_item_id") or "").strip() == normalized_queue_item_id and matched is None:
            matched = dict(item)
            continue
        remaining.append(dict(item))

    if not isinstance(matched, dict):
        return {
            "ok": False,
            "message": f"Queue item '{normalized_queue_item_id}' not found on task '{normalized_task_id}'.",
            "details": None,
        }

    finished_item = {
        **matched,
        "status": normalized_resolution,
        "resolved_at_unix": int(time.time()),
        "resolution_note": str(note or "").strip(),
    }
    completed.insert(0, finished_item)
    metadata["operator_queue"] = {
        "pending": remaining[:10],
        "completed": completed[:20],
        "last_item": finished_item,
    }
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    metadata["control_plane"] = {
        **control_plane,
        "operator_queue_count": len(remaining[:10]),
        "operator_queue_next": str((remaining[0].get("summary") if remaining else "") or "").strip(),
        "last_operator_resolution": normalized_resolution,
        "last_operator_resolution_at_unix": int(finished_item.get("resolved_at_unix") or 0),
        "last_operator_resolution_summary": str(finished_item.get("summary") or "").strip(),
    }
    update_task(normalized_task_id, metadata=metadata)
    sync_task_control_state(normalized_task_id)
    return {
        "ok": True,
        "message": f"Resolved queue item '{normalized_queue_item_id}' on task '{normalized_task_id}'.",
        "details": {
            "task_id": normalized_task_id,
            "queue_item_id": normalized_queue_item_id,
            "resolution": normalized_resolution,
            "remaining_count": len(remaining[:10]),
        },
    }
