from types import SimpleNamespace

from gateway.platforms.adapter_context import build_adapter_context_for_event


def test_build_adapter_context_for_event_exposes_normalized_fields():
    event = SimpleNamespace(
        text="看同步",
        source=SimpleNamespace(
            platform=SimpleNamespace(value="Feishu"),
            chat_id="oc_chat_1",
            thread_id="",
            chat_type="group",
            chat_name="任务群",
            user_id="ou_1",
            user_name="Alice",
        ),
    )

    context = build_adapter_context_for_event(event, session_key="session-1")

    assert context.platform_name == "feishu"
    assert context.chat_id == "oc_chat_1"
    assert context.text == "看同步"
    assert context.user_name == "Alice"
    assert context.is_private_chat() is False
