from agent.business_command_service import (
    dispatch_business_text_command,
    dispatch_tasksync_command,
    parse_business_profile_text,
)
from agent.incoming_message import IncomingMessage


def _message(text: str, *, chat_type: str = "group") -> IncomingMessage:
    return IncomingMessage(
        platform="feishu",
        chat_id="oc_chat_1",
        thread_id="",
        chat_type=chat_type,
        user_id="ou_owner",
        user_name="tester",
        text=text,
        session_key="session-1",
    )


def test_dispatch_business_text_routes_task_sync(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_channel_command",
        lambda message, **kwargs: f"{kwargs['action']}:{kwargs.get('text', '')}:{message.platform}",
    )

    result = dispatch_business_text_command(_message("发同步 已处理"), can_manage_bindings=True)

    assert result == "broadcast-current:已处理:feishu"


def test_dispatch_business_text_blocks_bind_without_permission():
    result = dispatch_business_text_command(_message("挂任务 task-123"), can_manage_bindings=False)
    assert result == "绑定任务只允许 Owner/Admin 使用。"


def test_dispatch_tasksync_command_links_uses_current_channel(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_channel_command",
        lambda _message, **kwargs: kwargs["action"],
    )

    result = dispatch_tasksync_command(_message("/tasksync"), "")

    assert result == "current-links"


def test_dispatch_tasksync_command_broadcast_specific_task(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_channel_command",
        lambda _message, **kwargs: f"{kwargs['action']}:{kwargs.get('task_id')}:{kwargs.get('text')}",
    )

    result = dispatch_tasksync_command(_message("/tasksync broadcast task-9 更新"), "broadcast task-9 更新")

    assert result == "broadcast:task-9:更新"


def test_dispatch_business_text_routes_bind_to_task_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_task_command",
        lambda _message, command, **_kwargs: f"{command['action']}:{command['task_id']}",
    )

    result = dispatch_business_text_command(_message("挂任务 task-123"), can_manage_bindings=True)

    assert result == "bind:task-123"


def test_dispatch_business_text_routes_profile_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_distilled_profile_command",
        lambda _message, command, **kwargs: f"{command['action']}:{kwargs['can_manage_profiles']}",
    )

    result = dispatch_business_text_command(_message("我的画像"), can_manage_bindings=False)

    assert result == "show:False"


def test_dispatch_business_text_routes_profile_evidence_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_distilled_profile_command",
        lambda _message, command, **_kwargs: f"{command['action']}:{command.get('field', '')}",
    )

    result = dispatch_business_text_command(_message("画像证据 输出偏好"), can_manage_bindings=True)

    assert result == "evidence:输出偏好"


def test_dispatch_business_text_routes_profile_history_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_distilled_profile_command",
        lambda _message, command, **_kwargs: f"{command['action']}:{command.get('field', '')}",
    )

    result = dispatch_business_text_command(_message("画像历史 输出偏好"), can_manage_bindings=True)

    assert result == "history:输出偏好"


def test_dispatch_business_text_routes_profile_draft_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_distilled_profile_command",
        lambda _message, command, **_kwargs: command["action"],
    )

    result = dispatch_business_text_command(_message("画像草稿"), can_manage_bindings=True)

    assert result == "draft"


def test_dispatch_business_text_routes_profile_diff_command(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_service.execute_distilled_profile_command",
        lambda _message, command, **_kwargs: f"{command['action']}:{command.get('field', '')}",
    )

    result = dispatch_business_text_command(_message("画像差异 输出偏好"), can_manage_bindings=True)

    assert result == "diff:输出偏好"


def test_parse_business_profile_text_prefers_target_user_over_field():
    assert parse_business_profile_text("画像历史 ou_other") == {
        "action": "history",
        "user_id": "ou_other",
        "field": "",
    }
    assert parse_business_profile_text("画像证据 ou_other") == {
        "action": "evidence",
        "user_id": "ou_other",
        "field": "",
    }
