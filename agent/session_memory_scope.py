"""Runtime-only memory scope prompt for per-user / per-task isolation."""

from __future__ import annotations

import os
from typing import Optional

from agent.business_db import (
    get_channel_task,
    get_task_memory,
    get_user_distilled_profile,
    get_user_memory,
)


def _session_value(name: str) -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name) or "").strip()
    except Exception:
        return str(os.getenv(name, "") or "").strip()


def _bot_label_for_platform(platform: str) -> str:
    normalized = str(platform or "").strip().lower()
    if normalized == "dingtalk":
        return str(os.getenv("DINGTALK_BOT_NAME", "") or "").strip() or "当前钉钉机器人"
    if normalized == "feishu":
        return str(os.getenv("FEISHU_BOT_NAME", "") or "").strip() or "当前飞书机器人"
    if normalized:
        return f"当前{normalized}会话机器人"
    return "当前会话机器人"


def _summary(record: Optional[dict]) -> str:
    if not isinstance(record, dict):
        return ""
    return str(record.get("summary") or "").strip()


def _memory_value(record: Optional[dict], key: str) -> str:
    if not isinstance(record, dict):
        return ""
    payload = record.get("memory")
    if not isinstance(payload, dict):
        return ""
    nested_profile = payload.get("profile")
    if isinstance(nested_profile, dict):
        value = nested_profile.get(key)
        if value not in (None, ""):
            return str(value).strip()
    return str(payload.get(key) or "").strip()


def build_session_memory_scope_prompt() -> str:
    platform = _session_value("HERMES_SESSION_PLATFORM").lower()
    if not platform:
        return ""

    chat_id = _session_value("HERMES_SESSION_CHAT_ID")
    thread_id = _session_value("HERMES_SESSION_THREAD_ID")
    user_id = _session_value("HERMES_SESSION_USER_ID")
    chat_type = _session_value("HERMES_SESSION_CHAT_TYPE").lower()

    lines = [
        "## Session Memory Scope",
        f"- Current external identity in this conversation: {_bot_label_for_platform(platform)}.",
        "- Do not present internal bridge/runtime names, other bot names, or implementation details as your current identity unless the user explicitly asks for bridge status or technical routing.",
        "- Stable repo/workspace/tool facts belong to system memory, not to any person's private memory or a shared chat's task memory.",
    ]

    if chat_type in {"dm", "private", "p2p", "single", "singlechat", "1"}:
        user_distilled = get_user_distilled_profile(platform=platform, user_id=user_id) if user_id else None
        user_memory = get_user_memory(platform=platform, user_id=user_id, scope="profile") if user_id else None
        user_notes = get_user_memory(platform=platform, user_id=user_id, scope="notes") if user_id else None
        lines.extend(
            [
                "- This is a private user conversation. Use only this user's private memory for durable personal context.",
                "- Do not pull task-group memory, another user's memory, or another platform conversation's private memory into this reply.",
                f"- Distilled working profile summary: {_summary(user_distilled) or 'none recorded yet.'}",
                f"- Distilled core principles: {_memory_value(user_distilled, 'core_principles') or 'none recorded yet.'}",
                f"- Distilled working style: {_memory_value(user_distilled, 'working_style') or 'none recorded yet.'}",
                f"- Distilled preferred output: {_memory_value(user_distilled, 'preferred_output') or 'none recorded yet.'}",
                f"- Distilled domain focus: {_memory_value(user_distilled, 'domain_focus') or 'none recorded yet.'}",
                f"- Distilled decision heuristics: {_memory_value(user_distilled, 'decision_heuristics') or 'none recorded yet.'}",
                f"- Distilled approval sensitivity: {_memory_value(user_distilled, 'approval_sensitivity') or 'none recorded yet.'}",
                f"- Distilled anti-patterns: {_memory_value(user_distilled, 'anti_patterns') or 'none recorded yet.'}",
                f"- Distilled stable instructions: {_memory_value(user_distilled, 'stable_instructions') or 'none recorded yet.'}",
                f"- Private user profile summary: {_summary(user_memory) or 'none recorded yet.'}",
                f"- Private user notes summary: {_summary(user_notes) or 'none recorded yet.'}",
            ]
        )
        return "\n".join(lines)

    channel_task = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id) if chat_id else None
    task = (channel_task or {}).get("task") if isinstance(channel_task, dict) else None
    if isinstance(task, dict) and str(task.get("task_id") or "").strip():
        task_id = str(task.get("task_id") or "").strip()
        task_title = str(task.get("title") or "").strip()
        task_memory = get_task_memory(task_id=task_id, scope="shared")
        lines.extend(
            [
                f"- This conversation is bound to task {task_id}" + (f" ({task_title})" if task_title else "") + ".",
                "- Use task memory as the shared durable context for this conversation.",
                "- There is no separate durable channel memory layer here; shared chat context must stay task-scoped.",
                "- Do not expose or rely on any participant's private DM memory here unless the user restates it in the current conversation.",
                f"- Task memory summary: {_summary(task_memory) or 'none recorded yet.'}",
            ]
        )
        return "\n".join(lines)

    lines.extend(
        [
            "- This is a shared chat without a bound task.",
            "- There is no durable shared channel memory here until a task is bound.",
            "- Do not use any participant's private memory as shared context here.",
            "- Prefer only the current visible conversation and explicit task bindings.",
        ]
    )
    return "\n".join(lines)
