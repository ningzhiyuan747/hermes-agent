#!/usr/bin/env python3
"""Background job state tool.

This is intentionally a state/control primitive first.  Executors can be
attached later without changing the persisted job format.
"""

from __future__ import annotations

import json
from typing import Any, Dict, Optional

from agent.background_jobs import (
    append_job_event,
    create_job,
    get_job,
    list_jobs,
    recent_events,
    render_jobs_status,
    update_job,
)


def _origin_from_session() -> Dict[str, str]:
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return {}
    origin = {
        "platform": get_session_env("HERMES_SESSION_PLATFORM") or "",
        "chat_id": get_session_env("HERMES_SESSION_CHAT_ID") or "",
        "chat_name": get_session_env("HERMES_SESSION_CHAT_NAME") or "",
        "thread_id": get_session_env("HERMES_SESSION_THREAD_ID") or "",
    }
    return {k: v for k, v in origin.items() if v}


def _session_value(key: str) -> str:
    try:
        from gateway.session_context import get_session_env
        return get_session_env(key) or ""
    except Exception:
        return ""


def background_job(args: Dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "list").strip().lower()

    if action == "create":
        prompt = str(args.get("prompt") or "").strip()
        if not prompt:
            return json.dumps({"error": "background_job.create requires prompt"}, ensure_ascii=False)
        record = create_job(
            title=str(args.get("title") or prompt).strip(),
            prompt=prompt,
            origin=args.get("origin") if isinstance(args.get("origin"), dict) else _origin_from_session(),
            session_id=str(args.get("session_id") or _session_value("HERMES_SESSION_ID") or ""),
            user_id=str(args.get("user_id") or _session_value("HERMES_SESSION_USER_ID") or ""),
            priority=str(args.get("priority") or "normal"),
            tags=args.get("tags") if isinstance(args.get("tags"), list) else None,
        )
        return json.dumps({"ok": True, "job": record}, ensure_ascii=False)

    if action in {"list", "status"}:
        active_only = bool(args.get("active_only")) or action == "status"
        status = str(args.get("status") or "").strip().lower()
        limit = int(args.get("limit") or 10)
        return json.dumps(
            {
                "ok": True,
                "jobs": list_jobs(status=status, limit=limit, active_only=active_only),
                "text": render_jobs_status(status=status, limit=limit, active_only=active_only),
            },
            ensure_ascii=False,
        )

    if action == "get":
        job_id = str(args.get("job_id") or "").strip()
        record = get_job(job_id)
        if not record:
            return json.dumps({"error": f"job not found: {job_id}"}, ensure_ascii=False)
        return json.dumps(
            {"ok": True, "job": record, "events": recent_events(job_id, int(args.get("limit") or 8))},
            ensure_ascii=False,
        )

    if action in {"update", "start", "pause", "complete", "fail", "cancel"}:
        job_id = str(args.get("job_id") or "").strip()
        status_map = {
            "start": "running",
            "pause": "paused",
            "complete": "completed",
            "fail": "failed",
            "cancel": "cancelled",
        }
        fields: Dict[str, Any] = {
            "status": status_map.get(action, args.get("status")),
            "current_focus": args.get("current_focus"),
            "next_step": args.get("next_step"),
            "blocker": args.get("blocker"),
            "result": args.get("result"),
            "artifact_paths": args.get("artifact_paths"),
            "executor": args.get("executor"),
        }
        record = update_job(job_id, **fields)
        if not record:
            return json.dumps({"error": f"job not found: {job_id}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "job": record}, ensure_ascii=False)

    if action == "append_event":
        job_id = str(args.get("job_id") or "").strip()
        record = append_job_event(
            job_id,
            kind=str(args.get("kind") or "progress"),
            message=str(args.get("message") or ""),
            status=str(args.get("status") or ""),
            current_focus=str(args.get("current_focus") or ""),
            next_step=str(args.get("next_step") or ""),
            blocker=str(args.get("blocker") or ""),
        )
        if not record:
            return json.dumps({"error": f"job not found: {job_id}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "job": record}, ensure_ascii=False)

    return json.dumps({"error": f"unknown background_job action: {action}"}, ensure_ascii=False)


BACKGROUND_JOB_SCHEMA = {
    "name": "background_job",
    "description": (
        "Create and manage durable background job state for long tasks. "
        "Use this when a task should not block the chat: create a job, append progress, and later mark it completed/failed/cancelled. "
        "This tool records state; executor automation is attached separately."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["create", "list", "status", "get", "update", "start", "pause", "complete", "fail", "cancel", "append_event"],
                "description": "Job action to perform.",
            },
            "job_id": {"type": "string", "description": "Background job id for get/update/event actions."},
            "title": {"type": "string", "description": "Short job title."},
            "prompt": {"type": "string", "description": "Full task prompt for a new background job."},
            "status": {"type": "string", "description": "Optional status filter or update value."},
            "current_focus": {"type": "string", "description": "Latest work focus."},
            "next_step": {"type": "string", "description": "Next concrete action."},
            "blocker": {"type": "string", "description": "Current blocker or risk."},
            "message": {"type": "string", "description": "Progress event message."},
            "result": {"type": "string", "description": "Final or intermediate result."},
            "priority": {"type": "string", "description": "Priority label, e.g. normal/high."},
            "tags": {"type": "array", "items": {"type": "string"}, "description": "Optional tags."},
            "artifact_paths": {"type": "array", "items": {"type": "string"}, "description": "Artifact paths created by the job."},
            "limit": {"type": "integer", "description": "Maximum jobs/events to return."},
            "active_only": {"type": "boolean", "description": "Only list queued/running/paused/blocked jobs."},
        },
        "required": ["action"],
    },
}


from tools.registry import registry

registry.register(
    name="background_job",
    toolset="jobs",
    schema=BACKGROUND_JOB_SCHEMA,
    handler=lambda args, **kw: background_job(args, **kw),
    emoji="📌",
)
