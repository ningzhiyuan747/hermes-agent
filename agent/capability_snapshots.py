from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}


def resolve_task_binding(
    *,
    platform: str,
    chat_id: str,
    thread_id: str,
    get_channel_task_func: Optional[Callable[..., Dict[str, Any]]],
) -> Tuple[Dict[str, Any], str]:
    task_context: Dict[str, Any] = {}
    task_id = ""
    if get_channel_task_func is not None and chat_id:
        try:
            task_context = get_channel_task_func(platform=platform, chat_id=chat_id, thread_id=thread_id) or {}
        except Exception:
            task_context = {}
    task = task_context.get("task") if isinstance(task_context.get("task"), dict) else {}
    if isinstance(task, dict):
        task_id = str(task.get("task_id") or "").strip()
    return dict(task) if isinstance(task, dict) else {}, task_id


def build_artifact_items(
    run_id: str,
    *,
    list_capability_artifacts_func: Optional[Callable[..., List[Dict[str, Any]]]],
    limit: int = 3,
) -> List[Dict[str, Any]]:
    if not run_id or list_capability_artifacts_func is None:
        return []
    artifact_items: List[Dict[str, Any]] = []
    try:
        for artifact in list_capability_artifacts_func(run_id, limit=limit):
            artifact_items.append(
                {
                    "kind": str(artifact.get("kind") or "").strip(),
                    "label": str(artifact.get("label") or "").strip(),
                    "path_or_ref": str(artifact.get("path_or_ref") or "").strip(),
                    "summary": str(artifact.get("summary") or "").strip(),
                    "metadata": artifact.get("metadata") if isinstance(artifact.get("metadata"), dict) else {},
                    "created_at_unix": int(artifact.get("created_at_unix") or 0),
                }
            )
    except Exception:
        return []
    return artifact_items


def enrich_capability_run_payload(
    payload: Dict[str, Any],
    *,
    task_id: str = "",
    task_title: str = "",
    shared_scope: str = "",
    list_capability_artifacts_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    record = dict(payload)
    record["trace_id"] = str(record.get("trace_id") or "").strip()
    if task_id:
        record["task_id"] = task_id
        record["task_title"] = task_title
    if shared_scope:
        record["shared_scope"] = shared_scope
    run_id = str(record.get("run_id") or "").strip()
    record["artifact_items"] = build_artifact_items(
        run_id,
        list_capability_artifacts_func=list_capability_artifacts_func,
    )
    return record


def _find_capability_run_for_job(
    payload: Dict[str, Any],
    tags: List[str],
    job_id: str,
    *,
    runs: List[Dict[str, Any]],
) -> Optional[Dict[str, Any]]:
    for tag in tags:
        if tag.startswith("capability_run:") and tag.split(":", 1)[1]:
            wanted = tag.split(":", 1)[1].strip()
            for item in runs:
                if str(item.get("run_id") or "").strip() == wanted:
                    return item
    for item in runs:
        if str(item.get("background_job_id") or "").strip() == str(job_id or "").strip():
            return item
    return None


def enrich_background_job_payload(
    payload: Dict[str, Any],
    *,
    runs: List[Dict[str, Any]],
    list_capability_artifacts_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Dict[str, Any]:
    record = dict(payload)
    tags = [str(item).strip().lower() for item in (record.get("tags") or []) if str(item).strip()]
    record["tags"] = tags
    record["trace_id"] = str(record.get("trace_id") or "").strip()
    run = _find_capability_run_for_job(record, tags, str(record.get("job_id") or "").strip(), runs=runs)
    if run is None:
        return record
    run_id = str(run.get("run_id") or "").strip()
    record.update(
        {
            "trace_id": str(run.get("trace_id") or "").strip() or str(record.get("trace_id") or "").strip(),
            "capability_run_id": run_id,
            "task_id": str(run.get("task_id") or "").strip(),
            "capability_name": str(run.get("capability_name") or "").strip(),
            "capability_status": str(run.get("status") or "").strip(),
            "approval_id": str(run.get("approval_id") or "").strip(),
            "capability_result": str(run.get("result") or "").strip(),
            "capability_focus": str(run.get("current_focus") or "").strip(),
            "capability_next_step": str(run.get("next_step") or "").strip(),
            "capability_blocker": str(run.get("blocker") or "").strip(),
            "artifact_items": build_artifact_items(
                run_id,
                list_capability_artifacts_func=list_capability_artifacts_func,
            ),
        }
    )
    return record


def pick_background_job_snapshot(
    rows: List[Dict[str, Any]],
    runs: List[Dict[str, Any]],
    *,
    session_id: str,
    task_id: str = "",
    task_title: str = "",
    global_fallback: bool = False,
    list_capability_artifacts_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for row in rows:
        tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
        if "executor:openclaw" not in tags:
            continue
        payload = enrich_background_job_payload(
            dict(row),
            runs=runs,
            list_capability_artifacts_func=list_capability_artifacts_func,
        )
        payload_task_id = str(payload.get("task_id") or "").strip()
        session_match = str(payload.get("session_id") or "").strip() == session_id
        task_match = bool(task_id and payload_task_id == task_id)
        if task_id:
            if not task_match and not (not payload_task_id and session_match):
                continue
            payload["task_id"] = task_id
            payload["task_title"] = task_title
        elif not session_match:
            continue
        matches.append(payload)
    if not matches and global_fallback:
        for row in rows:
            tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
            if "executor:openclaw" not in tags:
                continue
            payload = enrich_background_job_payload(
                dict(row),
                runs=runs,
                list_capability_artifacts_func=list_capability_artifacts_func,
            )
            payload["shared_scope"] = "global"
            if task_id:
                if str(payload.get("task_id") or "").strip() != task_id:
                    continue
                payload["task_id"] = task_id
                payload["task_title"] = task_title
            matches.append(payload)
    matches.sort(key=lambda item: int(item.get("updated_at_unix") or 0), reverse=True)
    return matches[0] if matches else None


def pick_capability_run_snapshot(
    rows: List[Dict[str, Any]],
    *,
    session_id: str,
    task_id: str = "",
    task_title: str = "",
    active_only: bool = True,
    global_fallback: bool = False,
    list_capability_artifacts_func: Optional[Callable[..., List[Dict[str, Any]]]] = None,
) -> Optional[Dict[str, Any]]:
    matches: List[Dict[str, Any]] = []
    for row in rows:
        status = str(row.get("status") or "").strip().lower()
        if active_only and status not in ACTIVE_RUN_STATUSES:
            continue
        row_task_id = str(row.get("task_id") or "").strip()
        session_match = str(row.get("session_id") or "").strip() == session_id
        if task_id:
            if row_task_id != task_id and not (not row_task_id and session_match):
                continue
        elif not session_match:
            continue
        matches.append(
            enrich_capability_run_payload(
                dict(row),
                task_id=task_id,
                task_title=task_title,
                list_capability_artifacts_func=list_capability_artifacts_func,
            )
        )
    if not matches and global_fallback:
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if active_only and status not in ACTIVE_RUN_STATUSES:
                continue
            if task_id and str(row.get("task_id") or "").strip() != task_id:
                continue
            matches.append(
                enrich_capability_run_payload(
                    dict(row),
                    task_id=task_id,
                    task_title=task_title,
                    shared_scope="global",
                    list_capability_artifacts_func=list_capability_artifacts_func,
                )
            )
    matches.sort(key=lambda item: int(item.get("updated_at_unix") or 0), reverse=True)
    return matches[0] if matches else None
