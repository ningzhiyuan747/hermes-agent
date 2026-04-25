from __future__ import annotations

import json
import os
from pathlib import Path
import time
from typing import Any, Dict

from agent.background_jobs import list_jobs, update_job
from agent.business_db import (
    _ensure_task_for_origin,
    create_task,
    get_capability_run,
    get_task,
    list_capability_runs,
    list_tasks,
    update_task,
    update_capability_run,
)
from scripts.subagent_task_status import _load_task_meta


_ACTIVE_RUN_STATUSES = {"queued", "running"}
_TERMINAL_STATUSES = {"completed", "failed", "cancelled"}


def _job_tag_value(tags: list[str], prefix: str) -> str:
    expected = f"{str(prefix or '').strip().lower()}:"
    if not expected or expected == ":":
        return ""
    for tag in tags:
        normalized = str(tag or "").strip()
        if normalized.lower().startswith(expected):
            return normalized.split(":", 1)[1].strip()
    return ""


def _scope_origin_from_key(task_scope_key: str) -> dict[str, str]:
    parts = str(task_scope_key or "").strip().split(":")
    if len(parts) < 3 or parts[1] != "chat":
        return {}
    origin = {
        "platform": str(parts[0] or "").strip().lower(),
        "chat_id": str(parts[2] or "").strip(),
        "thread_id": "",
    }
    if len(parts) >= 5 and parts[3] == "thread":
        origin["thread_id"] = str(parts[4] or "").strip()
    return origin if origin["platform"] and origin["chat_id"] else {}


def _origin_from_session_id(session_id: str) -> dict[str, str]:
    parts = str(session_id or "").strip().split(":")
    if len(parts) < 5 or parts[0] != "agent" or parts[1] != "main":
        return {}
    platform = str(parts[2] or "").strip().lower()
    chat_id = str(parts[4] or "").strip()
    if not platform or not chat_id:
        return {}
    origin = {
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": "",
    }
    if len(parts) >= 7 and str(parts[3] or "").strip().lower() == "thread":
        origin["thread_id"] = str(parts[5] or "").strip()
    return origin


def _parse_person_memory_key(person_memory_key: str) -> tuple[str, str]:
    parts = str(person_memory_key or "").strip().split(":")
    if len(parts) >= 3 and parts[1] == "user":
        return str(parts[0] or "").strip().lower(), str(parts[2] or "").strip()
    if len(parts) >= 2 and parts[0] == "user":
        return "", str(parts[1] or "").strip()
    return "", ""


def _upsert_meta_file(path: Path, row: dict[str, Any]) -> None:
    path.write_text(json.dumps(row, ensure_ascii=False, indent=2), encoding="utf-8")


