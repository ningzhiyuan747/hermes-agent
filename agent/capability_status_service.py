from __future__ import annotations

import os
import signal
from typing import Any, Dict, Optional

from agent.background_jobs import append_job_event, list_jobs, update_job
from agent.business_db import (
    get_channel_task,
    list_capability_artifacts,
    list_capability_runs,
    update_capability_run,
)
from agent.capability_snapshots import (
    pick_background_job_snapshot,
    pick_capability_run_snapshot,
    resolve_task_binding,
)


def get_task_binding(*, platform: str, chat_id: str = "", thread_id: str = "") -> tuple[Dict[str, Any], str]:
    return resolve_task_binding(
        platform=str(platform or "").strip(),
        chat_id=str(chat_id or "").strip(),
        thread_id=str(thread_id or "").strip(),
        get_channel_task_func=get_channel_task,
    )


def get_background_job_snapshot(
    *,
    session_id: str,
    active_only: bool = True,
    global_fallback: bool = False,
    platform: str = "",
    chat_id: str = "",
    thread_id: str = "",
) -> Optional[Dict[str, Any]]:
    rows = list_jobs(limit=30, active_only=bool(active_only))
    runs = list_capability_runs(status="", limit=100)
    task, task_id = get_task_binding(platform=platform, chat_id=chat_id, thread_id=thread_id)
    return pick_background_job_snapshot(
        rows,
        runs,
        session_id=str(session_id or "").strip(),
        task_id=task_id,
        task_title=str(task.get("title") or "").strip(),
        global_fallback=bool(global_fallback),
        list_capability_artifacts_func=list_capability_artifacts,
    )


def get_capability_run_snapshot(
    *,
    session_id: str,
    active_only: bool = True,
    global_fallback: bool = False,
    platform: str = "",
    chat_id: str = "",
    thread_id: str = "",
) -> Optional[Dict[str, Any]]:
    rows = list_capability_runs(status="", limit=100)
    task, task_id = get_task_binding(platform=platform, chat_id=chat_id, thread_id=thread_id)
    return pick_capability_run_snapshot(
        rows,
        session_id=str(session_id or "").strip(),
        task_id=task_id,
        task_title=str(task.get("title") or "").strip(),
        active_only=bool(active_only),
        global_fallback=bool(global_fallback),
        list_capability_artifacts_func=list_capability_artifacts,
    )


def cancel_background_jobs(
    *,
    session_id: str,
    global_fallback: bool = False,
    platform: str = "",
    chat_id: str = "",
    thread_id: str = "",
    cancel_label: str = "",
    cancel_source: str = "",
) -> Dict[str, Any]:
    rows = list_jobs(limit=50, active_only=True)
    runs = list_capability_runs(status="", limit=100)
    task, task_id = get_task_binding(platform=platform, chat_id=chat_id, thread_id=thread_id)
    del task
    runs_by_id = {
        str(run.get("run_id") or "").strip(): run
        for run in runs
        if str(run.get("run_id") or "").strip()
    }
    runs_by_job = {
        str(run.get("background_job_id") or "").strip(): run
        for run in runs
        if str(run.get("background_job_id") or "").strip()
    }

    def _resolve_run(row: Dict[str, Any]) -> Dict[str, Any] | None:
        run = runs_by_job.get(str(row.get("job_id") or "").strip())
        if run is not None:
            return run
        for tag in [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]:
            if tag.startswith("capability_run:") and tag.split(":", 1)[1]:
                run = runs_by_id.get(tag.split(":", 1)[1].strip())
                if run is not None:
                    return run
        return None

    matches: list[Dict[str, Any]] = []
    for row in rows:
        tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
        if "executor:openclaw" not in tags:
            continue
        run = _resolve_run(row)
        run_task_id = str((run or {}).get("task_id") or "").strip()
        session_match = str(row.get("session_id") or "").strip() == str(session_id or "").strip()
        if task_id:
            if run_task_id != task_id and not (not run_task_id and session_match):
                continue
        elif not session_match:
            continue
        matches.append(row)

    if not matches and global_fallback:
        fallback_rows: list[Dict[str, Any]] = []
        for row in rows:
            tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
            if "executor:openclaw" not in tags:
                continue
            if task_id:
                run = _resolve_run(row)
                if str((run or {}).get("task_id") or "").strip() != task_id:
                    continue
            fallback_rows.append(row)
        fallback_rows.sort(key=lambda item: int(item.get("updated_at_unix") or 0), reverse=True)
        matches = fallback_rows[:1]

    if not matches:
        return {"ok": True, "cancelled": False, "count": 0}

    cancelled_job_ids: set[str] = set()
    updated_run_ids: set[str] = set()
    jobs: list[Dict[str, Any]] = []
    label = str(cancel_label or "control command").strip()
    source = str(cancel_source or "control").strip()
    for row in matches:
        job_id = str(row.get("job_id") or "").strip()
        pid = row.get("runner_pid")
        killed = False
        if pid:
            try:
                pid = int(pid)
                try:
                    os.killpg(pid, signal.SIGTERM)
                except Exception:
                    os.kill(pid, signal.SIGTERM)
                killed = True
            except Exception:
                killed = False
        update_job(
            job_id,
            status="cancelled",
            current_focus=f"Background research job cancelled from {label}.",
            next_step="Create a new task if more research is needed.",
            blocker=f"Cancelled by owner from {label}.",
            runner_pid=None,
            runner_runtime="",
        )
        append_job_event(
            job_id,
            kind="cancelled",
            message=f"Cancelled from {label}.",
            status="cancelled",
            current_focus=f"Background research job cancelled from {label}.",
        )
        cancelled_job_ids.add(job_id)
        jobs.append({"job_id": job_id, "killed": killed})
        for run in runs:
            if str(run.get("background_job_id") or "").strip() != job_id:
                continue
            run_id = str(run.get("run_id") or "").strip()
            if not run_id or run_id in updated_run_ids:
                continue
            update_capability_run(
                run_id,
                status="cancelled",
                current_focus=f"Cancelled from {label}.",
                next_step="Restart the capability run if more work is needed.",
                blocker="Background OpenClaw research job cancelled by owner.",
                output={
                    "cancelled_job_ids": sorted(cancelled_job_ids),
                    "cancel_source": source,
                },
            )
            updated_run_ids.add(run_id)
    return {"ok": True, "cancelled": True, "count": len(jobs), "jobs": jobs}
