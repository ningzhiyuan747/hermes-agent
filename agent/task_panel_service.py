from __future__ import annotations

from typing import Any, Dict, Optional

from agent.background_jobs import get_job
from agent.business_db import (
    get_task,
    list_approvals,
    list_capability_artifacts,
    list_capability_runs,
)


ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}


def get_task_panel_snapshot(task_id: str, *, active_only: bool = False) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None

    runs = list_capability_runs(task_id=task_id, limit=20)
    active_runs = [row for row in runs if str(row.get("status") or "").strip().lower() in ACTIVE_RUN_STATUSES]
    current_run = active_runs[0] if active_runs else (runs[0] if runs and not active_only else None)
    run_ids = [str(row.get("run_id") or "").strip() for row in runs if str(row.get("run_id") or "").strip()]

    approval_rows = list_approvals(status="pending" if active_only else "", limit=100)
    approvals: list[Dict[str, Any]] = []
    for item in approval_rows:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        payload_task_id = str(payload.get("task_id") or "").strip()
        if payload_task_id == task_id or str(item.get("target_id") or "").strip() in run_ids:
            approvals.append(item)
    approvals.sort(key=lambda item: int(item.get("created_at_unix") or 0), reverse=True)

    current_job = None
    candidate_job_ids: list[str] = []
    if current_run and str(current_run.get("background_job_id") or "").strip():
        candidate_job_ids.append(str(current_run.get("background_job_id") or "").strip())
    for row in runs:
        job_id = str(row.get("background_job_id") or "").strip()
        if job_id and job_id not in candidate_job_ids:
            candidate_job_ids.append(job_id)
    for job_id in candidate_job_ids:
        current_job = get_job(job_id)
        if current_job:
            break

    artifact_items: list[Dict[str, Any]] = []
    seen_artifact_ids: set[str] = set()
    for row in runs[:5]:
        run_id = str(row.get("run_id") or "").strip()
        if not run_id:
            continue
        for artifact in list_capability_artifacts(run_id, limit=5):
            artifact_id = str(artifact.get("artifact_id") or "").strip()
            if artifact_id and artifact_id in seen_artifact_ids:
                continue
            if artifact_id:
                seen_artifact_ids.add(artifact_id)
            artifact_items.append(artifact)
            if len(artifact_items) >= 8:
                break
        if len(artifact_items) >= 8:
            break

    return {
        "task": task,
        "current_run": current_run,
        "current_job": current_job,
        "approvals": approvals[:8],
        "artifact_items": artifact_items[:8],
        "runs": runs[:8],
    }