def _pid_exists(pid: int) -> bool:
    normalized = int(pid or 0)
    if normalized <= 0:
        return False
    try:
        os.kill(normalized, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _stale_running_job_threshold_seconds() -> int:
    return max(300, int(os.getenv("HERMES_RECONCILE_STALE_RUNNING_JOB_SECONDS", "7200") or "7200"))


def _terminal_run_status_from_job(job_status: str) -> str:
    normalized = str(job_status or "").strip().lower()
    if normalized in _TERMINAL_STATUSES:
        return normalized
    return "failed"


def _reconcile_stale_runs_and_jobs(*, limit: int) -> tuple[int, int]:
    now = int(time.time())
    jobs_by_id = {
        str(row.get("job_id") or "").strip(): row
        for row in list_jobs(limit=max(20, limit * 4), active_only=False)
        if str(row.get("job_id") or "").strip()
    }

    terminalized = 0
    stale_failed = 0

    for run in list_capability_runs(limit=max(20, limit * 4)):
        run_id = str(run.get("run_id") or "").strip()
        run_status = str(run.get("status") or "").strip().lower()
        if run_status not in _ACTIVE_RUN_STATUSES:
            continue

        job_id = str(run.get("background_job_id") or "").strip()
        if not job_id:
            continue
        job = jobs_by_id.get(job_id)
        if not isinstance(job, dict):
            continue

        job_status = str(job.get("status") or "").strip().lower()
        if job_status in _TERMINAL_STATUSES:
            update_capability_run(
                run_id,
                status=_terminal_run_status_from_job(job_status),
                current_focus=str(job.get("current_focus") or "").strip() or f"Background job {job_id} already finished.",
                next_step=str(job.get("next_step") or "").strip() or "Review the background job result and continue from the task panel.",
                blocker=(
                    str(job.get("blocker") or "").strip()
                    or str(job.get("delivery_error") or "").strip()
                    if job_status == "failed"
                    else ""
                ),
                result=str(job.get("result") or "").strip() if job_status == "completed" else str(run.get("result") or "").strip(),
            )
            terminalized += 1
            continue

        if job_status != "running":
            continue

        age_seconds = max(
            0,
            now - int(job.get("updated_at_unix") or job.get("started_at_unix") or job.get("created_at_unix") or 0),
        )
        runner_pid = int(job.get("runner_pid") or 0)
        if age_seconds < _stale_running_job_threshold_seconds():
            continue
        if runner_pid > 0 and _pid_exists(runner_pid):
            continue

        stale_message = (
            f"Background job {job_id} was left in running state, "
            f"but runner pid {runner_pid or '-'} is no longer alive."
        )
        updated_job = update_job(
            job_id,
            status="failed",
            current_focus="Stale running background job was terminalized during reconciliation.",
            next_step="Retry the capability run or create a fresh background job if the work is still needed.",
            blocker=stale_message,
            runner_pid=None,
            runner_runtime="",
        ) or {}
        update_capability_run(
            run_id,
            status="failed",
            current_focus=str(updated_job.get("current_focus") or "").strip() or "Stale background job was terminalized.",
            next_step=str(updated_job.get("next_step") or "").strip() or "Retry the capability run if the work is still needed.",
            blocker=stale_message,
        )
        stale_failed += 1

    return terminalized, stale_failed


def _delegation_task_status(value: str) -> str:
    normalized = str(value or "").strip().lower()
    if not normalized:
        return "open"
    return {
        "created": "queued",
        "pending": "queued",
        "queued": "queued",
        "running": "running",
        "pending_approval": "pending_approval",
        "blocked": "blocked",
        "paused": "paused",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(normalized, "open")


def _effective_delegation_status(row: dict[str, Any]) -> str:
    explicit = _delegation_task_status(str(row.get("status") or "").strip())
    if explicit != "open":
        return explicit
    if int(row.get("finished_at_unix") or 0) > 0:
        return "completed"
    report_path = Path(str(row.get("final_report_path") or "").strip())
    if report_path.exists():
        return "completed"
    return explicit


def _sync_delegation_task_state(task_id: str, row: dict[str, Any]) -> None:
    task = get_task(task_id)
    if not isinstance(task, dict):
        return
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    if str(metadata.get("materialized_by") or "").strip() != "task_reconcile_service.delegation":
        return

    worker_role = str(row.get("worker_role") or row.get("role") or "").strip()
    status = _effective_delegation_status(row)
    current_focus = str(row.get("current_focus") or "").strip()
    next_step = str(row.get("next_step") or "").strip()
    blocker = str(row.get("blocker") or "").strip()
    if not current_focus:
        current_focus = {
            "queued": "Delegation task recorded; waiting for execution.",
            "running": "Delegation worker is running.",
            "pending_approval": "Delegation result is waiting for approval.",
            "completed": "Delegation task completed.",
            "failed": "Delegation task failed.",
            "cancelled": "Delegation task was cancelled.",
        }.get(status, "")
    if not next_step:
        next_step = {
            "queued": "Start the delegated execution.",
            "running": "Wait for the worker to finish and collect the result.",
            "pending_approval": "Review the delegated result and decide approval.",
            "completed": "Review the delegated report or artifact if needed.",
            "failed": "Inspect the delegated failure and decide whether to retry.",
            "cancelled": "Create a new task if the delegated work still matters.",
        }.get(status, "")

    updated_metadata = dict(metadata)
    updated_metadata["control_plane"] = {
        **(metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}),
        "status": status,
        "current_executor": worker_role,
        "current_focus": current_focus,
        "next_step": next_step,
        "blocker": blocker,
        "task_scope_key": str(row.get("task_scope_key") or metadata.get("task_scope_key") or f"task:{task_id}").strip(),
        "person_memory_key": str(row.get("person_memory_key") or metadata.get("person_memory_key") or "").strip(),
    }
    update_task(task_id, status=status, metadata=updated_metadata)


def _reconcile_task_terminal_states(*, limit: int) -> tuple[int, int]:
    from agent.task_panel_service import sync_task_control_state

    def _terminal_task_needs_refresh(task: dict[str, Any]) -> bool:
        metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
        control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
        status = str(task.get("status") or "").strip().lower()
        control_status = str(control_plane.get("status") or "").strip().lower()
        if control_status != status:
            return True
        if status == "failed" and not str(control_plane.get("failure_kind") or "").strip():
            return True
        return not any(
            [
                str(control_plane.get("current_executor") or "").strip(),
                str(control_plane.get("current_focus") or "").strip(),
                str(control_plane.get("next_step") or "").strip(),
                str(control_plane.get("blocker") or "").strip(),
            ]
        )

    scanned = 0
    terminalized = 0
    now = int(time.time())
    for task in list_tasks(limit=max(20, limit * 4)):
        task_id = str(task.get("task_id") or "").strip()
        if not task_id:
            continue
        before_status = str(task.get("status") or "").strip().lower()
        if before_status in {"completed", "failed", "cancelled"} and not _terminal_task_needs_refresh(task):
            continue
        scanned += 1
        snapshot = sync_task_control_state(task_id)
        updated_task = snapshot.get("task") if isinstance(snapshot, dict) and isinstance(snapshot.get("task"), dict) else {}
        after_status = str(updated_task.get("status") or before_status).strip().lower()
        if after_status == before_status == "open":
            control_summary = snapshot.get("control_summary") if isinstance(snapshot, dict) and isinstance(snapshot.get("control_summary"), dict) else {}
            no_activity = not any(
                [
                    snapshot.get("current_run"),
                    snapshot.get("current_job"),
                    snapshot.get("current_delegation"),
                    snapshot.get("approvals"),
                    str(control_summary.get("current_focus") or "").strip(),
                    str(control_summary.get("next_step") or "").strip(),
                    str(control_summary.get("blocker") or "").strip(),
                    str(control_summary.get("current_executor") or "").strip(),
                ]
            )
            age_seconds = max(0, now - int(updated_task.get("created_at_unix") or task.get("created_at_unix") or 0))
            title = str(updated_task.get("title") or "").strip().lower()
            if no_activity and age_seconds >= 6 * 3600 and "smoke" in title:
                metadata = updated_task.get("metadata") if isinstance(updated_task.get("metadata"), dict) else {}
                updated_metadata = dict(metadata)
                updated_metadata["control_plane"] = {
                    **(metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}),
                    "status": "cancelled",
                    "current_executor": "",
                    "current_focus": "Stale smoke task shell cancelled; no execution trace was found.",
                    "next_step": "Create a fresh smoke task if you still need the check.",
                    "blocker": "No execution trace found for an old smoke task shell.",
                    "task_scope_key": str(((metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}).get("task_scope_key")) or "").strip(),
                    "person_memory_key": str(((metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}).get("person_memory_key")) or "").strip(),
                }
                update_task(task_id, status="cancelled", metadata=updated_metadata)
                after_status = "cancelled"
        if after_status in {"completed", "failed", "cancelled"} and after_status != before_status:
            terminalized += 1
    return scanned, terminalized


