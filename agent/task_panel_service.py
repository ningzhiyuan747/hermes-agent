from __future__ import annotations

from typing import Any, Dict, Optional

from agent.background_jobs import get_job, list_jobs
from agent.executor_dispatch_policy import decide_dispatch_action
from agent.business_db import (
    get_task,
    list_approvals,
    list_capability_artifacts,
    list_capability_runs,
    update_task,
)
from agent.capability_snapshots import (
    extract_background_job_person_memory_key,
    extract_background_job_task_scope_key,
    extract_capability_run_person_memory_key,
    extract_capability_run_task_scope_key,
)
from scripts.subagent_task_status import _load_task_meta


ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused", "blocked"}
ACTIVE_DELEGATION_STATUSES = {"created", "running", "pending", "queued", "blocked", "paused"}


def _delegation_status(value: str) -> str:
    return {
        "created": "queued",
        "pending": "queued",
        "queued": "queued",
        "running": "running",
        "blocked": "blocked",
        "paused": "paused",
        "completed": "completed",
        "failed": "failed",
        "cancelled": "cancelled",
    }.get(str(value or "").strip().lower(), "")


def _match_task_delegations(task: Dict[str, Any]) -> list[Dict[str, Any]]:
    task_id = str(task.get("task_id") or "").strip()
    source_session_id = str(task.get("source_session_id") or "").strip()
    matched: list[Dict[str, Any]] = []
    for row in _load_task_meta():
        if not isinstance(row, dict):
            continue
        control_task_id = str(row.get("control_task_id") or "").strip()
        parent_session_id = str(row.get("parent_session_id") or "").strip()
        task_scope_key = str(row.get("task_scope_key") or "").strip()
        if control_task_id == task_id or (source_session_id and parent_session_id == source_session_id) or task_scope_key == f"task:{task_id}":
            matched.append(row)
    matched.sort(
        key=lambda item: (
            int(str(item.get("status") or "").strip().lower() in ACTIVE_DELEGATION_STATUSES),
            int(item.get("updated_at_unix") or item.get("finished_at_unix") or item.get("started_at_unix") or item.get("created_at_unix") or 0),
        ),
        reverse=True,
    )
    return matched


def _classify_failure(*, status: str, blocker: str = "", delivery_error: str = "") -> str:
    normalized_status = str(status or "").strip().lower()
    normalized_blocker = str(blocker or "").strip().lower()
    normalized_delivery_error = str(delivery_error or "").strip().lower()
    combined = " ".join(part for part in [normalized_delivery_error, normalized_blocker] if part).strip()
    if normalized_status not in {"failed", "cancelled"} and not combined:
        return ""
    if combined:
        if "app_id or app_secret not found" in combined or "api key" in combined or "token" in combined or "credential" in combined or "auth" in combined:
            return "credential_failed"
        if "no delivery target resolved" in combined or "could not resolve 'origin-chat'" in combined or "routing" in combined or "route" in combined:
            return "routing_failed"
        if normalized_delivery_error:
            return "delivery_failed"
        if "capture_output" in combined or "popen" in combined or "timeout" in combined or "connection" in combined or "network" in combined:
            return "infra_failed"
    if normalized_status == "failed":
        return "execution_failed"
    return ""


def _derive_execution_status(
    *,
    task_status: str,
    run_status: str,
    job_status: str,
    delegation_status: str,
) -> str:
    for candidate in [delegation_status, job_status, run_status, task_status]:
        normalized = str(candidate or "").strip().lower()
        if normalized:
            return normalized
    return "open"


def _derive_delivery_status(*, current_job: Dict[str, Any], execution_status: str) -> str:
    raw_status = str(current_job.get("delivery_status") or "").strip().lower()
    if raw_status:
        return raw_status
    if str(current_job.get("delivery_error") or "").strip():
        return "failed"
    if not current_job:
        return ""
    if str(execution_status or "").strip().lower() in ACTIVE_JOB_STATUSES | {"queued"}:
        return "pending"
    return "not_attempted"


