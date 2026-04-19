from types import SimpleNamespace

from agent.incoming_message import IncomingMessage


def test_from_event_normalizes_message_fields():
    event = SimpleNamespace(
        text="发同步 任务更新",
        source=SimpleNamespace(
            platform=SimpleNamespace(value="WeiXin"),
            chat_id="chat-1",
            thread_id="thread-1",
            chat_type="dm",
            chat_name="Hermes 私聊",
            user_id="user-1",
            user_name="Tester",
        ),
    )

    message = IncomingMessage.from_event(event, session_key="session-1")

    assert message.platform == "weixin"
    assert message.chat_id == "chat-1"
    assert message.thread_id == "thread-1"
    assert message.text == "发同步 任务更新"
    assert message.session_key == "session-1"


def test_from_source_supports_dingtalk_like_attributes():
    source = SimpleNamespace(
        conversation_title="任务群",
        sender_staff_id="staff-1",
        sender_nick="Alice",
    )

    message = IncomingMessage.from_source(
        source,
        platform="dingtalk",
        text="看同步",
        session_key="session-2",
    )

    assert message.platform == "dingtalk"
    assert message.chat_name == "任务群"
    assert message.user_id == "staff-1"
    assert message.user_name == "Alice"
