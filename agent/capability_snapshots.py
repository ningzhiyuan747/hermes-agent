from __future__ import annotations

from typing import Any, Callable, Dict, List, Optional, Tuple


ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}


def build_task_scope_key(
    *,
    platform: str = "",
    chat_id: str = "",
    thread_id: str = "",
    task_id: str = "",
) -> str:
    normalized_platform = str(platform or "").strip().lower()
    normalized_chat_id = str(chat_id or "").strip()
    normalized_thread_id = str(thread_id or "").strip()
    normalized_task_id = str(task_id or "").strip()
    if normalized_platform and normalized_chat_id:
        task_scope_key = f"{normalized_platform}:chat:{normalized_chat_id}"
        if normalized_thread_id:
            task_scope_key += f":thread:{normalized_thread_id}"
        return task_scope_key
    if normalized_task_id:
        return f"task:{normalized_task_id}"
    return ""


def _payload_key(payload: Any, key: str) -> str:
    if not isinstance(payload, dict):
        return ""
    return str(payload.get(key) or "").strip()


def _payload_task_scope_key(payload: Any) -> str:
    return _payload_key(payload, "task_scope_key")


def _payload_person_memory_key(payload: Any) -> str:
    return _payload_key(payload, "person_memory_key")


def extract_capability_run_task_scope_key(row: Dict[str, Any]) -> str:
    return (
        str(row.get("task_scope_key") or "").strip()
        or _payload_task_scope_key(row.get("output"))
        or _payload_task_scope_key(row.get("input"))
    )


def extract_capability_run_person_memory_key(row: Dict[str, Any]) -> str:
    return (
        str(row.get("person_memory_key") or "").strip()
        or _payload_person_memory_key(row.get("output"))
        or _payload_person_memory_key(row.get("input"))
    )


def _job_tag_value(tags: List[str], prefix: str) -> str:
    expected = f"{str(prefix or '').strip().lower()}:"
    if not expected or expected == ":":
        return ""
    for tag in tags:
        if tag.startswith(expected) and tag.split(":", 1)[1]:
            return tag.split(":", 1)[1].strip()
    return ""


def extract_background_job_task_scope_key(row: Dict[str, Any]) -> str:
    tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
    return (
        str(row.get("task_scope_key") or "").strip()
        or _job_tag_value(tags, "task_scope")
        or _payload_task_scope_key(row.get("payload"))
    )


def extract_background_job_person_memory_key(row: Dict[str, Any]) -> str:
    tags = [str(item).strip().lower() for item in (row.get("tags") or []) if str(item).strip()]
    return (
        str(row.get("person_memory_key") or "").strip()
        or _job_tag_value(tags, "person_memory")
        or _payload_person_memory_key(row.get("payload"))
    )


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
    record["task_scope_key"] = extract_capability_run_task_scope_key(record)
    record["person_memory_key"] = extract_capability_run_person_memory_key(record)
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
    wanted = _job_tag_value(tags, "capability_run")
    if wanted:
        for item in runs:
            if str(item.get("run_id") or "").strip() == wanted:
                return item
    for item in runs:
        if str(item.get("background_job_id") or "").strip() == str(job_id or "").strip():
            return item
    return None


def _scope_matches(*, wanted_scope_key: str, candidate_scope_key: str) -> bool:
    normalized_wanted = str(wanted_scope_key or "").strip()
    normalized_candidate = str(candidate_scope_key or "").strip()
    return bool(normalized_wanted and normalized_candidate and normalized_candidate == normalized_wanted)


def _matches_task_or_session(
    *,
    task_id: str,
    payload_task_id: str,
    session_match: bool,
) -> bool:
    if task_id:
        return bool(payload_task_id == task_id or (not payload_task_id and session_match))
    return session_match


def _global_fallback_match(
    *,
    task_scope_key: str,
    candidate_scope_key: str,
    task_id: str,
    payload_task_id: str,
) -> bool:
    if _scope_matches(wanted_scope_key=task_scope_key, candidate_scope_key=candidate_scope_key):
        return True
    if task_id:
        return payload_task_id == task_id
    return not task_scope_key


