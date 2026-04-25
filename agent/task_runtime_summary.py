from __future__ import annotations

from typing import Any, Dict

from agent.capability_snapshots import (
    extract_background_job_person_memory_key,
    extract_background_job_task_scope_key,
    extract_capability_run_person_memory_key,
    extract_capability_run_task_scope_key,
)
from agent.executor_dispatch_policy import decide_dispatch_action


ACTIVE_TASK_STATUSES = {"open", "queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused", "blocked"}
ACTIVE_DELEGATION_STATUSES = {"created", "running", "pending", "queued", "blocked", "paused"}


def delegation_status(value: str) -> str:
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


def match_task_delegations(task: Dict[str, Any], delegation_rows: list[Dict[str, Any]] | None) -> list[Dict[str, Any]]:
    task_id = str(task.get("task_id") or "").strip()
    source_session_id = str(task.get("source_session_id") or "").strip()
    matched: list[Dict[str, Any]] = []
    for row in delegation_rows or []:
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


def classify_failure(*, status: str, blocker: str = "", delivery_error: str = "") -> str:
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


def derive_execution_status(
    *,
    task_status: str,
    run_status: str,
    job_status: str,
    delegation_status_value: str,
) -> str:
    for candidate in [delegation_status_value, job_status, run_status, task_status]:
        normalized = str(candidate or "").strip().lower()
        if normalized:
            return normalized
    return "open"


def derive_delivery_status(*, current_job: Dict[str, Any], execution_status: str) -> str:
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


