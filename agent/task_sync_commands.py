"""Shared task-sync command parsing and payload helpers."""

from __future__ import annotations

from typing import Any, Callable

TASK_SYNC_LINK_COMMANDS: tuple[str, ...] = (
    "任务频道",
    "关联频道",
    "任务关联频道",
    "看同步",
    "task links",
)

TASK_SYNC_BROADCAST_PREFIXES: tuple[str, ...] = (
    "任务广播",
    "同步到任务频道",
    "同步播报",
    "发同步",
    "tasksync",
)


def build_tasksync_usage(command_name: str = "/tasksync") -> str:
    return (
        "用法：\n"
        f"{command_name} links [task-id]\n"
        f"{command_name} broadcast <消息>\n"
        f"{command_name} broadcast <task-id> <消息>"
    )


def parse_task_sync_control_text(
    text: str,
    *,
    normalize: Callable[[str], str],
    extract_prefixed_text: Callable[[str, tuple[str, ...]], str],
    link_commands: tuple[str, ...] = TASK_SYNC_LINK_COMMANDS,
    broadcast_prefixes: tuple[str, ...] = TASK_SYNC_BROADCAST_PREFIXES,
) -> dict[str, str] | None:
    normalized = normalize(text)
    if not normalized:
        return None
    normalized_link_commands = {normalize(item) for item in link_commands}
    if normalized in normalized_link_commands:
        return {"action": "links"}
    message = extract_prefixed_text(text, broadcast_prefixes)
    if message:
        return {"action": "broadcast", "message": message}
    return None


def build_channel_sync_payload(
    *,
    action: str,
    platform: str,
    chat_id: str,
    thread_id: str = "",
    chat_type: str = "",
    task_id: str = "",
    message: str = "",
    include_source: bool = False,
    limit: int = 20,
) -> dict[str, Any]:
    payload: dict[str, Any] = {
        "action": action,
        "platform": platform,
        "chat_id": chat_id,
        "thread_id": thread_id,
        "chat_type": chat_type,
        "limit": limit,
    }
    if task_id:
        payload["task_id"] = task_id
    if message:
        payload["message"] = message
    if action in {"broadcast", "broadcast-current"}:
        payload["source_platform"] = platform
        payload["source_chat_id"] = chat_id
        payload["source_thread_id"] = thread_id
        payload["include_source"] = include_source
    return payload
