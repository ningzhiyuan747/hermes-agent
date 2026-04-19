from agent.task_sync_commands import (
    build_channel_sync_payload,
    build_tasksync_usage,
    parse_task_sync_control_text,
)


def _normalize(text: str) -> str:
    return "".join(str(text or "").strip().lower().split())


def _extract_prefixed_text(text: str, prefixes: tuple[str, ...]) -> str:
    raw = str(text or "").strip()
    lowered = raw.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix.lower()):
            return raw[len(prefix):].strip(" ：:")
    return ""


def test_parse_task_sync_control_text_supports_links_and_broadcast_shortcuts():
    assert parse_task_sync_control_text(
        "看同步",
        normalize=_normalize,
        extract_prefixed_text=_extract_prefixed_text,
    ) == {"action": "links"}
    assert parse_task_sync_control_text(
        "发同步 任务更新",
        normalize=_normalize,
        extract_prefixed_text=_extract_prefixed_text,
    ) == {"action": "broadcast", "message": "任务更新"}


def test_build_channel_sync_payload_sets_source_for_broadcast_only():
    payload = build_channel_sync_payload(
        action="broadcast-current",
        platform="feishu",
        chat_id="oc_1",
        thread_id="",
        chat_type="group",
        message="hello",
        include_source=False,
    )

    assert payload["action"] == "broadcast-current"
    assert payload["platform"] == "feishu"
    assert payload["source_platform"] == "feishu"
    assert payload["source_chat_id"] == "oc_1"
    assert payload["message"] == "hello"

    links_payload = build_channel_sync_payload(
        action="current-links",
        platform="feishu",
        chat_id="oc_1",
    )
    assert "source_platform" not in links_payload
    assert "message" not in links_payload


def test_build_tasksync_usage_mentions_both_short_forms():
    usage = build_tasksync_usage("/tsync")
    assert "/tsync links [task-id]" in usage
    assert "/tsync broadcast <消息>" in usage