def _recovery_hint(
    *,
    failure_kind: str,
    execution_status: str,
    delivery_status: str,
    pending_approval_count: int,
) -> str:
    if int(pending_approval_count or 0) > 0:
        return "Resolve the pending approval before dispatch continues."
    normalized_failure = str(failure_kind or "").strip().lower()
    if normalized_failure == "credential_failed":
        return "Repair platform credentials and redeliver the existing result."
    if normalized_failure == "routing_failed":
        return "Repair the delivery target mapping and redeliver the existing result."
    if normalized_failure == "delivery_failed":
        return "Retry delivery without rerunning the executor."
    if normalized_failure == "infra_failed":
        return "Inspect worker/runtime infrastructure, then retry execution."
    if normalized_failure == "execution_failed":
        return "Retry execution or switch to another executor."
    if str(delivery_status or "").strip().lower() == "failed":
        return "Retry delivery after fixing the platform channel."
    if str(execution_status or "").strip().lower() in ACTIVE_RUN_STATUSES | ACTIVE_JOB_STATUSES | {"queued"}:
        return "Wait for the current executor to finish and capture the result."
    return ""


def _task_control_summary(
    *,
    task: Dict[str, Any],
    current_run: Dict[str, Any] | None,
    current_job: Dict[str, Any] | None,
    current_delegation: Dict[str, Any] | None,
    approvals: list[Dict[str, Any]],
) -> Dict[str, Any]:
    current_run = current_run if isinstance(current_run, dict) else {}
    current_job = current_job if isinstance(current_job, dict) else {}
    current_delegation = current_delegation if isinstance(current_delegation, dict) else {}
    task_status = str(task.get("status") or "").strip().lower()
    run_status = str(current_run.get("status") or "").strip().lower()
    job_status = str(current_job.get("status") or "").strip().lower()
    delegation_status = _delegation_status(str(current_delegation.get("status") or "").strip())

    status = task_status or "open"
    if approvals:
        status = "pending_approval"
    elif delegation_status in {"queued", "running", "blocked", "paused"}:
        status = delegation_status
    elif run_status in ACTIVE_RUN_STATUSES:
        status = run_status
    elif job_status in ACTIVE_JOB_STATUSES:
        status = job_status
    elif delegation_status in {"completed", "failed", "cancelled"}:
        status = delegation_status
    elif job_status in {"completed", "failed", "cancelled"}:
        status = job_status
    elif run_status in {"completed", "failed", "cancelled"}:
        status = run_status
    elif task_status:
        status = task_status

    current_executor = ""
    if str(current_delegation.get("worker_role") or current_delegation.get("role_title") or "").strip():
        current_executor = str(current_delegation.get("worker_role") or current_delegation.get("role_title") or "").strip()
    elif str(current_job.get("executor") or "").strip():
        current_executor = str(current_job.get("executor") or "").strip()
    elif str(current_run.get("capability_name") or "").strip():
        current_executor = str(current_run.get("capability_name") or "").strip()

    current_focus = (
        str(current_delegation.get("current_focus") or "").strip()
        or str(current_delegation.get("goal") or "").strip()
        or
        str(current_job.get("current_focus") or "").strip()
        or str(current_run.get("current_focus") or "").strip()
    )
    next_step = (
        str(current_delegation.get("next_step") or "").strip()
        or
        str(current_job.get("next_step") or "").strip()
        or str(current_run.get("next_step") or "").strip()
    )
    blocker = (
        str(current_delegation.get("blocker") or "").strip()
        or
        str(current_job.get("blocker") or "").strip()
        or str(current_run.get("blocker") or "").strip()
    )
    if approvals and not blocker:
        blocker = "Pending approval."
    if approvals and not next_step:
        next_step = "Owner/admin should resolve the pending approval."
    activity_candidates = [
        int(current_delegation.get("updated_at_unix") or current_delegation.get("finished_at_unix") or current_delegation.get("started_at_unix") or current_delegation.get("created_at_unix") or 0),
        int(current_job.get("updated_at_unix") or current_job.get("finished_at_unix") or current_job.get("started_at_unix") or current_job.get("created_at_unix") or 0),
        int(current_run.get("updated_at_unix") or current_run.get("finished_at_unix") or current_run.get("started_at_unix") or current_run.get("created_at_unix") or 0),
        max((int(item.get("created_at_unix") or 0) for item in approvals), default=0),
    ]
    activity_updated_at_unix = max(activity_candidates) if any(activity_candidates) else int(task.get("created_at_unix") or 0)

    task_scope_key = (
        str(current_delegation.get("task_scope_key") or "").strip()
        or
        extract_background_job_task_scope_key(current_job)
        or extract_capability_run_task_scope_key(current_run)
    )
    person_memory_key = (
        str(current_delegation.get("person_memory_key") or "").strip()
        or
        extract_background_job_person_memory_key(current_job)
        or extract_capability_run_person_memory_key(current_run)
    )
    failure_kind = _classify_failure(
        status=status,
        blocker=blocker,
        delivery_error=str(current_job.get("delivery_error") or "").strip(),
    )
    execution_status = _derive_execution_status(
        task_status=task_status,
        run_status=run_status,
        job_status=job_status,
        delegation_status=delegation_status,
    )
    delivery_status = _derive_delivery_status(current_job=current_job, execution_status=execution_status)
    recovery_hint = _recovery_hint(
        failure_kind=failure_kind,
        execution_status=execution_status,
        delivery_status=delivery_status,
        pending_approval_count=len(approvals),
    )
    dispatch_decision = decide_dispatch_action(
        status=status,
        current_executor=current_executor,
        failure_kind=failure_kind,
        delivery_status=delivery_status,
        pending_approval_count=len(approvals),
    )

    return {
        "status": status,
        "execution_status": execution_status,
        "delivery_status": delivery_status,
        "current_executor": current_executor,
        "current_focus": current_focus,
        "next_step": next_step,
        "blocker": blocker,
        "failure_kind": failure_kind,
        "recovery_hint": recovery_hint,
        "dispatch_action": str(dispatch_decision.get("dispatch_action") or "").strip(),
        "suggested_executor": str(dispatch_decision.get("suggested_executor") or "").strip(),
        "pending_approval_count": len(approvals),
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "activity_updated_at_unix": activity_updated_at_unix,
    }