def _resolve_delegation_task_id(
    row: dict[str, Any],
    *,
    parent_task_ids: dict[str, str],
) -> str:
    explicit_control_task_id = str(row.get("control_task_id") or "").strip()
    if explicit_control_task_id and get_task(explicit_control_task_id):
        return explicit_control_task_id

    explicit_task_scope_key = str(row.get("task_scope_key") or "").strip()
    if explicit_task_scope_key.startswith("task:"):
        explicit_task_id = explicit_task_scope_key.split(":", 1)[1].strip()
        if explicit_task_id and get_task(explicit_task_id):
            return explicit_task_id

    parent_session_id = str(row.get("parent_session_id") or "").strip()
    inherited_task_id = parent_task_ids.get(parent_session_id, "")
    if inherited_task_id and get_task(inherited_task_id):
        return inherited_task_id

    origin = _scope_origin_from_key(explicit_task_scope_key)
    if not origin:
        origin = _origin_from_session_id(parent_session_id)
    actor_platform, actor_user_id = _parse_person_memory_key(str(row.get("person_memory_key") or "").strip())
    if origin:
        if not actor_user_id and origin.get("platform") == str(row.get("platform") or "").strip().lower():
            actor_user_id = str(row.get("user_id") or "").strip()
        resolved_task_id = _ensure_task_for_origin(
            title=str(row.get("title") or row.get("goal") or "").strip(),
            goal=str(row.get("goal") or row.get("title") or "").strip(),
            actor_user_id=actor_user_id,
            session_id=parent_session_id,
            origin=origin,
            metadata={
                "materialized_by": "task_reconcile_service.delegation",
                "delegation_task": True,
                "worker_role": str(row.get("worker_role") or row.get("role") or "").strip(),
                "reconciled": True,
            },
        )
        if resolved_task_id:
            return resolved_task_id

    source_platform = str(row.get("platform") or actor_platform or "").strip().lower()
    created = create_task(
        title=str(row.get("title") or row.get("goal") or "").strip(),
        goal=str(row.get("goal") or row.get("title") or "").strip(),
        owner_user_id=actor_user_id,
        source_platform=source_platform,
        source_chat_id="",
        source_thread_id="",
        source_session_id=parent_session_id,
        metadata={
            "auto_materialized": True,
            "materialized_by": "task_reconcile_service.delegation",
            "delegation_task": True,
            "worker_role": str(row.get("worker_role") or row.get("role") or "").strip(),
            "reconciled": True,
        },
    )
    return str((created or {}).get("task_id") or "").strip()


