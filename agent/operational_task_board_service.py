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


def _contains(text: Any, needle: str) -> bool:
    return needle in str(text or "").strip().lower()


def _derive_system_signals(*, runs: list[dict[str, Any]], jobs: list[dict[str, Any]]) -> list[str]:
    signals: list[str] = []
    openclaw_smokes = [
        row
        for row in jobs
        if str(row.get("status") or "").strip().lower() == "completed"
        and _contains(row.get("executor"), "openclaw")
        and (
            _contains(row.get("title"), "smoke")
            or _contains(row.get("result"), "openclaw_launch_ok")
            or _contains(row.get("result"), "service_smoke_ok")
        )
    ]
    stale_launch_blockers = [
        row
        for row in runs + jobs
        if _contains(row.get("blocker"), "capture_output")
    ]
    if openclaw_smokes:
        latest_smoke = max(int(row.get("updated_at_unix") or 0) for row in openclaw_smokes)
        stale_after_smoke = [
            row
            for row in stale_launch_blockers
            if int(row.get("updated_at_unix") or 0) <= latest_smoke
        ]
        if stale_after_smoke:
            signals.append(
                "OpenClaw 启动冒烟已成功，旧的 capture_output/Popen 启动故障应视为历史阻塞；"
                f"当前仍有 {len(stale_after_smoke)} 条旧记录需要改为重试/回填，而不是继续把启动故障当成当前根因。"
            )
    feishu_delivery_auth_failures = [
        row
        for row in jobs
        if _contains(row.get("delivery_error"), "app_id or app_secret not found")
    ]
    if feishu_delivery_auth_failures:
        signals.append(
            f"飞书投递凭证仍未就绪（app_id/app_secret 缺失）并影响 {len(feishu_delivery_auth_failures)} 个后台任务；"
            "当前更值得优先修复的是投递鉴权，而不是再次排查 OpenClaw 启动。"
        )
    origin_delivery_failures = [
        row
        for row in jobs
        if _contains(row.get("delivery_error"), "no delivery target resolved for deliver=origin")
    ]
    if origin_delivery_failures:
        signals.append(
            f"仍有 {len(origin_delivery_failures)} 个任务/定时作业使用 deliver=origin 但缺少可解析目标；"
            "需要把统一任务板中的会话/投递目标映射补齐。"
        )
    return signals


def _status_counts(rows: list[dict[str, Any]], key: str = "status") -> dict[str, int]:
    counter = Counter(str(row.get(key) or "unknown").strip() or "unknown" for row in rows)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _top_nonempty_counts(values: list[str], *, limit: int = 5) -> dict[str, int]:
    counter = Counter(str(value or "").strip() for value in values if str(value or "").strip())
    return dict(counter.most_common(max(1, limit)))


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


def _job_tag_value(row: dict[str, Any], prefix: str) -> str:
    expected = f"{str(prefix or '').strip().lower()}:"
    if not expected or expected == ":":
        return ""
    for tag in row.get("tags") or []:
        raw_tag = str(tag or "").strip()
        if raw_tag.lower().startswith(expected):
            return raw_tag.split(":", 1)[1].strip()
    return ""


def _job_capability_run_id(row: dict[str, Any]) -> str:
    return _job_tag_value(row, "capability_run")


def _job_unit(row: dict[str, Any]) -> dict[str, Any]:
    job_id = str(row.get("job_id") or "").strip()
    task_scope_key = _job_tag_value(row, "task_scope")
    person_memory_key = _job_tag_value(row, "person_memory")
    if task_scope_key.startswith("dingtalk:chat:"):
        conversation_role = "task_group"
    elif task_scope_key.startswith("task:"):
        conversation_role = "task_unit"
    elif task_scope_key:
        conversation_role = "chat_surface"
    else:
        conversation_role = "system"
    return {
        "unit_id": f"job:{job_id}",
        "unit_type": "background_job",
        "title": str(row.get("title") or "").strip(),
        "status": str(row.get("status") or "unknown").strip().lower(),
        "owner": str(row.get("session_id") or row.get("user_id") or "").strip(),
        "origin_platform": task_scope_key.split(":", 1)[0] if ":" in task_scope_key else "",
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
        "task_scope_key": task_scope_key,
        "person_memory_key": person_memory_key,
        "conversation_role": conversation_role,
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
    scoped_units = [unit for unit in units if unit.get("task_scope_key") or unit.get("person_memory_key")]

    return {
        "generated_at_unix": int(time.time()),
        "counts": {
            "capability_runs": _status_counts(runs),
            "background_jobs": _status_counts(jobs),
            "delegation_tasks": _status_counts(subagents),
        },
        "scope_summary": {
            "task_scopes": _top_nonempty_counts([str(unit.get("task_scope_key") or "") for unit in scoped_units], limit=limit),
            "person_memories": _top_nonempty_counts([str(unit.get("person_memory_key") or "") for unit in scoped_units], limit=limit),
            "conversation_roles": _top_nonempty_counts([str(unit.get("conversation_role") or "") for unit in scoped_units], limit=limit),
            "origin_platforms": _top_nonempty_counts([str(unit.get("origin_platform") or "") for unit in scoped_units], limit=limit),
        },
        "derived_signals": _derive_system_signals(runs=runs, jobs=jobs),
        "capability_runs": runs,
        "background_jobs": jobs,
        "delegation_tasks": subagents,
        "units": units,
        "top_units": units[: max(1, limit)],
    }