def get_task_panel_snapshot(task_id: str, *, active_only: bool = False) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    operator_queue = metadata.get("operator_queue") if isinstance(metadata.get("operator_queue"), dict) else {}
    pending_operator_items = list(operator_queue.get("pending") or []) if isinstance(operator_queue.get("pending"), list) else []

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
    if not current_job:
        task_jobs = [
            row
            for row in list_jobs(limit=50, active_only=False)
            if str(row.get("task_id") or "").strip() == task_id
        ]
        active_task_jobs = [
            row for row in task_jobs if str(row.get("status") or "").strip().lower() in ACTIVE_JOB_STATUSES
        ]
        ranked_task_jobs = active_task_jobs or task_jobs
        if ranked_task_jobs:
            current_job = ranked_task_jobs[0]

    delegations = _match_task_delegations(task)
    active_delegations = [
        row for row in delegations if str(row.get("status") or "").strip().lower() in ACTIVE_DELEGATION_STATUSES
    ]
    current_delegation = active_delegations[0] if active_delegations else (delegations[0] if delegations and not active_only else None)

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

    control_summary = _task_control_summary(
        task=task,
        current_run=current_run,
        current_job=current_job,
        current_delegation=current_delegation,
        approvals=approvals[:8],
    )
    secretary_action = metadata.get("secretary_action") if isinstance(metadata.get("secretary_action"), dict) else {}
    secretary_follow_up = metadata.get("secretary_follow_up") if isinstance(metadata.get("secretary_follow_up"), dict) else {}
    last_secretary_action = secretary_action.get("last_action") if isinstance(secretary_action.get("last_action"), dict) else {}
    last_follow_up = secretary_follow_up.get("last_item") if isinstance(secretary_follow_up.get("last_item"), dict) else {}
    if last_secretary_action:
        control_summary["last_secretary_action"] = str(last_secretary_action.get("action") or "").strip()
        control_summary["last_secretary_action_ok"] = bool(last_secretary_action.get("ok"))
        control_summary["last_secretary_action_at_unix"] = int(last_secretary_action.get("recorded_at_unix") or 0)
        control_summary["last_secretary_action_summary"] = str(last_secretary_action.get("summary") or last_secretary_action.get("message") or "").strip()
        control_summary["requested_executor_override"] = str(last_secretary_action.get("requested_executor") or "").strip()
    if last_follow_up:
        control_summary["last_follow_up_action_id"] = str(last_follow_up.get("action_id") or "").strip()
        control_summary["last_follow_up_ok"] = bool(last_follow_up.get("ok"))
        control_summary["last_follow_up_at_unix"] = int(last_follow_up.get("recorded_at_unix") or 0)
        control_summary["last_follow_up_summary"] = str(last_follow_up.get("summary") or "").strip()
        control_summary["last_follow_up_target_ref"] = str(last_follow_up.get("target_ref") or "").strip()
    first_item = pending_operator_items[0] if pending_operator_items and isinstance(pending_operator_items[0], dict) else {}
    control_summary["operator_queue_count"] = len(pending_operator_items)
    control_summary["operator_queue_next"] = str(first_item.get("summary") or "").strip()
    control_summary["last_operator_resolution"] = str((metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}).get("last_operator_resolution") or "").strip()
    control_summary["last_operator_resolution_summary"] = str((metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}).get("last_operator_resolution_summary") or "").strip()

    return {
        "task": task,
        "current_run": current_run,
        "current_job": current_job,
        "current_delegation": current_delegation,
        "approvals": approvals[:8],
        "artifact_items": artifact_items[:8],
        "runs": runs[:8],
        "delegations": delegations[:8],
        "control_summary": control_summary,
    }