def _reconcile_delegation_records(*, limit: int) -> tuple[int, int]:
    rows = _load_task_meta()
    if not rows:
        return 0, 0

    parent_task_ids: dict[str, str] = {}
    for task in list_tasks(limit=max(20, limit * 4)):
        session_id = str(task.get("source_session_id") or "").strip()
        task_id = str(task.get("task_id") or "").strip()
        if session_id and task_id and session_id not in parent_task_ids:
            parent_task_ids[session_id] = task_id
    for run in list_capability_runs(limit=max(20, limit * 4)):
        session_id = str(run.get("session_id") or "").strip()
        task_id = str(run.get("task_id") or "").strip()
        if session_id and task_id and session_id not in parent_task_ids:
            parent_task_ids[session_id] = task_id

    scanned = 0
    linked = 0
    for row in rows[: max(1, int(limit or 100))]:
        meta_path = Path(str(row.get("_meta_path") or "").strip())
        if not meta_path.exists():
            continue
        current_control_task_id = str(row.get("control_task_id") or "").strip()
        if current_control_task_id and get_task(current_control_task_id):
            _sync_delegation_task_state(current_control_task_id, row)
            continue
        scanned += 1
        resolved_task_id = _resolve_delegation_task_id(row, parent_task_ids=parent_task_ids)
        if not resolved_task_id:
            continue
        row["control_task_id"] = resolved_task_id
        _sync_delegation_task_state(resolved_task_id, row)
        if not str(row.get("task_scope_key") or "").strip():
            parent_session_id = str(row.get("parent_session_id") or "").strip()
            task_scope_key = ""
            if parent_session_id in parent_task_ids and parent_task_ids[parent_session_id] == resolved_task_id:
                task_scope_key = ""
            else:
                task_scope_key = f"task:{resolved_task_id}"
            if task_scope_key:
                row["task_scope_key"] = task_scope_key
                row.setdefault("conversation_role", "task_unit")
        _upsert_meta_file(meta_path, row)
        linked += 1
    return scanned, linked