def recovery_hint(
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


def build_task_control_summary(
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
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    existing_control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}

    task_status = str(task.get("status") or existing_control_plane.get("status") or "").strip().lower()
    run_status = str(current_run.get("status") or "").strip().lower()
    job_status = str(current_job.get("status") or "").strip().lower()
    delegation_status_value = delegation_status(str(current_delegation.get("status") or "").strip())

    status = task_status or "open"
    if approvals:
        status = "pending_approval"
    elif delegation_status_value in {"queued", "running", "blocked", "paused"}:
        status = delegation_status_value
    elif run_status in ACTIVE_RUN_STATUSES:
        status = run_status
    elif job_status in ACTIVE_JOB_STATUSES:
        status = job_status
    elif delegation_status_value in {"completed", "failed", "cancelled"}:
        status = delegation_status_value
    elif job_status in {"completed", "failed", "cancelled"}:
        status = job_status
    elif run_status in {"completed", "failed", "cancelled"}:
        status = run_status
    elif task_status:
        status = task_status

    current_executor = (
        str(current_delegation.get("worker_role") or current_delegation.get("role_title") or "").strip()
        or str(current_job.get("executor") or "").strip()
        or str(current_run.get("capability_name") or "").strip()
        or str(existing_control_plane.get("current_executor") or "").strip()
    )
    current_focus = (
        str(current_delegation.get("current_focus") or "").strip()
        or str(current_delegation.get("goal") or "").strip()
        or str(current_job.get("current_focus") or "").strip()
        or str(current_run.get("current_focus") or "").strip()
        or str(existing_control_plane.get("current_focus") or "").strip()
    )
    next_step = (
        str(current_delegation.get("next_step") or "").strip()
        or str(current_job.get("next_step") or "").strip()
        or str(current_run.get("next_step") or "").strip()
        or str(existing_control_plane.get("next_step") or "").strip()
    )
    blocker = (
        str(current_delegation.get("blocker") or "").strip()
        or str(current_job.get("blocker") or "").strip()
        or str(current_run.get("blocker") or "").strip()
        or str(existing_control_plane.get("blocker") or "").strip()
    )
    if approvals and not blocker:
        blocker = "Pending approval."
    if approvals and not next_step:
        next_step = "Owner/admin should resolve the pending approval."

    activity_candidates = [
        int(task.get("updated_at_unix") or 0),
        int(current_delegation.get("updated_at_unix") or current_delegation.get("finished_at_unix") or current_delegation.get("started_at_unix") or current_delegation.get("created_at_unix") or 0),
        int(current_job.get("updated_at_unix") or current_job.get("finished_at_unix") or current_job.get("started_at_unix") or current_job.get("created_at_unix") or 0),
        int(current_run.get("updated_at_unix") or current_run.get("finished_at_unix") or current_run.get("started_at_unix") or current_run.get("created_at_unix") or 0),
        max((int(item.get("created_at_unix") or 0) for item in approvals), default=0),
        int(existing_control_plane.get("activity_updated_at_unix") or 0),
    ]
    activity_updated_at_unix = max(activity_candidates) if any(activity_candidates) else int(task.get("created_at_unix") or 0)

    task_scope_key = (
        str(current_delegation.get("task_scope_key") or "").strip()
        or str(existing_control_plane.get("task_scope_key") or "").strip()
        or extract_background_job_task_scope_key(current_job)
        or extract_capability_run_task_scope_key(current_run)
    )
    person_memory_key = (
        str(current_delegation.get("person_memory_key") or "").strip()
        or str(existing_control_plane.get("person_memory_key") or "").strip()
        or extract_background_job_person_memory_key(current_job)
        or extract_capability_run_person_memory_key(current_run)
    )

    failure_kind = classify_failure(
        status=status,
        blocker=blocker,
        delivery_error=str(current_job.get("delivery_error") or "").strip(),
    )
    execution_status = derive_execution_status(
        task_status=task_status,
        run_status=run_status,
        job_status=job_status,
        delegation_status_value=delegation_status_value,
    )
    delivery_status = derive_delivery_status(current_job=current_job, execution_status=execution_status)
    recovery = recovery_hint(
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
        "delivery_target": str(current_job.get("delivery_target") or existing_control_plane.get("delivery_target") or "").strip(),
        "current_executor": current_executor,
        "current_focus": current_focus,
        "next_step": next_step,
        "blocker": blocker,
        "failure_kind": failure_kind,
        "recovery_hint": recovery,
        "dispatch_action": str(dispatch_decision.get("dispatch_action") or "").strip(),
        "suggested_executor": str(dispatch_decision.get("suggested_executor") or "").strip(),
        "pending_approval_count": len(approvals),
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "activity_updated_at_unix": activity_updated_at_unix,
    }


def build_task_runtime_snapshot(
    task: Dict[str, Any],
    *,
    runs: list[Dict[str, Any]] | None,
    jobs: list[Dict[str, Any]] | None,
    approval_rows: list[Dict[str, Any]] | None,
    delegation_rows: list[Dict[str, Any]] | None,
    resolve_job_by_id=None,
    active_only: bool = False,
) -> Dict[str, Any]:
    task_id = str(task.get("task_id") or "").strip()
    task_runs = [
        row for row in (runs or [])
        if str(row.get("task_id") or "").strip() == task_id
    ]
    active_runs = [row for row in task_runs if str(row.get("status") or "").strip().lower() in ACTIVE_RUN_STATUSES]
    current_run = active_runs[0] if active_runs else (task_runs[0] if task_runs and not active_only else None)
    run_ids = [str(row.get("run_id") or "").strip() for row in task_runs if str(row.get("run_id") or "").strip()]

    approvals: list[Dict[str, Any]] = []
    for item in approval_rows or []:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        payload_task_id = str(payload.get("task_id") or "").strip()
        if payload_task_id == task_id or str(item.get("target_id") or "").strip() in run_ids:
            approvals.append(item)
    approvals.sort(key=lambda item: int(item.get("created_at_unix") or 0), reverse=True)

    current_job = None
    candidate_job_ids: list[str] = []
    if current_run and str(current_run.get("background_job_id") or "").strip():
        candidate_job_ids.append(str(current_run.get("background_job_id") or "").strip())
    for row in task_runs:
        job_id = str(row.get("background_job_id") or "").strip()
        if job_id and job_id not in candidate_job_ids:
            candidate_job_ids.append(job_id)
    job_rows_by_id = {
        str(row.get("job_id") or "").strip(): row
        for row in (jobs or [])
        if str(row.get("job_id") or "").strip()
    }
    for job_id in candidate_job_ids:
        current_job = job_rows_by_id.get(job_id)
        if current_job:
            break
    if not current_job and callable(resolve_job_by_id):
        for job_id in candidate_job_ids:
            current_job = resolve_job_by_id(job_id)
            if current_job:
                break
    if not current_job:
        task_jobs = [
            row for row in (jobs or [])
            if str(row.get("task_id") or "").strip() == task_id
        ]
        active_task_jobs = [
            row for row in task_jobs if str(row.get("status") or "").strip().lower() in ACTIVE_JOB_STATUSES
        ]
        ranked_task_jobs = active_task_jobs or task_jobs
        if ranked_task_jobs:
            current_job = ranked_task_jobs[0]

    delegations = match_task_delegations(task, delegation_rows)
    active_delegations = [
        row for row in delegations if str(row.get("status") or "").strip().lower() in ACTIVE_DELEGATION_STATUSES
    ]
    current_delegation = active_delegations[0] if active_delegations else (delegations[0] if delegations and not active_only else None)

    control_summary = build_task_control_summary(
        task=task,
        current_run=current_run,
        current_job=current_job,
        current_delegation=current_delegation,
        approvals=approvals[:8],
    )

    return {
        "runs": task_runs,
        "current_run": current_run,
        "approvals": approvals,
        "current_job": current_job,
        "delegations": delegations,
        "current_delegation": current_delegation,
        "control_summary": control_summary,
    }


def enrich_task_control_summary(task: Dict[str, Any], control_summary: Dict[str, Any]) -> Dict[str, Any]:
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}
    previous_control_plane = metadata.get("control_plane") if isinstance(metadata.get("control_plane"), dict) else {}
    secretary_action = metadata.get("secretary_action") if isinstance(metadata.get("secretary_action"), dict) else {}
    secretary_follow_up = metadata.get("secretary_follow_up") if isinstance(metadata.get("secretary_follow_up"), dict) else {}
    operator_queue = metadata.get("operator_queue") if isinstance(metadata.get("operator_queue"), dict) else {}
    pending_operator_items = list(operator_queue.get("pending") or []) if isinstance(operator_queue.get("pending"), list) else []
    first_item = pending_operator_items[0] if pending_operator_items and isinstance(pending_operator_items[0], dict) else {}
    last_secretary_action = secretary_action.get("last_action") if isinstance(secretary_action.get("last_action"), dict) else {}
    last_follow_up = secretary_follow_up.get("last_item") if isinstance(secretary_follow_up.get("last_item"), dict) else {}

    enriched = dict(control_summary or {})
    if last_secretary_action:
        enriched["last_secretary_action"] = str(last_secretary_action.get("action") or "").strip()
        enriched["last_secretary_action_ok"] = bool(last_secretary_action.get("ok"))
        enriched["last_secretary_action_at_unix"] = int(last_secretary_action.get("recorded_at_unix") or 0)
        enriched["last_secretary_action_summary"] = str(last_secretary_action.get("summary") or last_secretary_action.get("message") or "").strip()
        enriched["requested_executor_override"] = str(last_secretary_action.get("requested_executor") or "").strip()
    else:
        enriched["last_secretary_action"] = str(previous_control_plane.get("last_secretary_action") or enriched.get("last_secretary_action") or "").strip()
        enriched["last_secretary_action_ok"] = bool(previous_control_plane.get("last_secretary_action_ok") if "last_secretary_action_ok" in previous_control_plane else enriched.get("last_secretary_action_ok"))
        enriched["last_secretary_action_at_unix"] = int(previous_control_plane.get("last_secretary_action_at_unix") or enriched.get("last_secretary_action_at_unix") or 0)
        enriched["last_secretary_action_summary"] = str(previous_control_plane.get("last_secretary_action_summary") or enriched.get("last_secretary_action_summary") or "").strip()
        enriched["requested_executor_override"] = str(previous_control_plane.get("requested_executor_override") or enriched.get("requested_executor_override") or "").strip()

    if last_follow_up:
        enriched["last_follow_up_action_id"] = str(last_follow_up.get("action_id") or "").strip()
        enriched["last_follow_up_ok"] = bool(last_follow_up.get("ok"))
        enriched["last_follow_up_at_unix"] = int(last_follow_up.get("recorded_at_unix") or 0)
        enriched["last_follow_up_summary"] = str(last_follow_up.get("summary") or "").strip()
        enriched["last_follow_up_target_ref"] = str(last_follow_up.get("target_ref") or "").strip()
    else:
        enriched["last_follow_up_action_id"] = str(previous_control_plane.get("last_follow_up_action_id") or enriched.get("last_follow_up_action_id") or "").strip()
        enriched["last_follow_up_ok"] = bool(previous_control_plane.get("last_follow_up_ok") if "last_follow_up_ok" in previous_control_plane else enriched.get("last_follow_up_ok"))
        enriched["last_follow_up_at_unix"] = int(previous_control_plane.get("last_follow_up_at_unix") or enriched.get("last_follow_up_at_unix") or 0)
        enriched["last_follow_up_summary"] = str(previous_control_plane.get("last_follow_up_summary") or enriched.get("last_follow_up_summary") or "").strip()
        enriched["last_follow_up_target_ref"] = str(previous_control_plane.get("last_follow_up_target_ref") or enriched.get("last_follow_up_target_ref") or "").strip()

    enriched["operator_queue_count"] = len(pending_operator_items) if pending_operator_items else int(previous_control_plane.get("operator_queue_count") or enriched.get("operator_queue_count") or 0)
    enriched["operator_queue_next"] = str(first_item.get("summary") or previous_control_plane.get("operator_queue_next") or enriched.get("operator_queue_next") or "").strip()
    enriched["last_operator_resolution"] = str(previous_control_plane.get("last_operator_resolution") or enriched.get("last_operator_resolution") or "").strip()
    enriched["last_operator_resolution_summary"] = str(previous_control_plane.get("last_operator_resolution_summary") or enriched.get("last_operator_resolution_summary") or "").strip()
    return enriched