def _apply_scope_metadata(
    payload: Dict[str, Any],
    *,
    task_id: str = "",
    task_title: str = "",
    task_scope_key: str = "",
) -> Dict[str, Any]:
    if task_id:
        payload["task_id"] = task_id
        payload["task_title"] = task_title
    if task_scope_key:
        payload["task_scope_key"] = task_scope_key
    if not str(payload.get("person_memory_key") or "").strip():
        payload["person_memory_key"] = extract_capability_run_person_memory_key(payload) or extract_background_job_person_memory_key(payload)
    return payload


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
    record["task_scope_key"] = extract_background_job_task_scope_key(record)
    record["person_memory_key"] = extract_background_job_person_memory_key(record)
    run = _find_capability_run_for_job(record, tags, str(record.get("job_id") or "").strip(), runs=runs)
    if run is None:
        return record
    run_id = str(run.get("run_id") or "").strip()
    run_task_scope_key = extract_capability_run_task_scope_key(run)
    run_person_memory_key = extract_capability_run_person_memory_key(run)
    record.update(
        {
            "trace_id": str(run.get("trace_id") or "").strip() or str(record.get("trace_id") or "").strip(),
            "capability_run_id": run_id,
            "task_id": str(run.get("task_id") or "").strip(),
            "task_scope_key": run_task_scope_key or str(record.get("task_scope_key") or "").strip(),
            "person_memory_key": run_person_memory_key or str(record.get("person_memory_key") or "").strip(),
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
    task_scope_key: str = "",
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
        payload_scope_key = str(payload.get("task_scope_key") or "").strip()
        payload_task_id = str(payload.get("task_id") or "").strip()
        session_match = str(payload.get("session_id") or "").strip() == session_id
        if _scope_matches(wanted_scope_key=task_scope_key, candidate_scope_key=payload_scope_key):
            matches.append(
                _apply_scope_metadata(
                    payload,
                    task_id=task_id,
                    task_title=task_title,
                    task_scope_key=task_scope_key,
                )
            )
            continue
        if payload_scope_key and task_scope_key:
            continue
        if not _matches_task_or_session(
            task_id=task_id,
            payload_task_id=payload_task_id,
            session_match=session_match,
        ):
            continue
        matches.append(
            _apply_scope_metadata(
                payload,
                task_id=task_id,
                task_title=task_title,
                task_scope_key=task_scope_key,
            )
        )
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
            if not _global_fallback_match(
                task_scope_key=task_scope_key,
                candidate_scope_key=str(payload.get("task_scope_key") or "").strip(),
                task_id=task_id,
                payload_task_id=str(payload.get("task_id") or "").strip(),
            ):
                continue
            matches.append(
                _apply_scope_metadata(
                    payload,
                    task_id=task_id,
                    task_title=task_title,
                    task_scope_key=task_scope_key,
                )
            )
    matches.sort(key=lambda item: int(item.get("updated_at_unix") or 0), reverse=True)
    return matches[0] if matches else None


def pick_capability_run_snapshot(
    rows: List[Dict[str, Any]],
    *,
    session_id: str,
    task_scope_key: str = "",
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
        row_task_scope_key = extract_capability_run_task_scope_key(row)
        session_match = str(row.get("session_id") or "").strip() == session_id
        if _scope_matches(wanted_scope_key=task_scope_key, candidate_scope_key=row_task_scope_key):
            payload = enrich_capability_run_payload(
                dict(row),
                task_id=task_id,
                task_title=task_title,
                list_capability_artifacts_func=list_capability_artifacts_func,
            )
            payload["task_scope_key"] = task_scope_key
            matches.append(payload)
            continue
        if row_task_scope_key and task_scope_key:
            continue
        if not _matches_task_or_session(
            task_id=task_id,
            payload_task_id=row_task_id,
            session_match=session_match,
        ):
            continue
        payload = enrich_capability_run_payload(
            dict(row),
            task_id=task_id,
            task_title=task_title,
            list_capability_artifacts_func=list_capability_artifacts_func,
        )
        if task_scope_key:
            payload["task_scope_key"] = task_scope_key
        matches.append(payload)
    if not matches and global_fallback:
        for row in rows:
            status = str(row.get("status") or "").strip().lower()
            if active_only and status not in ACTIVE_RUN_STATUSES:
                continue
            if not _global_fallback_match(
                task_scope_key=task_scope_key,
                candidate_scope_key=extract_capability_run_task_scope_key(row),
                task_id=task_id,
                payload_task_id=str(row.get("task_id") or "").strip(),
            ):
                continue
            payload = enrich_capability_run_payload(
                dict(row),
                task_id=task_id,
                task_title=task_title,
                shared_scope="global",
                list_capability_artifacts_func=list_capability_artifacts_func,
            )
            if task_scope_key:
                payload["task_scope_key"] = task_scope_key
            matches.append(payload)
    matches.sort(key=lambda item: int(item.get("updated_at_unix") or 0), reverse=True)
    return matches[0] if matches else None
