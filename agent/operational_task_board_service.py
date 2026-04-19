from __future__ import annotations

import time
from collections import Counter
from typing import Any

from agent.background_jobs import list_jobs
from agent.business_db import list_capability_runs
from scripts.subagent_task_status import _load_task_meta

ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused", "blocked"}
ACTIVE_SUBAGENT_STATUSES = {"created", "running"}


def _status_counts(rows: list[dict[str, Any]], key: str = "status") -> dict[str, int]:
    counter = Counter(str(row.get(key) or "unknown").strip() or "unknown" for row in rows)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


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
            "task_id": task_id,
            "background_job_id": str(row.get("background_job_id") or "").strip(),
            "approval_id": str(row.get("approval_id") or "").strip(),
        },
        **scopes,
        "raw": row,
    }


def _job_capability_run_id(row: dict[str, Any]) -> str:
    for tag in row.get("tags") or []:
        token = str(tag or "").strip().lower()
        if token.startswith("capability_run:"):
            return token.split(":", 1)[1].strip()
    return ""


def _job_unit(row: dict[str, Any]) -> dict[str, Any]:
    job_id = str(row.get("job_id") or "").strip()
    return {
        "unit_id": f"job:{job_id}",
        "unit_type": "background_job",
        "title": str(row.get("title") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "owner": str(row.get("session_id") or row.get("user_id") or "").strip(),
        "origin_platform": "",
        "current_focus": str(row.get("current_focus") or "").strip(),
        "next_step": str(row.get("next_step") or "").strip(),
        "blocker": str(row.get("blocker") or "").strip(),
        "updated_at_unix": int(row.get("updated_at_unix") or 0),
        "priority_hint": 80 if str(row.get("status") or "").strip().lower() in ACTIVE_JOB_STATUSES else 20,
        "related_ids": {
            "job_id": job_id,
            "capability_run_id": _job_capability_run_id(row),
            "executor": str(row.get("executor") or "").strip(),
        },
        "raw": row,
    }


def _subagent_unit(row: dict[str, Any]) -> dict[str, Any]:
    task_id = str(row.get("task_id") or row.get("id") or row.get("session_id") or "unknown").strip()
    return {
        "unit_id": f"subagent:{task_id}",
        "unit_type": "delegation_task",
        "title": str(row.get("goal") or row.get("title") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "owner": str(row.get("worker_role") or "generic").strip(),
        "origin_platform": "",
        "current_focus": str(row.get("current_focus") or "").strip(),
        "next_step": str(row.get("next_step") or "").strip(),
        "blocker": str(row.get("blocker") or "").strip(),
        "updated_at_unix": int(row.get("started_at_unix") or row.get("created_at_unix") or 0),
        "priority_hint": 90 if str(row.get("status") or "").strip().lower() in ACTIVE_SUBAGENT_STATUSES else 10,
        "related_ids": {
            "delegation_task_id": task_id,
        },
        "raw": row,
    }


def build_operational_task_snapshot(*, limit: int = 20) -> dict[str, Any]:
    runs = list_capability_runs(limit=max(20, limit * 4))
    jobs = list_jobs(limit=max(20, limit * 4), active_only=False)
    subagents = _load_task_meta()

    units = [_run_unit(row) for row in runs] + [_job_unit(row) for row in jobs] + [_subagent_unit(row) for row in subagents]
    units.sort(key=lambda item: (-int(item.get("updated_at_unix") or 0), -int(item.get("priority_hint") or 0), item.get("unit_id") or ""))

    return {
        "generated_at_unix": int(time.time()),
        "counts": {
            "capability_runs": _status_counts(runs),
            "background_jobs": _status_counts(jobs),
            "delegation_tasks": _status_counts(subagents),
        },
        "capability_runs": runs,
        "background_jobs": jobs,
        "delegation_tasks": subagents,
        "units": units,
        "top_units": units[: max(1, limit)],
    }
