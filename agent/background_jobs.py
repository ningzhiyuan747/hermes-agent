from __future__ import annotations

import json
import os
import re
import time
import uuid
from pathlib import Path
from typing import Any, Dict, List, Optional

from hermes_constants import get_hermes_home

try:
    from agent.business_db import (
        _ensure_task_for_origin,
        get_task,
        get_channel_task,
        insert_background_job_event,
        upsert_background_job,
    )
except Exception:  # pragma: no cover - DB must not break the JSON fallback path.
    _ensure_task_for_origin = None  # type: ignore[assignment]
    get_task = None  # type: ignore[assignment]
    get_channel_task = None  # type: ignore[assignment]
    insert_background_job_event = None  # type: ignore[assignment]
    upsert_background_job = None  # type: ignore[assignment]


ACTIVE_STATUSES = {"queued", "running", "paused", "blocked"}
TERMINAL_STATUSES = {"completed", "failed", "cancelled"}
ALL_STATUSES = ACTIVE_STATUSES | TERMINAL_STATUSES


def jobs_root() -> Path:
    root = get_hermes_home() / "background_jobs"
    root.mkdir(parents=True, exist_ok=True)
    return root


def _job_dir(job_id: str) -> Path:
    safe = _safe_job_id(job_id)
    return jobs_root() / safe


def _safe_job_id(job_id: str) -> str:
    return re.sub(r"[^A-Za-z0-9_.-]", "", str(job_id or "").strip())


def _now() -> int:
    return int(time.time())


def _timestamp() -> str:
    return time.strftime("%Y-%m-%d %H:%M:%S")


def _short_title(text: str, limit: int = 80) -> str:
    cleaned = " ".join(str(text or "").split())
    if len(cleaned) <= limit:
        return cleaned
    return cleaned[: limit - 3] + "..."


def _new_trace_id() -> str:
    return "trace-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _normalize_trace_id(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized or _new_trace_id()


def _atomic_write_json(path: Path, data: Dict[str, Any]) -> None:
    tmp = path.with_suffix(path.suffix + ".tmp")
    tmp.write_text(json.dumps(data, ensure_ascii=False, indent=2), encoding="utf-8")
    os.replace(tmp, path)


def _read_json(path: Path) -> Dict[str, Any]:
    try:
        value = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {}
    return value if isinstance(value, dict) else {}


def _mirror_job_to_db(record: Dict[str, Any]) -> None:
    if upsert_background_job is None:
        return
    try:
        upsert_background_job(record)
    except Exception:
        pass


def _mirror_event_to_db(job_id: str, event: Dict[str, Any]) -> None:
    if insert_background_job_event is None:
        return
    try:
        insert_background_job_event(job_id, event)
    except Exception:
        pass


def _tag_value(tags: List[str], prefix: str) -> str:
    expected = f"{str(prefix or '').strip().lower()}:"
    if not expected or expected == ":":
        return ""
    for tag in tags:
        normalized = str(tag or "").strip()
        if normalized.lower().startswith(expected):
            return normalized.split(":", 1)[1].strip()
    return ""


def _sync_task_control(task_id: str) -> None:
    normalized = str(task_id or "").strip()
    if not normalized:
        return
    try:
        from agent.task_panel_service import sync_task_control_state

        sync_task_control_state(normalized)
    except Exception:
        pass


def _resolve_task_id_for_record(record: Dict[str, Any], *, create_if_missing: bool = False) -> str:
    current_task_id = str(record.get("task_id") or "").strip()
    if current_task_id:
        return current_task_id

    tags = [str(item).strip() for item in (record.get("tags") or []) if str(item).strip()]
    scope_task_id = _tag_value(tags, "task_scope")
    if scope_task_id.startswith("task:"):
        resolved = scope_task_id.split(":", 1)[1].strip()
        if resolved and (get_task is None or get_task(resolved)):
            record["task_id"] = resolved
            return resolved

    origin = record.get("origin") if isinstance(record.get("origin"), dict) else {}
    platform = str(origin.get("platform") or "").strip().lower()
    chat_id = str(origin.get("chat_id") or "").strip()
    thread_id = str(origin.get("thread_id") or "").strip()
    if platform and chat_id and get_channel_task is not None:
        try:
            bound = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id) or {}
        except Exception:
            bound = {}
        task = bound.get("task") if isinstance(bound, dict) else None
        bound_task_id = str((task or {}).get("task_id") or "").strip()
        if bound_task_id:
            record["task_id"] = bound_task_id
            return bound_task_id

    if create_if_missing and _ensure_task_for_origin is not None and platform and chat_id:
        try:
            created_task_id = _ensure_task_for_origin(
                title=str(record.get("title") or "").strip(),
                goal=str(record.get("prompt") or "").strip(),
                actor_user_id=str(record.get("user_id") or "").strip(),
                session_id=str(record.get("session_id") or "").strip(),
                origin=origin,
                metadata={
                    "materialized_by": "background_job.create_job",
                    "executor": str(record.get("executor") or "").strip(),
                    "background_job": True,
                },
            )
        except Exception:
            created_task_id = ""
        if created_task_id:
            record["task_id"] = created_task_id
            return created_task_id

    return ""