def reconcile_task_records(*, limit: int = 100) -> Dict[str, Any]:
    runs_scanned = 0
    runs_linked = 0
    jobs_scanned = 0
    jobs_linked = 0

    runs = list_capability_runs(limit=max(1, int(limit or 100)))
    for row in runs:
        if str(row.get("task_id") or "").strip():
            continue
        runs_scanned += 1
        resolved_task_id = _ensure_task_for_origin(
            title=str(row.get("title") or "").strip(),
            goal=str(row.get("goal") or "").strip(),
            actor_user_id=str(row.get("actor_user_id") or "").strip(),
            session_id=str(row.get("session_id") or "").strip(),
            origin=row.get("origin") if isinstance(row.get("origin"), dict) else {},
            metadata={
                "materialized_by": "task_reconcile_service.run",
                "capability": str(row.get("capability_name") or "").strip(),
                "reconciled": True,
            },
        )
        if not resolved_task_id:
            continue
        update_capability_run(str(row.get("run_id") or "").strip(), task_id=resolved_task_id)
        runs_linked += 1

    jobs = list_jobs(limit=max(1, int(limit or 100)), active_only=False)
    for row in jobs:
        if str(row.get("task_id") or "").strip():
            continue
        jobs_scanned += 1
        tags = [str(item).strip() for item in (row.get("tags") or []) if str(item).strip()]
        resolved_task_id = ""
        linked_run_id = _job_tag_value(tags, "capability_run")
        if linked_run_id:
            linked_run = get_capability_run(linked_run_id)
            resolved_task_id = str((linked_run or {}).get("task_id") or "").strip()
        if not resolved_task_id:
            resolved_task_id = _ensure_task_for_origin(
                title=str(row.get("title") or "").strip(),
                goal=str(row.get("prompt") or "").strip(),
                actor_user_id=str(row.get("user_id") or "").strip(),
                session_id=str(row.get("session_id") or "").strip(),
                origin=row.get("origin") if isinstance(row.get("origin"), dict) else {},
                metadata={
                    "materialized_by": "task_reconcile_service.job",
                    "executor": str(row.get("executor") or "").strip(),
                    "background_job": True,
                    "reconciled": True,
                },
            )
        if not resolved_task_id:
            continue
        update_job(str(row.get("job_id") or "").strip(), task_id=resolved_task_id)
        jobs_linked += 1

    runs_terminalized, jobs_failed = _reconcile_stale_runs_and_jobs(limit=max(1, int(limit or 100)))
    delegations_scanned, delegations_linked = _reconcile_delegation_records(limit=max(1, int(limit or 100)))
    tasks_scanned, tasks_terminalized = _reconcile_task_terminal_states(limit=max(1, int(limit or 100)))

    return {
        "runs_scanned": runs_scanned,
        "runs_linked": runs_linked,
        "runs_terminalized": runs_terminalized,
        "jobs_scanned": jobs_scanned,
        "jobs_linked": jobs_linked,
        "jobs_failed": jobs_failed,
        "delegations_scanned": delegations_scanned,
        "delegations_linked": delegations_linked,
        "tasks_scanned": tasks_scanned,
        "tasks_terminalized": tasks_terminalized,
    }
