from __future__ import annotations

import os
import time
from collections import Counter
from pathlib import Path
from typing import Any

from agent.background_jobs import list_jobs
from agent.business_db import get_task, list_capability_runs, list_tasks
from agent.task_reconcile_service import reconcile_task_records
from scripts.subagent_task_status import _load_task_meta

ACTIVE_TASK_STATUSES = {"open", "queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused", "blocked"}
ACTIVE_SUBAGENT_STATUSES = {"created", "running"}


def _contains(text: Any, needle: str) -> bool:
    return needle in str(text or "").strip().lower()


def _derive_system_signals(*, runs: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    openclaw_smokes = [
        row
        for row in jobs
        if str(row.get("status") or "").strip().lower() == "completed"
        and _contains(row.get("executor"), "openclaw")
        and (
            _contains(row.get("title"), "smoke")
            or _contains(row.get("result"), "openclaw_launch_ok")
            or _contains(row.get("result"), "service_smoke_ok")
        )
    ]
    stale_launch_blockers = [
        row
        for row in runs + jobs
        if _contains(row.get("blocker"), "capture_output")
    ]
    if openclaw_smokes:
        latest_smoke = max(int(row.get("updated_at_unix") or 0) for row in openclaw_smokes)
        stale_after_smoke = [
            row
            for row in stale_launch_blockers
            if int(row.get("updated_at_unix") or 0) <= latest_smoke
        ]
        if stale_after_smoke:
            signals.append(
                "OpenClaw 启动冒烟已成功，旧的 capture_output/Popen 启动故障应视为历史阻塞；"
                f"当前仍有 {len(stale_after_smoke)} 条旧记录需要改为重试/回填，而不是继续把启动故障当成当前根因。"
            )
    feishu_delivery_auth_failures = [
        row
        for row in jobs
        if _contains(row.get("delivery_error"), "app_id or app_secret not found")
    ]
    if feishu_delivery_auth_failures:
        signals.append(
            f"飞书投递凭证仍未就绪（app_id/app_secret 缺失）并影响 {len(feishu_delivery_auth_failures)} 个后台任务；"
            "当前更值得优先修复的是投递鉴权，而不是再次排查 OpenClaw 启动。"
        )
    origin_delivery_failures = [
        row
        for row in jobs
        if _contains(row.get("delivery_error"), "no delivery target resolved for deliver=origin")
    ]
    if origin_delivery_failures:
        signals.append(
            f"仍有 {len(origin_delivery_failures)} 个任务/定时作业使用 deliver=origin 但缺少可解析目标；"
            "需要把统一任务板中的会话/投递目标映射补齐。"
        )
    return signals


def _status_counts(rows: list[dict[str, Any]], key: str = "status") -> dict[str, int]:
    counter = Counter(str(row.get(key) or "unknown").strip() or "unknown" for row in rows)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _top_nonempty_counts(values: list[str], *, limit: int = 5) -> dict[str, int]:
    counter = Counter(str(value or "").strip() for value in values if str(value or "").strip())
    return dict(counter.most_common(max(1, limit)))


def _follow_up_cooldown_seconds() -> int:
    raw = str(os.getenv("HERMES_SECRETARY_FOLLOW_UP_COOLDOWN_SECONDS") or "").strip()
    try:
        return max(300, int(raw or "14400"))
    except ValueError:
        return 14400


def _build_scope_fields(
    *,
    origin: dict[str, Any] | None,
    actor_user_id: str = "",
    task_id: str = "",
) -> dict[str, str]:
    origin = origin if isinstance(origin, dict) else {}
    platform = str(origin.get("platform") or "").strip().lower()
    chat_id = str(origin.get("chat_id") or "").strip()
    thread_id = str(origin.get("thread_id") or "").strip()
    actor_user_id = str(actor_user_id or "").strip()
    task_id = str(task_id or "").strip()

    task_scope_key = ""
    if platform and chat_id:
        task_scope_key = f"{platform}:chat:{chat_id}"
        if thread_id:
            task_scope_key += f":thread:{thread_id}"
    elif task_id:
        task_scope_key = f"task:{task_id}"

    person_memory_key = ""
    if platform and actor_user_id:
        person_memory_key = f"{platform}:user:{actor_user_id}"
    elif actor_user_id:
        person_memory_key = f"user:{actor_user_id}"

    if platform == "dingtalk" and chat_id:
        conversation_role = "task_group"
    elif chat_id:
        conversation_role = "chat_surface"
    elif task_id:
        conversation_role = "task_unit"
    else:
        conversation_role = "system"

    return {
        "origin_chat_id": chat_id,
        "origin_thread_id": thread_id,
        "actor_user_id": actor_user_id,
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "conversation_role": conversation_role,
    }


def _run_unit(row: dict[str, Any]) -> dict[str, Any]:
    origin = row.get("origin") if isinstance(row.get("origin"), dict) else {}
    run_id = str(row.get("run_id") or "").strip()
    session_id = str(row.get("session_id") or "").strip()
    actor_user_id = str(row.get("actor_user_id") or "").strip()
    task_id = str(row.get("task_id") or "").strip()
    scopes = _build_scope_fields(origin=origin, actor_user_id=actor_user_id, task_id=task_id)
    return {
        "unit_id": f"run:{run_id}",
        "unit_type": "capability_run",
        "title": str(row.get("title") or row.get("goal") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "owner": str(row.get("session_id") or actor_user_id or "").strip(),
        "origin_platform": str(origin.get("platform") or "").strip().lower(),
        "current_focus": str(row.get("current_focus") or "").strip(),
        "next_step": str(row.get("next_step") or "").strip(),
        "blocker": str(row.get("blocker") or "").strip(),
        "updated_at_unix": int(row.get("updated_at_unix") or 0),
        "priority_hint": 100 if str(row.get("status") or "").strip().lower() in ACTIVE_RUN_STATUSES else 10,
        "related_ids": {
            "run_id": run_id,
            "session_id": session_id,
            "task_id": task_id,
            "background_job_id": str(row.get("background_job_id") or "").strip(),
            "approval_id": str(row.get("approval_id") or "").strip(),
        },
        **scopes,
        "raw": row,
    }


def _task_unit(row: dict[str, Any]) -> dict[str, Any]:
    task_id = str(row.get("task_id") or "").strip()
    source_session_id = str(row.get("source_session_id") or "").strip()
    metadata = row.get("metadata") if isinstance(row.get("metadata"), dict) else {}
    control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    origin = {
        "platform": str(row.get("source_platform") or "").strip().lower(),
        "chat_id": str(row.get("source_chat_id") or "").strip(),
        "thread_id": str(row.get("source_thread_id") or "").strip(),
    }
    scopes = _build_scope_fields(
        origin=origin,
        actor_user_id=str(row.get("owner_user_id") or "").strip(),
        task_id=task_id,
    )
    task_scope_key = str(control_plane.get("task_scope_key") or "").strip() or str(scopes.get("task_scope_key") or "").strip()
    person_memory_key = str(control_plane.get("person_memory_key") or "").strip() or str(scopes.get("person_memory_key") or "").strip()
    conversation_role = str(scopes.get("conversation_role") or "task_unit").strip()
    if task_scope_key.startswith("dingtalk:chat:"):
        conversation_role = "task_group"
    elif task_scope_key.startswith("task:"):
        conversation_role = "task_unit"
    elif task_scope_key:
        conversation_role = "chat_surface"
    status = str(control_plane.get("status") or row.get("status") or "unknown").strip().lower()
    current_focus = str(control_plane.get("current_focus") or "").strip()
    next_step = str(control_plane.get("next_step") or "").strip()
    blocker = str(control_plane.get("blocker") or "").strip()
    failure_kind = str(control_plane.get("failure_kind") or "").strip()
    execution_status = str(control_plane.get("execution_status") or "").strip()
    delivery_status = str(control_plane.get("delivery_status") or "").strip()
    recovery_hint = str(control_plane.get("recovery_hint") or "").strip()
    dispatch_action = str(control_plane.get("dispatch_action") or "").strip()
    suggested_executor = str(control_plane.get("suggested_executor") or "").strip()
    last_secretary_action = str(control_plane.get("last_secretary_action") or "").strip()
    last_secretary_action_summary = str(control_plane.get("last_secretary_action_summary") or "").strip()
    requested_executor_override = str(control_plane.get("requested_executor_override") or "").strip()
    last_follow_up_action_id = str(control_plane.get("last_follow_up_action_id") or "").strip()
    last_follow_up_ok = bool(control_plane.get("last_follow_up_ok"))
    last_follow_up_at_unix = int(control_plane.get("last_follow_up_at_unix") or 0)
    last_follow_up_summary = str(control_plane.get("last_follow_up_summary") or "").strip()
    last_follow_up_target_ref = str(control_plane.get("last_follow_up_target_ref") or "").strip()
    operator_queue_count = int(control_plane.get("operator_queue_count") or 0)
    operator_queue_next = str(control_plane.get("operator_queue_next") or "").strip()
    last_operator_resolution = str(control_plane.get("last_operator_resolution") or "").strip()
    last_operator_resolution_summary = str(control_plane.get("last_operator_resolution_summary") or "").strip()
    activity_updated_at_unix = int(control_plane.get("activity_updated_at_unix") or 0)
    is_active = status in ACTIVE_TASK_STATUSES and (
        status != "open" or bool(current_focus or next_step or blocker)
    )
    return {
        "unit_id": f"task:{task_id}",
        "unit_type": "task",
        "title": str(row.get("title") or row.get("goal") or "").strip(),
        "status": status,
        "owner": str(row.get("source_session_id") or row.get("owner_user_id") or "").strip(),
        "origin_platform": str(row.get("source_platform") or "").strip().lower(),
        "current_focus": current_focus,
        "next_step": next_step,
        "blocker": blocker,
        "failure_kind": failure_kind,
        "execution_status": execution_status,
        "delivery_status": delivery_status,
        "recovery_hint": recovery_hint,
        "dispatch_action": dispatch_action,
        "suggested_executor": suggested_executor,
        "last_secretary_action": last_secretary_action,
        "last_secretary_action_summary": last_secretary_action_summary,
        "requested_executor_override": requested_executor_override,
        "last_follow_up_action_id": last_follow_up_action_id,
        "last_follow_up_ok": last_follow_up_ok,
        "last_follow_up_at_unix": last_follow_up_at_unix,
        "last_follow_up_summary": last_follow_up_summary,
        "last_follow_up_target_ref": last_follow_up_target_ref,
        "operator_queue_count": operator_queue_count,
        "operator_queue_next": operator_queue_next,
        "last_operator_resolution": last_operator_resolution,
        "last_operator_resolution_summary": last_operator_resolution_summary,
        "updated_at_unix": activity_updated_at_unix or int(row.get("updated_at_unix") or 0),
        "task_record_updated_at_unix": int(row.get("updated_at_unix") or 0),
        "priority_hint": 120 if is_active else 15,
        "is_active": is_active,
        "related_ids": {
            "task_id": task_id,
            "source_session_id": source_session_id,
            "owner_user_id": str(row.get("owner_user_id") or "").strip(),
            "source_chat_id": str(row.get("source_chat_id") or "").strip(),
            "source_thread_id": str(row.get("source_thread_id") or "").strip(),
        },
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "conversation_role": conversation_role,
        "raw": row,
    }


def _job_tag_value(row: dict[str, Any], prefix: str) -> str:
    expected = f"{str(prefix or '').strip().lower()}:"
    if not expected or expected == ":":
        return ""
    for tag in row.get("tags") or []:
        raw_tag = str(tag or "").strip()
        if raw_tag.lower().startswith(expected):
            return raw_tag.split(":", 1)[1].strip()
    return ""


def _job_capability_run_id(row: dict[str, Any]) -> str:
    return _job_tag_value(row, "capability_run")


def _job_unit(row: dict[str, Any]) -> dict[str, Any]:
    job_id = str(row.get("job_id") or "").strip()
    session_id = str(row.get("session_id") or row.get("user_id") or "").strip()
    task_scope_key = _job_tag_value(row, "task_scope")
    person_memory_key = _job_tag_value(row, "person_memory")
    if task_scope_key.startswith("dingtalk:chat:"):
        conversation_role = "task_group"
    elif task_scope_key.startswith("task:"):
        conversation_role = "task_unit"
    elif task_scope_key:
        conversation_role = "chat_surface"
    else:
        conversation_role = "system"
    return {
        "unit_id": f"job:{job_id}",
        "unit_type": "background_job",
        "title": str(row.get("title") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "delivery_status": str(row.get("delivery_status") or "").strip().lower(),
        "owner": session_id,
        "origin_platform": task_scope_key.split(":", 1)[0] if ":" in task_scope_key else "",
        "current_focus": str(row.get("current_focus") or "").strip(),
        "next_step": str(row.get("next_step") or "").strip(),
        "blocker": str(row.get("blocker") or "").strip(),
        "updated_at_unix": int(row.get("updated_at_unix") or 0),
        "priority_hint": 80 if str(row.get("status") or "").strip().lower() in ACTIVE_JOB_STATUSES else 20,
        "related_ids": {
            "job_id": job_id,
            "session_id": session_id,
            "capability_run_id": _job_capability_run_id(row),
            "executor": str(row.get("executor") or "").strip(),
        },
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "conversation_role": conversation_role,
        "raw": row,
    }


def _scope_fields_from_task_scope_key(task_scope_key: str) -> dict[str, str]:
    normalized = str(task_scope_key or "").strip()
    if normalized.startswith("dingtalk:chat:"):
        conversation_role = "task_group"
    elif normalized.startswith("task:"):
        conversation_role = "task_unit"
    elif normalized:
        conversation_role = "chat_surface"
    else:
        conversation_role = "system"
    return {
        "task_scope_key": normalized,
        "conversation_role": conversation_role,
        "origin_platform": normalized.split(":", 1)[0] if ":" in normalized else "",
    }


def _scope_fields_from_session_key(session_id: str) -> dict[str, str]:
    parts = str(session_id or "").strip().split(":")
    if len(parts) < 5 or parts[0] != "agent" or parts[1] != "main":
        return {
            "task_scope_key": "",
            "person_memory_key": "",
            "conversation_role": "system",
            "origin_platform": "",
        }
    platform = str(parts[2] or "").strip().lower()
    chat_type = str(parts[3] or "").strip().lower()
    chat_id = str(parts[4] or "").strip()
    thread_id = str(parts[5] or "").strip() if len(parts) > 5 and chat_type in {"dm", "thread"} else ""
    if not platform or not chat_id:
        return {
            "task_scope_key": "",
            "person_memory_key": "",
            "conversation_role": "system",
            "origin_platform": platform,
        }
    task_scope_key = f"{platform}:chat:{chat_id}"
    if thread_id:
        task_scope_key += f":thread:{thread_id}"
    fields = _scope_fields_from_task_scope_key(task_scope_key)
    fields["person_memory_key"] = ""
    return fields


def _session_scope_index(
    *,
    task_units: list[dict[str, Any]],
    run_units: list[dict[str, Any]],
    job_units: list[dict[str, Any]],
) -> dict[str, dict[str, Any]]:
    indexed: dict[str, dict[str, Any]] = {}
    run_units_by_id = {
        str(unit.get("related_ids", {}).get("run_id") or "").strip(): unit
        for unit in run_units
        if str(unit.get("related_ids", {}).get("run_id") or "").strip()
    }
    type_priority = {
        "task": 0,
        "capability_run": 0,
        "background_job": 1,
        "delegation_task": 2,
    }
    for unit in sorted(
        task_units + run_units + job_units,
        key=lambda item: (
            type_priority.get(str(item.get("unit_type") or "").strip(), 9),
            -int(item.get("updated_at_unix") or 0),
            item.get("unit_id") or "",
        ),
    ):
        session_id = str(unit.get("owner") or "").strip()
        if not session_id or session_id in indexed:
            continue
        candidate = unit
        if str(unit.get("unit_type") or "").strip() == "background_job":
            related_run_id = str(unit.get("related_ids", {}).get("capability_run_id") or "").strip()
            related_run = run_units_by_id.get(related_run_id)
            if isinstance(related_run, dict) and (
                str(related_run.get("task_scope_key") or "").strip()
                or str(related_run.get("person_memory_key") or "").strip()
            ):
                candidate = related_run
        if not (str(candidate.get("task_scope_key") or "").strip() or str(candidate.get("person_memory_key") or "").strip()):
            continue
        indexed[session_id] = candidate
    return indexed


def _subagent_scope_fields(
    row: dict[str, Any],
    *,
    parent_scopes: dict[str, dict[str, Any]],
) -> dict[str, str]:
    explicit_task_scope_key = str(row.get("task_scope_key") or "").strip()
    explicit_person_memory_key = str(row.get("person_memory_key") or "").strip()
    explicit_conversation_role = str(row.get("conversation_role") or "").strip()
    explicit_origin_platform = str(row.get("origin_platform") or "").strip().lower()
    if explicit_task_scope_key or explicit_person_memory_key:
        fields = _scope_fields_from_task_scope_key(explicit_task_scope_key)
        fields["person_memory_key"] = explicit_person_memory_key
        if explicit_conversation_role:
            fields["conversation_role"] = explicit_conversation_role
        if explicit_origin_platform:
            fields["origin_platform"] = explicit_origin_platform
        return fields

    control_task_id = str(row.get("control_task_id") or "").strip()
    if control_task_id:
        control_task = get_task(control_task_id)
        if isinstance(control_task, dict):
            metadata = control_task.get("metadata") if isinstance(control_task.get("metadata"), dict) else {}
            control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
            task_scope_key = str(control_plane.get("task_scope_key") or "").strip()
            person_memory_key = str(control_plane.get("person_memory_key") or "").strip()
            if task_scope_key or person_memory_key:
                fields = _scope_fields_from_task_scope_key(task_scope_key)
                fields["person_memory_key"] = person_memory_key
                return fields
        fields = _scope_fields_from_task_scope_key(f"task:{control_task_id}")
        fields["person_memory_key"] = ""
        return fields

    parent_session_id = str(row.get("parent_session_id") or "").strip()
    parent_unit = parent_scopes.get(parent_session_id) if parent_session_id else None
    if isinstance(parent_unit, dict):
        task_scope_key = str(parent_unit.get("task_scope_key") or "").strip()
        fields = _scope_fields_from_task_scope_key(task_scope_key)
        fields["person_memory_key"] = str(parent_unit.get("person_memory_key") or "").strip()
        inherited_role = str(parent_unit.get("conversation_role") or "").strip()
        if inherited_role:
            fields["conversation_role"] = inherited_role
        inherited_origin = str(parent_unit.get("origin_platform") or "").strip().lower()
        if inherited_origin:
            fields["origin_platform"] = inherited_origin
        return fields

    return _scope_fields_from_session_key(parent_session_id)


def _subagent_unit(row: dict[str, Any], *, parent_scopes: dict[str, dict[str, Any]]) -> dict[str, Any]:
    task_id = str(row.get("task_id") or row.get("id") or row.get("session_id") or "").strip()
    if not task_id:
        meta_path = str(row.get("_meta_path") or "").strip()
        if meta_path:
            task_id = Path(meta_path).parent.name
    if not task_id:
        task_id = str(row.get("parent_session_id") or "").strip()
    if not task_id:
        created_at = int(row.get("created_at_unix") or 0)
        task_id = f"delegation-{created_at}" if created_at else "unknown"
    scopes = _subagent_scope_fields(row, parent_scopes=parent_scopes)
    return {
        "unit_id": f"subagent:{task_id}",
        "unit_type": "delegation_task",
        "title": str(row.get("goal") or row.get("title") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "owner": str(row.get("worker_role") or "generic").strip(),
        "origin_platform": str(scopes.get("origin_platform") or "").strip(),
        "current_focus": str(row.get("current_focus") or "").strip(),
        "next_step": str(row.get("next_step") or "").strip(),
        "blocker": str(row.get("blocker") or "").strip(),
        "updated_at_unix": int(row.get("started_at_unix") or row.get("created_at_unix") or 0),
        "priority_hint": 90 if str(row.get("status") or "").strip().lower() in ACTIVE_SUBAGENT_STATUSES else 10,
        "related_ids": {
            "delegation_task_id": task_id,
            "parent_session_id": str(row.get("parent_session_id") or "").strip(),
            "control_task_id": str(row.get("control_task_id") or "").strip(),
        },
        "task_scope_key": str(scopes.get("task_scope_key") or "").strip(),
        "person_memory_key": str(scopes.get("person_memory_key") or "").strip(),
        "conversation_role": str(scopes.get("conversation_role") or "system").strip(),
        "raw": row,
    }


def build_operational_task_snapshot(*, limit: int = 20) -> dict[str, Any]:
    reconcile_summary: dict[str, Any] = {}
    try:
        reconcile_summary = reconcile_task_records(limit=max(20, limit * 4))
    except Exception:
        reconcile_summary = {}
    tasks = list_tasks(limit=max(20, limit * 4))
    runs = list_capability_runs(limit=max(20, limit * 4))
    jobs = list_jobs(limit=max(20, limit * 4), active_only=False)
    subagents = _load_task_meta()

    task_units = [_task_unit(row) for row in tasks]
    run_units = [_run_unit(row) for row in runs]
    job_units = [_job_unit(row) for row in jobs]
    parent_scopes = _session_scope_index(task_units=task_units, run_units=run_units, job_units=job_units)
    subagent_units = [_subagent_unit(row, parent_scopes=parent_scopes) for row in subagents]
    units = task_units + run_units + job_units + subagent_units
    units.sort(key=lambda item: (-int(item.get("updated_at_unix") or 0), -int(item.get("priority_hint") or 0), item.get("unit_id") or ""))
    scoped_units = [unit for unit in units if unit.get("task_scope_key") or unit.get("person_memory_key")]

    return {
        "generated_at_unix": int(time.time()),
        "counts": {
            "tasks": _status_counts(tasks),
            "capability_runs": _status_counts(runs),
            "background_jobs": _status_counts(jobs),
            "delegation_tasks": _status_counts(subagents),
        },
        "scope_summary": {
            "task_scopes": _top_nonempty_counts([str(unit.get("task_scope_key") or "") for unit in scoped_units], limit=limit),
            "person_memories": _top_nonempty_counts([str(unit.get("person_memory_key") or "") for unit in scoped_units], limit=limit),
            "conversation_roles": _top_nonempty_counts([str(unit.get("conversation_role") or "") for unit in scoped_units], limit=limit),
            "origin_platforms": _top_nonempty_counts([str(unit.get("origin_platform") or "") for unit in scoped_units], limit=limit),
            "secretary_actions": _top_nonempty_counts([str(unit.get("last_secretary_action") or "") for unit in task_units], limit=limit),
            "executor_overrides": _top_nonempty_counts([str(unit.get("requested_executor_override") or "") for unit in task_units], limit=limit),
            "follow_up_targets": _top_nonempty_counts([str(unit.get("last_follow_up_target_ref") or "") for unit in task_units], limit=limit),
            "operator_queue_next": _top_nonempty_counts([str(unit.get("operator_queue_next") or "") for unit in task_units], limit=limit),
            "operator_resolutions": _top_nonempty_counts([str(unit.get("last_operator_resolution") or "") for unit in task_units], limit=limit),
        },
        "derived_signals": _derive_system_signals(runs=runs, jobs=jobs),
        "reconcile_summary": reconcile_summary,
        "tasks": tasks,
        "capability_runs": runs,
        "background_jobs": jobs,
        "delegation_tasks": subagents,
        "units": units,
        "top_units": units[: max(1, limit)],
    }
