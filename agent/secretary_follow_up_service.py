from __future__ import annotations

import os
import time
from typing import Any, Dict

from agent.business_db import get_task, update_task
from agent.outbound_delivery import DeliveryTarget, send_text_to_target
from agent.secretary_loop_service import build_secretary_loop_snapshot
from agent.task_panel_service import sync_task_control_state


def _normalize(value: Any) -> str:
    return str(value or "").strip()


def _follow_up_cooldown_seconds() -> int:
    raw = _normalize(os.getenv("HERMES_SECRETARY_FOLLOW_UP_COOLDOWN_SECONDS"))
    try:
        return max(300, int(raw or "14400"))
    except ValueError:
        return 14400


def _normalize_levels(levels: Any) -> tuple[str, ...]:
    if levels is None:
        return tuple()
    if isinstance(levels, str):
        raw_values = levels.split(",")
    elif isinstance(levels, (list, tuple, set)):
        raw_values = levels
    else:
        raw_values = [levels]
    normalized = tuple(
        value
        for value in (_normalize(item).lower() for item in raw_values)
        if value
    )
    return normalized


def _find_follow_up_item(*, task_id: str = "", action_id: str = "") -> Dict[str, Any] | None:
    snapshot = build_secretary_loop_snapshot(limit=100)
    items = snapshot.get("follow_up_items") if isinstance(snapshot.get("follow_up_items"), list) else []
    normalized_task_id = _normalize(task_id)
    normalized_action_id = _normalize(action_id)
    for item in items:
        if not isinstance(item, dict):
            continue
        if normalized_action_id and _normalize(item.get("action_id")) == normalized_action_id:
            return dict(item)
        if normalized_task_id and _normalize(item.get("task_id")) == normalized_task_id:
            return dict(item)
    return None


def _candidate_follow_up_items(*, limit: int = 5, levels: Any = None) -> list[Dict[str, Any]]:
    snapshot = build_secretary_loop_snapshot(limit=max(20, int(limit or 0), 1))
    items = snapshot.get("follow_up_items") if isinstance(snapshot.get("follow_up_items"), list) else []
    allowed_levels = set(_normalize_levels(levels))
    candidates: list[Dict[str, Any]] = []
    for item in items:
        if not isinstance(item, dict):
            continue
        if _normalize(item.get("target_kind")) != "origin_chat":
            continue
        if not _normalize(item.get("target_ref")):
            continue
        level = _normalize(item.get("follow_up_level")).lower()
        if allowed_levels and level not in allowed_levels:
            continue
        candidates.append(dict(item))
    candidates.sort(key=lambda item: int(item.get("stale_for_seconds") or 0), reverse=True)
    return candidates[: max(1, int(limit or 1))]


def _record_follow_up(task: Dict[str, Any], *, ok: bool, item: Dict[str, Any], response: Dict[str, Any] | None) -> Dict[str, Any] | None:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    metadata = dict(metadata)
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    recorded_at_unix = int(time.time())
    record = {
        "action_id": _normalize(item.get("action_id")),
        "task_id": _normalize(item.get("task_id")),
        "target_ref": _normalize(item.get("target_ref")),
        "target_kind": _normalize(item.get("target_kind")),
        "follow_up_level": _normalize(item.get("follow_up_level")),
        "summary": _normalize(item.get("summary")),
        "ok": bool(ok),
        "recorded_at_unix": recorded_at_unix,
    }
    if isinstance(response, dict) and response:
        record["response"] = dict(response)
    metadata["secretary_follow_up"] = {
        "last_item": record,
    }
    metadata["control_plane"] = {
        **control_plane,
        "last_follow_up_action_id": str(record.get("action_id") or "").strip(),
        "last_follow_up_ok": bool(record.get("ok")),
        "last_follow_up_at_unix": recorded_at_unix,
        "last_follow_up_summary": str(record.get("summary") or "").strip(),
        "last_follow_up_target_ref": str(record.get("target_ref") or "").strip(),
    }
    return update_task(_normalize(task.get("task_id")), metadata=metadata)