def _append_event(job_dir: Path, event: Dict[str, Any], *, job_id: str = "") -> None:
    event = {
        "timestamp_unix": _now(),
        "timestamp": _timestamp(),
        **event,
    }
    if job_id and not event.get("trace_id"):
        record = get_job(job_id)
        if record and str(record.get("trace_id") or "").strip():
            event["trace_id"] = str(record.get("trace_id") or "").strip()
    with (job_dir / "events.jsonl").open("a", encoding="utf-8") as fh:
        fh.write(json.dumps(event, ensure_ascii=False) + "\n")
    if job_id:
        _mirror_event_to_db(job_id, event)


def create_job(
    title: str,
    prompt: str,
    *,
    origin: Optional[Dict[str, Any]] = None,
    session_id: str = "",
    user_id: str = "",
    priority: str = "normal",
    executor: str = "",
    tags: Optional[List[str]] = None,
    trace_id: str = "",
) -> Dict[str, Any]:
    job_id = "job-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    job_dir = _job_dir(job_id)
    job_dir.mkdir(parents=True, exist_ok=False)

    now = _now()
    record: Dict[str, Any] = {
        "job_id": job_id,
        "trace_id": _normalize_trace_id(trace_id),
        "task_id": "",
        "title": _short_title(title or prompt),
        "prompt": str(prompt or "").strip(),
        "status": "queued",
        "priority": str(priority or "normal"),
        "tags": [str(item).strip() for item in (tags or []) if str(item).strip()],
        "origin": origin or {},
        "session_id": str(session_id or ""),
        "user_id": str(user_id or ""),
        "created_at_unix": now,
        "updated_at_unix": now,
        "started_at_unix": None,
        "finished_at_unix": None,
        "current_focus": "Queued; waiting for an executor.",
        "next_step": "Start execution or assign this job to a worker.",
        "blocker": "",
        "result": "",
        "artifact_paths": [],
        "job_dir": str(job_dir),
        "events_path": str(job_dir / "events.jsonl"),
        "executor": str(executor or "").strip(),
        "runner_pid": None,
        "runner_runtime": "",
    }
    _resolve_task_id_for_record(record, create_if_missing=True)
    _atomic_write_json(job_dir / "job.json", record)
    _mirror_job_to_db(record)
    _append_event(job_dir, {"kind": "created", "status": "queued", "message": record["title"]}, job_id=job_id)
    _sync_task_control(str(record.get("task_id") or "").strip())
    return record


def get_job(job_id: str) -> Optional[Dict[str, Any]]:
    safe = _safe_job_id(job_id)
    if not safe:
        return None
    path = _job_dir(safe) / "job.json"
    if not path.exists():
        return None
    return _read_json(path)


