from __future__ import annotations

from typing import Any, Dict, List, Tuple

from agent.business_db import get_channel_task, list_approvals


def get_scope_task_context(*, platform: str, chat_id: str = "", thread_id: str = "") -> Tuple[str, str]:
    if not platform or not chat_id:
        return "", ""
    try:
        record = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id) or {}
    except Exception:
        return "", ""
    task = record.get("task") if isinstance(record, dict) else {}
    if not isinstance(task, dict):
        return "", ""
    return str(task.get("task_id") or "").strip(), str(task.get("title") or "").strip()


def matches_scope(
    record: Dict[str, Any],
    *,
    platform: str,
    chat_id: str = "",
    thread_id: str = "",
    task_id: str = "",
    scope_all: bool = False,
) -> bool:
    if scope_all:
        return True
    payload = record.get("payload") if isinstance(record.get("payload"), dict) else {}
    payload_task_id = str(payload.get("task_id") or "").strip()
    if task_id:
        return payload_task_id == task_id
    origin = payload.get("origin") if isinstance(payload, dict) else {}
    if not isinstance(origin, dict):
        return False
    origin_platform = str(origin.get("platform") or "").strip().lower()
    origin_chat_id = str(origin.get("chat_id") or "").strip()
    origin_thread_id = str(origin.get("thread_id") or "").strip()
    if platform and origin_platform and origin_platform != str(platform or "").strip().lower():
        return False
    if chat_id and origin_chat_id and origin_chat_id != str(chat_id or "").strip():
        return False
    if thread_id and origin_thread_id and origin_thread_id != str(thread_id or "").strip():
        return False
    if chat_id and not origin_chat_id:
        return False
    return True


def list_scoped_approvals(
    *,
    platform: str,
    chat_id: str = "",
    thread_id: str = "",
    scope_all: bool = False,
    status: str = "pending",
    limit: int = 50,
) -> Tuple[List[Dict[str, Any]], str, str]:
    task_id, task_title = get_scope_task_context(platform=platform, chat_id=chat_id, thread_id=thread_id)
    rows = list_approvals(status=status, limit=limit)
    approvals = [
        item
        for item in rows
        if matches_scope(
            item,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            task_id=task_id,
            scope_all=scope_all,
        )
    ]
    return approvals, task_id, task_title
