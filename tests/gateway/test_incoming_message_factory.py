from types import SimpleNamespace

from gateway.platforms.incoming_message_factory import build_incoming_message_for_event


def test_build_incoming_message_for_event_prefers_source_platform_value():
    event = SimpleNamespace(
        text="看同步",
        source=SimpleNamespace(
            platform=SimpleNamespace(value="WeiXin"),
            chat_id="chat-1",
            thread_id="thread-1",
            chat_type="dm",
            chat_name="微信私聊",
            user_id="user-1",
            user_name="tester",
        ),
    )

    message = build_incoming_message_for_event(event, session_key="session-1")

    assert message.platform == "weixin"
    assert message.chat_id == "chat-1"
    assert message.session_key == "session-1"


def test_build_incoming_message_for_event_allows_explicit_platform_override():
    event = SimpleNamespace(
        text="发同步 更新",
        source=SimpleNamespace(chat_id="chat-2", thread_id="", chat_type="group", user_id="user-2", user_name="tester"),
    )

    message = build_incoming_message_for_event(event, session_key="session-2", platform_name="feishu")

    assert message.platform == "feishu"
    assert message.text == "发同步 更新"