def sync_task_control_state(task_id: str) -> Optional[Dict[str, Any]]:
    snapshot = get_task_panel_snapshot(task_id, active_only=False)
    if not snapshot:
        return None
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    control_summary = snapshot.get("control_summary") if isinstance(snapshot.get("control_summary"), dict) else {}
    metadata = dict(task.get("metadata") or {})
    previous_control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    metadata["control_plane"] = {
        "status": str(control_summary.get("status") or "").strip(),
        "execution_status": str(control_summary.get("execution_status") or "").strip(),
        "delivery_status": str(control_summary.get("delivery_status") or "").strip(),
        "current_executor": str(control_summary.get("current_executor") or "").strip(),
        "current_focus": str(control_summary.get("current_focus") or "").strip(),
        "next_step": str(control_summary.get("next_step") or "").strip(),
        "blocker": str(control_summary.get("blocker") or "").strip(),
        "failure_kind": str(control_summary.get("failure_kind") or "").strip(),
        "recovery_hint": str(control_summary.get("recovery_hint") or "").strip(),
        "dispatch_action": str(control_summary.get("dispatch_action") or "").strip(),
        "suggested_executor": str(control_summary.get("suggested_executor") or "").strip(),
        "pending_approval_count": int(control_summary.get("pending_approval_count") or 0),
        "task_scope_key": str(control_summary.get("task_scope_key") or "").strip(),
        "person_memory_key": str(control_summary.get("person_memory_key") or "").strip(),
        "activity_updated_at_unix": int(control_summary.get("activity_updated_at_unix") or previous_control_plane.get("activity_updated_at_unix") or 0),
        "last_secretary_action": str(control_summary.get("last_secretary_action") or previous_control_plane.get("last_secretary_action") or "").strip(),
        "last_secretary_action_ok": bool(control_summary.get("last_secretary_action_ok") if "last_secretary_action_ok" in control_summary else previous_control_plane.get("last_secretary_action_ok")),
        "last_secretary_action_at_unix": int(control_summary.get("last_secretary_action_at_unix") or previous_control_plane.get("last_secretary_action_at_unix") or 0),
        "last_secretary_action_summary": str(control_summary.get("last_secretary_action_summary") or previous_control_plane.get("last_secretary_action_summary") or "").strip(),
        "requested_executor_override": str(control_summary.get("requested_executor_override") or previous_control_plane.get("requested_executor_override") or "").strip(),
        "last_follow_up_action_id": str(control_summary.get("last_follow_up_action_id") or previous_control_plane.get("last_follow_up_action_id") or "").strip(),
        "last_follow_up_ok": bool(control_summary.get("last_follow_up_ok") if "last_follow_up_ok" in control_summary else previous_control_plane.get("last_follow_up_ok")),
        "last_follow_up_at_unix": int(control_summary.get("last_follow_up_at_unix") or previous_control_plane.get("last_follow_up_at_unix") or 0),
        "last_follow_up_summary": str(control_summary.get("last_follow_up_summary") or previous_control_plane.get("last_follow_up_summary") or "").strip(),
        "last_follow_up_target_ref": str(control_summary.get("last_follow_up_target_ref") or previous_control_plane.get("last_follow_up_target_ref") or "").strip(),
        "operator_queue_count": int(control_summary.get("operator_queue_count") or previous_control_plane.get("operator_queue_count") or 0),
        "operator_queue_next": str(control_summary.get("operator_queue_next") or previous_control_plane.get("operator_queue_next") or "").strip(),
        "last_operator_resolution": str(control_summary.get("last_operator_resolution") or previous_control_plane.get("last_operator_resolution") or "").strip(),
        "last_operator_resolution_summary": str(control_summary.get("last_operator_resolution_summary") or previous_control_plane.get("last_operator_resolution_summary") or "").strip(),
    }
    updated = update_task(
        task_id,
        status=str(control_summary.get("status") or task.get("status") or "open").strip().lower() or "open",
        metadata=metadata,
    )
    if not updated:
        return snapshot
    snapshot["task"] = updated
    return snapshot
