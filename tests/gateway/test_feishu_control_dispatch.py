from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

import gateway.platforms.feishu as feishu_module
from gateway.platforms.feishu import FeishuAdapter


def _make_event(text: str, *, chat_type: str = "group") -> SimpleNamespace:
    return SimpleNamespace(
        text=text,
        source=SimpleNamespace(
            chat_type=chat_type,
            user_id="ou_owner",
            chat_id="oc_task_group",
            thread_id="",
        ),
    )


def _make_adapter() -> FeishuAdapter:
    adapter = object.__new__(FeishuAdapter)
    adapter._send_plain = AsyncMock()
    adapter._is_owner_or_admin = lambda _event: True
    adapter._status_service = None
    adapter._control_router = None
    return adapter


@pytest.mark.asyncio
async def test_handle_dingtalk_bridge_status_sends_status_text():
    adapter = _make_adapter()
    adapter._get_dingtalk_bridge_status = AsyncMock(return_value="桥状态正常")

    handled = await adapter._handle_dingtalk_bridge_control_text(_make_event("钉钉桥状态", chat_type="dm"))

    assert handled is True
    adapter._get_dingtalk_bridge_status.assert_awaited_once()
    adapter._send_plain.assert_awaited_once()
    assert adapter._send_plain.await_args.args[1] == "桥状态正常"


@pytest.mark.asyncio
async def test_handle_dingtalk_relay_test_text_intercepts_and_returns_bridge_status():
    adapter = _make_adapter()
    adapter._get_dingtalk_bridge_status = AsyncMock(return_value="桥状态正常")
    adapter._relay_text_to_dingtalk = AsyncMock()

    handled = await adapter._handle_dingtalk_bridge_control_text(
        _make_event("派给钉钉子智能体 测试一下钉钉桥", chat_type="dm")
    )

    assert handled is True
    adapter._relay_text_to_dingtalk.assert_not_awaited()
    adapter._send_plain.assert_awaited_once()
    sent_text = adapter._send_plain.await_args.args[1]
    assert "已拦截这次桥测试任务" in sent_text
    assert "桥状态正常" in sent_text


@pytest.mark.asyncio
async def test_handle_status_control_text_prefers_task_panel_for_group():
    adapter = _make_adapter()
    adapter._get_event_task_panel_snapshot = Mock(return_value={"task": {"task_id": "task-1"}})
    adapter._format_task_panel_snapshot = Mock(return_value="任务面板")
    adapter._get_background_job_snapshot = Mock(side_effect=AssertionError("should not query background job"))
    adapter._get_capability_run_snapshot = Mock(side_effect=AssertionError("should not query capability run"))

    handled = await adapter._handle_status_control_text(_make_event("状态"), "状态", "session-1")

    assert handled is True
    adapter._get_event_task_panel_snapshot.assert_called_once()
    adapter._format_task_panel_snapshot.assert_called_once()
    adapter._send_plain.assert_awaited_once()
    assert adapter._send_plain.await_args.args[1] == "任务面板"


@pytest.mark.asyncio
async def test_handle_status_control_text_falls_back_to_background_job_then_run():
    adapter = _make_adapter()
    adapter._get_event_task_panel_snapshot = Mock(return_value=None)
    adapter._get_background_job_snapshot = Mock(return_value={"job_id": "job-1"})
    adapter._get_capability_run_snapshot = Mock()
    adapter._format_activity_snapshot = Mock(return_value="后台任务状态")

    handled = await adapter._handle_status_control_text(_make_event("状态"), "状态", "session-1")

    assert handled is True
    adapter._get_background_job_snapshot.assert_called_once()
    adapter._get_capability_run_snapshot.assert_not_called()
    adapter._format_activity_snapshot.assert_called_once()
    adapter._send_plain.assert_awaited_once()
    assert adapter._send_plain.await_args.args[1] == "后台任务状态"


@pytest.mark.asyncio
async def test_handle_task_control_text_links_uses_channel_sync(monkeypatch):
    adapter = _make_adapter()
    dispatcher = Mock(return_value="已绑定频道")
    monkeypatch.setattr(feishu_module, "dispatch_business_text_command", dispatcher)

    handled = await adapter._handle_task_control_text(_make_event("看同步"), "看同步", "session-1")

    assert handled is True
    dispatcher.assert_called_once()
    incoming = dispatcher.call_args.args[0]
    assert incoming.platform == "feishu"
    assert incoming.text == "看同步"
    adapter._send_plain.assert_awaited_once()
    assert adapter._send_plain.await_args.args[1] == "已绑定频道"


@pytest.mark.asyncio
async def test_handle_task_control_text_broadcast_uses_channel_sync(monkeypatch):
    adapter = _make_adapter()
    dispatcher = Mock(return_value="广播完成")
    monkeypatch.setattr(feishu_module, "dispatch_business_text_command", dispatcher)

    handled = await adapter._handle_task_control_text(_make_event("发同步 已处理"), "发同步已处理", "session-1")

    assert handled is True
    dispatcher.assert_called_once()
    incoming = dispatcher.call_args.args[0]
    assert incoming.platform == "feishu"
    assert incoming.text == "发同步 已处理"
    adapter._send_plain.assert_awaited_once()
    assert adapter._send_plain.await_args.args[1] == "广播完成"


@pytest.mark.asyncio
async def test_sync_business_identity_marks_owner_as_approval_bypass(monkeypatch):
    adapter = object.__new__(FeishuAdapter)
    adapter._admins = {"ou_owner"}

    captured = {}
    monkeypatch.setattr(feishu_module, "get_user", lambda **kwargs: {})
    monkeypatch.setattr(feishu_module, "get_channel", lambda **kwargs: {})
    monkeypatch.setattr(feishu_module, "upsert_channel", lambda **kwargs: kwargs)
    monkeypatch.setattr(feishu_module, "upsert_user", lambda **kwargs: captured.update(kwargs))

    event = SimpleNamespace(
        source=SimpleNamespace(
            user_id="ou_owner",
            user_id_alt="",
            user_name="Owner",
            chat_id="oc_demo",
            thread_id="",
            chat_name="Owner DM",
            chat_type="dm",
        )
    )

    await FeishuAdapter._sync_business_identity(adapter, event)

    assert captured["role"] == "owner"
    assert captured["permissions"]["bypass_approval"] is True
    assert captured["permissions"]["global_owner"] is True