def _cooldown_status(task: Dict[str, Any], item: Dict[str, Any]) -> Dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    follow_up_state = metadata.get("secretary_follow_up") if isinstance(metadata.get("secretary_follow_up"), dict) else {}
    last_item = follow_up_state.get("last_item") if isinstance(follow_up_state.get("last_item"), dict) else {}
    now_unix = int(time.time())
    cooldown_seconds = _follow_up_cooldown_seconds()
    action_id = _normalize(item.get("action_id"))
    target_ref = _normalize(item.get("target_ref"))
    last_action_id = _normalize(last_item.get("action_id"))
    last_target_ref = _normalize(last_item.get("target_ref"))
    last_recorded_at_unix = int(last_item.get("recorded_at_unix") or 0)
    matches_last = bool(
        action_id
        and target_ref
        and action_id == last_action_id
        and target_ref == last_target_ref
    )
    next_allowed_at_unix = last_recorded_at_unix + cooldown_seconds if matches_last and last_recorded_at_unix else 0
    cooldown_active = bool(matches_last and next_allowed_at_unix > now_unix)
    return {
        "cooldown_seconds": cooldown_seconds,
        "matches_last": matches_last,
        "last_recorded_at_unix": last_recorded_at_unix,
        "next_allowed_at_unix": next_allowed_at_unix,
        "cooldown_active": cooldown_active,
        "remaining_cooldown_seconds": max(0, next_allowed_at_unix - now_unix) if cooldown_active else 0,
    }


def execute_secretary_follow_up(
    *,
    task_id: str = "",
    action_id: str = "",
    dry_run: bool = False,
) -> Dict[str, Any]:
    item = _find_follow_up_item(task_id=task_id, action_id=action_id)
    if not isinstance(item, dict):
        return {
            "ok": False,
            "message": "No matching follow-up item found.",
            "details": None,
        }

    normalized_task_id = _normalize(item.get("task_id"))
    task_snapshot = sync_task_control_state(normalized_task_id)
    task = (task_snapshot or {}).get("task") if isinstance(task_snapshot, dict) else None
    if not isinstance(task, dict):
        task = get_task(normalized_task_id)
    if not isinstance(task, dict):
        return {
            "ok": False,
            "message": f"Task '{normalized_task_id}' not found.",
            "details": item,
        }

    target_kind = _normalize(item.get("target_kind"))
    target_ref = _normalize(item.get("target_ref"))
    if target_kind != "origin_chat":
        return {
            "ok": False,
            "message": f"Follow-up target kind '{target_kind or 'unknown'}' is not yet sendable.",
            "details": item,
        }
    if not target_ref:
        return {
            "ok": False,
            "message": "Follow-up target is missing.",
            "details": item,
        }
    if dry_run:
        return {
            "ok": True,
            "message": f"Prepared secretary follow-up for task '{normalized_task_id}'.",
            "details": {
                **item,
                **_cooldown_status(task, item),
                "dry_run": True,
            },
        }
    cooldown = _cooldown_status(task, item)
    if bool(cooldown.get("cooldown_active")):
        return {
            "ok": False,
            "message": (
                f"Secretary follow-up for task '{normalized_task_id}' is cooling down for "
                f"{int(cooldown.get('remaining_cooldown_seconds') or 0)}s."
            ),
            "details": {
                **item,
                **cooldown,
            },
        }

    target = DeliveryTarget(
        platform=_normalize(item.get("target_platform")),
        chat_id=_normalize(item.get("target_chat_id")),
        thread_id=_normalize(item.get("target_thread_id")),
    )
    if not target.is_valid():
        return {
            "ok": False,
            "message": "Follow-up target is invalid.",
            "details": item,
        }
    response = send_text_to_target(target, _normalize(item.get("message_preview")))
    updated_task = _record_follow_up(task, ok=True, item=item, response=response) or task
    sync_task_control_state(_normalize(updated_task.get("task_id")))
    return {
        "ok": True,
        "message": f"Sent secretary follow-up for task '{normalized_task_id}' to '{target_ref}'.",
        "details": {
            **item,
            **cooldown,
            "delivery_response": response,
        },
    }


def execute_due_secretary_follow_ups(
    *,
    limit: int = 3,
    levels: Any = None,
    dry_run: bool = False,
) -> Dict[str, Any]:
    normalized_levels = _normalize_levels(levels)
    candidates = _candidate_follow_up_items(limit=max(1, int(limit or 1)), levels=normalized_levels)
    results = [
        execute_secretary_follow_up(action_id=_normalize(item.get("action_id")), dry_run=dry_run)
        for item in candidates
        if _normalize(item.get("action_id"))
    ]
    ok_count = sum(1 for item in results if bool(item.get("ok")))
    blocked_count = len(results) - ok_count
    mode = "previewed" if dry_run else "sent"
    return {
        "ok": True,
        "message": (
            f"Secretary follow-up batch {mode}: "
            f"{ok_count}/{len(results)} succeeded from {len(candidates)} candidate(s)."
        ),
        "details": {
            "dry_run": bool(dry_run),
            "limit": max(1, int(limit or 1)),
            "levels": list(normalized_levels),
            "candidate_count": len(candidates),
            "ok_count": ok_count,
            "blocked_count": blocked_count,
            "results": results,
        },
    }
