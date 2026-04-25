from __future__ import annotations

from typing import Any, Dict, Optional

from agent.business_db import (
    get_task,
    list_approvals,
    list_capability_artifacts,
    list_capability_runs,
    update_task,
)
from agent.background_jobs import get_job, list_jobs
from agent.task_runtime_summary import (
    build_task_runtime_snapshot,
    enrich_task_control_summary,
)
from scripts.subagent_task_status import _load_task_meta


def get_task_panel_snapshot(task_id: str, *, active_only: bool = False) -> Optional[Dict[str, Any]]:
    task = get_task(task_id)
    if not task:
        return None
    metadata = task.get("metadata") if isinstance(task.get("metadata"), dict) else {}

    runs = list_capability_runs(task_id=task_id, limit=20)
    approval_rows = list_approvals(status="pending", limit=100)
    runtime_snapshot = build_task_runtime_snapshot(
        task,
        runs=runs,
        jobs=list_jobs(limit=50, active_only=False),
        approval_rows=approval_rows,
        delegation_rows=_load_task_meta(),
        resolve_job_by_id=get_job,
        active_only=active_only,
    )
    current_run = runtime_snapshot.get("current_run")
    current_job = runtime_snapshot.get("current_job")
    approvals = runtime_snapshot.get("approvals") if isinstance(runtime_snapshot.get("approvals"), list) else []
    delegations = runtime_snapshot.get("delegations") if isinstance(runtime_snapshot.get("delegations"), list) else []
    current_delegation = runtime_snapshot.get("current_delegation")

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

    control_summary = enrich_task_control_summary(task, dict(runtime_snapshot.get("control_summary") or {}))

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
        "delivery_target": str(control_summary.get("delivery_target") or previous_control_plane.get("delivery_target") or "").strip(),
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