def update_job(job_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    record = get_job(job_id)
    if not record:
        return None
    job_dir = Path(record.get("job_dir") or _job_dir(job_id))

    status = fields.get("status")
    if status is not None:
        status = str(status).strip().lower()
        if status not in ALL_STATUSES:
            raise ValueError(f"Invalid job status: {status}")
        fields["status"] = status
        if status == "running" and not record.get("started_at_unix"):
            fields["started_at_unix"] = _now()
        if status in TERMINAL_STATUSES and not record.get("finished_at_unix"):
            fields["finished_at_unix"] = _now()

    allowed = {
        "status",
        "task_id",
        "current_focus",
        "next_step",
        "blocker",
        "result",
        "priority",
        "artifact_paths",
        "started_at_unix",
        "finished_at_unix",
        "executor",
        "runner_pid",
        "runner_runtime",
        "delivery_status",
        "delivery_error",
        "delivery_target",
        "delivered_at_unix",
    }
    for key, value in fields.items():
        if key in allowed and value is not None:
            record[key] = value
    record["updated_at_unix"] = _now()
    _resolve_task_id_for_record(record, create_if_missing=False)
    _atomic_write_json(job_dir / "job.json", record)
    _mirror_job_to_db(record)
    _append_event(
        job_dir,
        {
            "kind": "update",
            "status": record.get("status"),
            "message": fields.get("current_focus") or fields.get("result") or fields.get("blocker") or "job updated",
        },
        job_id=job_id,
    )
    _sync_task_control(str(record.get("task_id") or "").strip())
    return record


def claim_next_job(*, executor: str = "background-job-worker") -> Optional[Dict[str, Any]]:
    """Claim the oldest queued job for execution.

    This uses a simple lock-file per job. It is intentionally conservative:
    if another process already owns the lock, the job is skipped.
    """
    queued = sorted(
        [row for row in iter_jobs() if str(row.get("status") or "") == "queued"],
        key=lambda item: int(item.get("created_at_unix") or 0),
    )
    for row in queued:
        job_id = str(row.get("job_id") or "")
        job_dir = Path(row.get("job_dir") or _job_dir(job_id))
        lock_path = job_dir / "run.lock"
        try:
            fd = os.open(str(lock_path), os.O_CREAT | os.O_EXCL | os.O_WRONLY)
            with os.fdopen(fd, "w", encoding="utf-8") as fh:
                fh.write(f"{executor}\n{_timestamp()}\n")
        except FileExistsError:
            continue
        except OSError:
            continue
        claimed = update_job(
            job_id,
            status="running",
            executor=executor,
            current_focus="Executor claimed the job and is preparing to run.",
            next_step="Run Hermes on the persisted job prompt and capture the result.",
        )
        return claimed
    return None


def release_job_lock(job_id: str) -> None:
    record = get_job(job_id)
    if not record:
        return
    lock_path = Path(record.get("job_dir") or _job_dir(job_id)) / "run.lock"
    try:
        lock_path.unlink(missing_ok=True)
    except Exception:
        pass


def append_job_event(
    job_id: str,
    *,
    kind: str = "progress",
    message: str = "",
    status: str = "",
    current_focus: str = "",
    next_step: str = "",
    blocker: str = "",
) -> Optional[Dict[str, Any]]:
    record = get_job(job_id)
    if not record:
        return None
    job_dir = Path(record.get("job_dir") or _job_dir(job_id))
    update_fields: Dict[str, Any] = {}
    if status:
        update_fields["status"] = status
    if current_focus:
        update_fields["current_focus"] = current_focus
    if next_step:
        update_fields["next_step"] = next_step
    if blocker:
        update_fields["blocker"] = blocker
    if update_fields:
        record = update_job(job_id, **update_fields) or record
    else:
        record["updated_at_unix"] = _now()
        _atomic_write_json(job_dir / "job.json", record)
        _mirror_job_to_db(record)
    _append_event(
        job_dir,
        {
            "kind": str(kind or "progress"),
            "status": status or record.get("status"),
            "message": str(message or current_focus or next_step or blocker or "progress"),
        },
        job_id=job_id,
    )
    return record


def iter_jobs() -> List[Dict[str, Any]]:
    records: List[Dict[str, Any]] = []
    for path in jobs_root().glob("job-*/job.json"):
        record = _read_json(path)
        if record:
            records.append(record)
    records.sort(key=lambda item: int(item.get("updated_at_unix") or item.get("created_at_unix") or 0), reverse=True)
    return records


def list_jobs(status: str = "", limit: int = 10, active_only: bool = False) -> List[Dict[str, Any]]:
    normalized_status = str(status or "").strip().lower()
    rows = iter_jobs()
    if active_only:
        rows = [row for row in rows if str(row.get("status") or "") in ACTIVE_STATUSES]
    elif normalized_status:
        rows = [row for row in rows if str(row.get("status") or "") == normalized_status]
    return rows[: max(1, int(limit or 10))]


def recent_events(job_id: str, limit: int = 8) -> List[Dict[str, Any]]:
    record = get_job(job_id)
    if not record:
        return []
    path = Path(record.get("events_path") or Path(record.get("job_dir") or _job_dir(job_id)) / "events.jsonl")
    if not path.exists():
        return []
    events: List[Dict[str, Any]] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(limit, 1):]
    except Exception:
        return []
    for line in lines:
        try:
            value = json.loads(line)
        except json.JSONDecodeError:
            continue
        if isinstance(value, dict):
            events.append(value)
    return events


def render_jobs_status(status: str = "", limit: int = 10, active_only: bool = False) -> str:
    rows = list_jobs(status=status, limit=limit, active_only=active_only)
    header = "后台任务看板"
    if active_only:
        header += "（活跃）"
    elif status:
        header += f"（{status}）"
    if not rows:
        return f"{header}\n\n没有找到后台任务。"

    lines = [header, ""]
    for row in rows:
        updated = int(row.get("updated_at_unix") or row.get("created_at_unix") or 0)
        stamp = time.strftime("%m-%d %H:%M", time.localtime(updated)) if updated else "unknown"
        lines.append(f"- {row.get('job_id')} | {row.get('status')} | {stamp} | {row.get('title')}")
        focus = str(row.get("current_focus") or "").strip()
        next_step = str(row.get("next_step") or "").strip()
        blocker = str(row.get("blocker") or "").strip()
        if focus:
            lines.append(f"  当前: {_short_title(focus, 100)}")
        if next_step:
            lines.append(f"  下一步: {_short_title(next_step, 100)}")
        if blocker:
            lines.append(f"  阻塞: {_short_title(blocker, 100)}")
    return "\n".join(lines)
