import importlib.util
import sys
import types
from pathlib import Path
from types import SimpleNamespace
from unittest.mock import Mock

import pytest


BRIDGE_PATH = Path("/mnt/f/hermes-dingtalk-bridge/dingtalk_stream_hermes_bridge.py")


@pytest.fixture()
def dingtalk_bridge_module(monkeypatch):
    fake_stream = types.ModuleType("dingtalk_stream")

    class _AckMessage:
        STATUS_OK = "ok"

    class _ChatbotHandler:
        pass

    fake_stream.AckMessage = _AckMessage
    fake_stream.ChatbotHandler = _ChatbotHandler
    fake_stream.Credential = object
    fake_stream.DingTalkStreamClient = object

    fake_chatbot = types.ModuleType("dingtalk_stream.chatbot")

    class _ChatbotMessage:
        @classmethod
        def from_dict(cls, payload):
            return payload

    fake_chatbot.ChatbotMessage = _ChatbotMessage

    monkeypatch.setitem(sys.modules, "dingtalk_stream", fake_stream)
    monkeypatch.setitem(sys.modules, "dingtalk_stream.chatbot", fake_chatbot)
    monkeypatch.syspath_prepend(str(BRIDGE_PATH.parent))

    module_name = "test_dingtalk_stream_hermes_bridge"
    spec = importlib.util.spec_from_file_location(module_name, BRIDGE_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[module_name] = module
    spec.loader.exec_module(module)
    try:
        yield module
    finally:
        sys.modules.pop(module_name, None)


def _make_incoming(*, conversation_type: str = "2") -> SimpleNamespace:
    return SimpleNamespace(
        conversation_id="cid-task-group",
        conversation_title="任务群",
        conversation_type=conversation_type,
        sender_staff_id="staff-owner",
        sender_id="sender-owner",
        sender_nick="宁致远",
    )


def _make_handler(bridge_module):
    handler = object.__new__(bridge_module.HermesDingTalkHandler)
    handler.reply_text = Mock()
    return handler


@pytest.mark.asyncio
async def test_handle_session_status_command_replies_with_shared_status(dingtalk_bridge_module, monkeypatch):
    handler = _make_handler(dingtalk_bridge_module)
    incoming = _make_incoming()
    build_status = Mock(return_value="任务状态面板")
    monkeypatch.setattr(dingtalk_bridge_module, "build_status_text", build_status)
    monkeypatch.setattr(dingtalk_bridge_module, "is_owner_message", lambda _incoming: False)

    result = await handler._handle_session_control_command(incoming, "状态", "session-1")

    assert result == "status"
    build_status.assert_called_once_with("session-1", global_fallback=False, incoming=incoming)
    handler.reply_text.assert_called_once_with("任务状态面板", incoming)


@pytest.mark.asyncio
async def test_handle_admin_task_bind_in_private_chat_is_blocked(dingtalk_bridge_module, monkeypatch):
    handler = _make_handler(dingtalk_bridge_module)
    incoming = _make_incoming(conversation_type="1")
    run_task_command = Mock()
    monkeypatch.setattr(dingtalk_bridge_module, "is_owner_message", lambda _incoming: True)
    monkeypatch.setattr(
        dingtalk_bridge_module,
        "parse_business_task_command",
        lambda _question: {"action": "bind", "task_id": "task-1"},
    )
    monkeypatch.setattr(dingtalk_bridge_module, "run_business_task_command", run_task_command)

    result = await handler._handle_admin_control_command(incoming, "绑定任务 task-1", "session-1")

    assert result == "task-bind-private"
    run_task_command.assert_not_called()
    handler.reply_text.assert_called_once()
    assert "私聊不需要绑定任务群" in handler.reply_text.call_args.args[0]


@pytest.mark.asyncio
async def test_handle_admin_bridge_identity_requires_owner(dingtalk_bridge_module, monkeypatch):
    handler = _make_handler(dingtalk_bridge_module)
    incoming = _make_incoming()
    monkeypatch.setattr(dingtalk_bridge_module, "is_owner_message", lambda _incoming: False)

    result = await handler._handle_admin_control_command(incoming, "桥身份", "session-1")

    assert result == "unauthorized_control"
    handler.reply_text.assert_called_once_with(
        dingtalk_bridge_module.unauthorized_control_message(),
        incoming,
    )


def test_parse_business_task_command_supports_sync_shortcuts(dingtalk_bridge_module):
    assert dingtalk_bridge_module.parse_business_task_command("看同步") == {"action": "links"}
    assert dingtalk_bridge_module.parse_business_task_command("发同步 任务更新") == {
        "action": "broadcast",
        "message": "任务更新",
    }


@pytest.mark.asyncio
async def test_handle_admin_task_links_routes_to_channel_command(dingtalk_bridge_module, monkeypatch):
    handler = _make_handler(dingtalk_bridge_module)
    incoming = _make_incoming()
    run_dispatch_command = Mock(return_value="同步列表")
    run_channel_command = Mock()
    run_task_command = Mock()
    monkeypatch.setattr(dingtalk_bridge_module, "is_owner_message", lambda _incoming: False)
    monkeypatch.setattr(
        dingtalk_bridge_module,
        "parse_business_task_command",
        lambda _question: {"action": "links"},
    )
    monkeypatch.setattr(dingtalk_bridge_module, "run_business_dispatch_command", run_dispatch_command)
    monkeypatch.setattr(dingtalk_bridge_module, "run_business_channel_command", run_channel_command)
    monkeypatch.setattr(dingtalk_bridge_module, "run_business_task_command", run_task_command)

    result = await handler._handle_admin_control_command(incoming, "看同步", "session-1")

    assert result == "task-links"
    run_dispatch_command.assert_called_once_with(
        incoming,
        "看同步",
        "session-1",
        can_manage_bindings=False,
    )
    run_channel_command.assert_not_called()
    run_task_command.assert_not_called()
    handler.reply_text.assert_called_once_with("同步列表", incoming)
