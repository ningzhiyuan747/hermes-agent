from datetime import datetime
from types import SimpleNamespace
from unittest.mock import AsyncMock, MagicMock

import pytest

import gateway.run as gateway_run
from gateway.config import GatewayConfig, Platform, PlatformConfig
from gateway.platforms.base import MessageEvent
from gateway.session import SessionEntry, SessionSource, build_session_key


def _make_source() -> SessionSource:
    return SessionSource(
        platform=Platform.WEIXIN,
        user_id="u1",
        chat_id="c1",
        user_name="tester",
        chat_type="dm",
    )


def _make_event(text: str) -> MessageEvent:
    return MessageEvent(
        text=text,
        source=_make_source(),
        message_id="m1",
    )


def _make_runner() -> gateway_run.GatewayRunner:
    runner = object.__new__(gateway_run.GatewayRunner)
    runner.config = GatewayConfig(
        platforms={Platform.WEIXIN: PlatformConfig(enabled=True, token="***")}
    )
    runner.adapters = {Platform.WEIXIN: MagicMock()}
    runner.hooks = SimpleNamespace(emit=AsyncMock(), loaded_hooks=False)
    runner.session_store = MagicMock()
    runner.session_store.get_or_create_session.return_value = SessionEntry(
        session_key=build_session_key(_make_source()),
        session_id="sess-1",
        created_at=datetime.now(),
        updated_at=datetime.now(),
        platform=Platform.WEIXIN,
        chat_type="dm",
    )
    return runner


@pytest.mark.asyncio
async def test_tasksync_command_without_args_lists_current_links(monkeypatch):
    runner = _make_runner()
    dispatcher = MagicMock(return_value="当前绑定")
    monkeypatch.setattr(
        "agent.business_command_service.dispatch_tasksync_command",
        dispatcher,
    )

    result = await runner._handle_tasksync_command(_make_event("/tasksync"))

    assert result == "当前绑定"
    incoming = dispatcher.call_args.args[0]
    assert incoming.platform == "weixin"
    assert incoming.chat_id == "c1"
    assert dispatcher.call_args.args[1] == ""


@pytest.mark.asyncio
async def test_tasksync_command_with_raw_message_broadcasts_current_task(monkeypatch):
    runner = _make_runner()
    dispatcher = MagicMock(return_value="已广播")
    monkeypatch.setattr(
        "agent.business_command_service.dispatch_tasksync_command",
        dispatcher,
    )

    result = await runner._handle_tasksync_command(_make_event("/tasksync 任务更新"))

    assert result == "已广播"
    incoming = dispatcher.call_args.args[0]
    assert incoming.platform == "weixin"
    assert dispatcher.call_args.args[1] == "任务更新"
