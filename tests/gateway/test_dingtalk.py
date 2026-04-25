"""Tests for DingTalk platform adapter."""
import asyncio
import json
import os
from datetime import datetime, timezone
from unittest.mock import AsyncMock, MagicMock, patch, PropertyMock

import pytest

from gateway.config import GatewayConfig, HomeChannel, Platform, PlatformConfig, _apply_env_overrides


# ---------------------------------------------------------------------------
# Requirements check
# ---------------------------------------------------------------------------


class TestDingTalkRequirements:

    def test_returns_false_when_sdk_missing(self, monkeypatch):
        with patch.dict("sys.modules", {"dingtalk_stream": None}):
            monkeypatch.setattr(
                "gateway.platforms.dingtalk.DINGTALK_STREAM_AVAILABLE", False
            )
            from gateway.platforms.dingtalk import check_dingtalk_requirements
            assert check_dingtalk_requirements() is False

    def test_returns_false_when_env_vars_missing(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.dingtalk.DINGTALK_STREAM_AVAILABLE", True
        )
        monkeypatch.setattr("gateway.platforms.dingtalk.HTTPX_AVAILABLE", True)
        monkeypatch.delenv("DINGTALK_CLIENT_ID", raising=False)
        monkeypatch.delenv("DINGTALK_CLIENT_SECRET", raising=False)
        from gateway.platforms.dingtalk import check_dingtalk_requirements
        assert check_dingtalk_requirements() is False

    def test_returns_true_when_all_available(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.dingtalk.DINGTALK_STREAM_AVAILABLE", True
        )
        monkeypatch.setattr("gateway.platforms.dingtalk.HTTPX_AVAILABLE", True)
        monkeypatch.setenv("DINGTALK_CLIENT_ID", "test-id")
        monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "test-secret")
        from gateway.platforms.dingtalk import check_dingtalk_requirements
        assert check_dingtalk_requirements() is True


# ---------------------------------------------------------------------------
# Adapter construction
# ---------------------------------------------------------------------------


class TestDingTalkAdapterInit:

    def test_reads_config_from_extra(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        config = PlatformConfig(
            enabled=True,
            extra={"client_id": "cfg-id", "client_secret": "cfg-secret"},
        )
        adapter = DingTalkAdapter(config)
        assert adapter._client_id == "cfg-id"
        assert adapter._client_secret == "cfg-secret"
        assert adapter.name == "Dingtalk"  # base class uses .title()

    def test_falls_back_to_env_vars(self, monkeypatch):
        monkeypatch.setenv("DINGTALK_CLIENT_ID", "env-id")
        monkeypatch.setenv("DINGTALK_CLIENT_SECRET", "env-secret")
        from gateway.platforms.dingtalk import DingTalkAdapter
        config = PlatformConfig(enabled=True)
        adapter = DingTalkAdapter(config)
        assert adapter._client_id == "env-id"
        assert adapter._client_secret == "env-secret"

    def test_reads_legacy_owner_ids_from_env(self, monkeypatch):
        monkeypatch.setenv("DINGTALK_OWNER_IDS", "staff-legacy")
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        assert adapter._owner_user_ids == {"staff-legacy"}


# ---------------------------------------------------------------------------
# Message text extraction
# ---------------------------------------------------------------------------


class TestExtractText:

    def test_extracts_dict_text(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        msg = MagicMock()
        msg.text = {"content": "  hello world  "}
        msg.rich_text = None
        assert DingTalkAdapter._extract_text(msg) == "hello world"

    def test_extracts_string_text(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        msg = MagicMock()
        msg.text = "plain text"
        msg.rich_text = None
        assert DingTalkAdapter._extract_text(msg) == "plain text"

    def test_extracts_sdk_text_content_object(self):
        from gateway.platforms.dingtalk import DingTalkAdapter

        class _TextContent:
            content = "native probe"

        msg = MagicMock()
        msg.text = _TextContent()
        msg.rich_text = None
        assert DingTalkAdapter._extract_text(msg) == "native probe"

    def test_falls_back_to_rich_text(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        msg = MagicMock()
        msg.text = ""
        msg.rich_text = [{"text": "part1"}, {"text": "part2"}, {"image": "url"}]
        assert DingTalkAdapter._extract_text(msg) == "part1 part2"

    def test_returns_empty_for_no_content(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        msg = MagicMock()
        msg.text = ""
        msg.rich_text = None
        assert DingTalkAdapter._extract_text(msg) == ""


# ---------------------------------------------------------------------------
# Deduplication
# ---------------------------------------------------------------------------


class TestDeduplication:

    def test_first_message_not_duplicate(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        assert adapter._dedup.is_duplicate("msg-1") is False

    def test_second_same_message_is_duplicate(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter._dedup.is_duplicate("msg-1")
        assert adapter._dedup.is_duplicate("msg-1") is True

    def test_different_messages_not_duplicate(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter._dedup.is_duplicate("msg-1")
        assert adapter._dedup.is_duplicate("msg-2") is False

    def test_cache_cleanup_on_overflow(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        max_size = adapter._dedup._max_size
        # Fill beyond max
        for i in range(max_size + 10):
            adapter._dedup.is_duplicate(f"msg-{i}")
        # Cache should have been pruned
        assert len(adapter._dedup._seen) <= max_size + 10


# ---------------------------------------------------------------------------
# Send
# ---------------------------------------------------------------------------


class TestSend:

    @pytest.mark.asyncio
    async def test_send_posts_to_webhook(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_response.text = "OK"

        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_client

        result = await adapter.send(
            "chat-123", "Hello!",
            metadata={"session_webhook": "https://dingtalk.example/webhook"}
        )
        assert result.success is True
        mock_client.post.assert_called_once()
        call_args = mock_client.post.call_args
        assert call_args[0][0] == "https://dingtalk.example/webhook"
        payload = call_args[1]["json"]
        assert payload["msgtype"] == "markdown"
        assert payload["markdown"]["title"] == "Hermes"
        assert payload["markdown"]["text"] == "Hello!"

    @pytest.mark.asyncio
    async def test_send_fails_without_webhook(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter._http_client = AsyncMock()

        result = await adapter.send("chat-123", "Hello!")
        assert result.success is False
        assert "session_webhook" in result.error

    @pytest.mark.asyncio
    async def test_send_uses_cached_webhook(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))

        mock_response = MagicMock()
        mock_response.status_code = 200
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_client
        adapter._session_webhooks["chat-123"] = "https://cached.example/webhook"

        result = await adapter.send("chat-123", "Hello!")
        assert result.success is True
        assert mock_client.post.call_args[0][0] == "https://cached.example/webhook"

    @pytest.mark.asyncio
    async def test_send_handles_http_error(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))

        mock_response = MagicMock()
        mock_response.status_code = 400
        mock_response.text = "Bad Request"
        mock_client = AsyncMock()
        mock_client.post = AsyncMock(return_value=mock_response)
        adapter._http_client = mock_client

        result = await adapter.send(
            "chat-123", "Hello!",
            metadata={"session_webhook": "https://example/webhook"}
        )
        assert result.success is False
        assert "400" in result.error


# ---------------------------------------------------------------------------
# Connect / disconnect
# ---------------------------------------------------------------------------


class TestConnect:

    @pytest.mark.asyncio
    async def test_connect_fails_without_sdk(self, monkeypatch):
        monkeypatch.setattr(
            "gateway.platforms.dingtalk.DINGTALK_STREAM_AVAILABLE", False
        )
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        result = await adapter.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_connect_fails_without_credentials(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter._client_id = ""
        adapter._client_secret = ""
        result = await adapter.connect()
        assert result is False

    @pytest.mark.asyncio
    async def test_disconnect_cleans_up(self):
        from gateway.platforms.dingtalk import DingTalkAdapter
        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter._session_webhooks["a"] = "http://x"
        adapter._dedup._seen["b"] = 1.0
        adapter._http_client = AsyncMock()
        adapter._stream_task = None

        await adapter.disconnect()
        assert len(adapter._session_webhooks) == 0
        assert len(adapter._dedup._seen) == 0
        assert adapter._http_client is None


class TestStreamLifecycle:

    @pytest.mark.asyncio
    async def test_run_stream_awaits_async_start_result(self):
        from gateway.platforms.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        state = {"awaited": False}

        async def async_start():
            state["awaited"] = True
            adapter._running = False

        class FakeStreamClient:
            def start(self):
                return async_start()

        adapter._stream_client = FakeStreamClient()
        adapter._running = True

        await adapter._run_stream()

        assert state["awaited"] is True


class TestIncomingHandler:

    @pytest.mark.asyncio
    async def test_process_awaits_adapter_message_handler(self):
        from gateway.platforms.dingtalk import _IncomingHandler

        adapter = MagicMock()
        adapter._on_message = AsyncMock()
        loop = asyncio.get_running_loop()
        handler = _IncomingHandler(adapter, loop)
        message = MagicMock()

        result = await handler.process(message)

        adapter._on_message.assert_awaited_once_with(message)
        assert result == (200, "OK")

    @pytest.mark.asyncio
    async def test_process_normalizes_callback_message_payload(self):
        from gateway.platforms.dingtalk import _IncomingHandler

        adapter = MagicMock()
        adapter._on_message = AsyncMock()
        loop = asyncio.get_running_loop()
        handler = _IncomingHandler(adapter, loop)
        callback_message = MagicMock()
        callback_message.data = {
            "msgId": "msg-1",
            "msgtype": "text",
            "text": {"content": "native probe"},
            "conversationId": "cid-1",
            "conversationType": "1",
            "senderId": "sender-1",
            "senderNick": "Alice",
            "sessionWebhook": "https://api.dingtalk.com/v1.0/im/bot/messages/get",
        }

        result = await handler.process(callback_message)

        adapter._on_message.assert_awaited_once()
        normalized = adapter._on_message.await_args.args[0]
        assert normalized.message_id == "msg-1"
        assert normalized.text.content == "native probe"
        assert normalized.conversation_id == "cid-1"
        assert result == (200, "OK")


class TestNativeBusinessDispatch:

    @pytest.mark.asyncio
    async def test_handle_native_business_command_uses_shared_business_service(self, monkeypatch):
        from gateway.platforms.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        event = MagicMock()
        event.text = "任务频道"
        event.message_id = "msg-1"
        event.source = MagicMock(
            chat_id="cid-1",
            chat_name="商务群",
            chat_type="group",
            user_id="sender-1",
            user_name="Alice",
            user_id_alt="staff-1",
            thread_id=None,
        )
        monkeypatch.setattr("gateway.platforms.dingtalk.dispatch_business_text_command", lambda _msg, can_manage_bindings=True: f"shared:{can_manage_bindings}")
        monkeypatch.setattr("gateway.platforms.dingtalk.build_adapter_context_for_event", lambda *args, **kwargs: MagicMock(incoming_message="incoming"))
        monkeypatch.setattr("gateway.platforms.dingtalk.build_session_key", lambda *_args, **_kwargs: "agent:main:dingtalk:group:cid-1")
        monkeypatch.setattr(adapter, "_is_owner_or_admin", lambda _event: True)
        adapter._send_plain = AsyncMock(return_value=MagicMock(success=True))

        handled = await adapter._handle_native_business_command(event, session_webhook="https://api.dingtalk.com/webhook")

        assert handled is True
        adapter._send_plain.assert_awaited_once_with(
            event,
            "shared:True",
            session_webhook="https://api.dingtalk.com/webhook",
        )

    @pytest.mark.asyncio
    async def test_handle_native_business_command_falls_through_when_no_shared_response(self, monkeypatch):
        from gateway.platforms.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        event = MagicMock()
        event.text = "普通聊天"
        event.source = MagicMock(
            chat_id="cid-1",
            chat_name="商务群",
            chat_type="group",
            user_id="sender-1",
            user_name="Alice",
            user_id_alt="staff-1",
            thread_id=None,
        )
        monkeypatch.setattr("gateway.platforms.dingtalk.dispatch_business_text_command", lambda _msg, can_manage_bindings=True: None)
        monkeypatch.setattr("gateway.platforms.dingtalk.build_adapter_context_for_event", lambda *args, **kwargs: MagicMock(incoming_message="incoming"))
        monkeypatch.setattr("gateway.platforms.dingtalk.build_session_key", lambda *_args, **_kwargs: "agent:main:dingtalk:group:cid-1")
        adapter._send_plain = AsyncMock(return_value=MagicMock(success=True))

        handled = await adapter._handle_native_business_command(event, session_webhook="https://api.dingtalk.com/webhook")

        assert handled is False
        adapter._send_plain.assert_not_awaited()

    def test_sync_business_identity_persists_user_and_channel(self, monkeypatch):
        from gateway.platforms.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(PlatformConfig(enabled=True, extra={"owner_user_ids": ["staff-1"]}))
        event = MagicMock()
        event.source = MagicMock(
            chat_id="cid-1",
            chat_name="商务群",
            chat_type="dm",
            user_id="sender-1",
            user_name="Alice",
            user_id_alt="staff-1",
            thread_id="",
        )
        captured = {}
        monkeypatch.setattr("gateway.platforms.dingtalk.get_user", lambda **_kwargs: {})
        monkeypatch.setattr("gateway.platforms.dingtalk.get_channel", lambda **_kwargs: {})
        monkeypatch.setattr("gateway.platforms.dingtalk.upsert_user", lambda **kwargs: captured.setdefault("user", kwargs))
        monkeypatch.setattr("gateway.platforms.dingtalk.upsert_channel", lambda **kwargs: captured.setdefault("channel", kwargs))

        adapter._sync_business_identity(event)

        assert captured["user"]["platform"] == "dingtalk"
        assert captured["user"]["user_id"] == "staff-1"
        assert captured["user"]["role"] == "owner"
        assert captured["channel"]["platform"] == "dingtalk"
        assert captured["channel"]["chat_id"] == "cid-1"
        assert captured["channel"]["allow_free_chat"] is True

    @pytest.mark.asyncio
    async def test_on_message_caches_oapi_session_webhook(self):
        from gateway.platforms.dingtalk import DingTalkAdapter

        adapter = DingTalkAdapter(PlatformConfig(enabled=True))
        adapter.handle_message = AsyncMock()
        message = MagicMock()
        message.message_id = "msg-1"
        message.text = {"content": "hello"}
        message.rich_text = None
        message.conversation_id = "cid-1"
        message.conversation_type = "1"
        message.sender_id = "sender-1"
        message.sender_nick = "Alice"
        message.sender_staff_id = "staff-1"
        message.session_webhook = "https://oapi.dingtalk.com/robot/sendBySession?abc=1"
        message.conversation_title = "chat"
        message.create_at = None

        await adapter._on_message(message)

        assert adapter._session_webhooks["cid-1"] == message.session_webhook


# ---------------------------------------------------------------------------
# Config wiring
# ---------------------------------------------------------------------------


class TestDingTalkConfig:

    def test_apply_env_overrides_configures_dingtalk(self):
        config = GatewayConfig()

        with patch.dict(
            os.environ,
            {
                "DINGTALK_CLIENT_ID": "ding-app-id",
                "DINGTALK_CLIENT_SECRET": "ding-secret",
                "DINGTALK_OWNER_USER_IDS": "staff-1,staff-2",
                "DINGTALK_HOME_CHANNEL": "cid-home",
                "DINGTALK_HOME_CHANNEL_NAME": "Boss DM",
            },
            clear=True,
        ):
            _apply_env_overrides(config)

        platform_config = config.platforms[Platform.DINGTALK]
        assert platform_config.enabled is True
        assert platform_config.extra["client_id"] == "ding-app-id"
        assert platform_config.extra["client_secret"] == "ding-secret"
        assert platform_config.extra["owner_user_ids"] == ["staff-1", "staff-2"]
        assert platform_config.home_channel == HomeChannel(Platform.DINGTALK, "cid-home", "Boss DM")

    def test_apply_env_overrides_supports_legacy_bridge_env_names(self):
        config = GatewayConfig()

        with patch.dict(
            os.environ,
            {
                "DINGTALK_CLIENT_ID": "ding-app-id",
                "DINGTALK_CLIENT_SECRET": "ding-secret",
                "DINGTALK_OWNER_IDS": "staff-legacy",
                "DINGTALK_PRIMARY_CHAT_ID": "cid-primary",
            },
            clear=True,
        ):
            _apply_env_overrides(config)

        platform_config = config.platforms[Platform.DINGTALK]
        assert platform_config.extra["owner_user_ids"] == ["staff-legacy"]
        assert platform_config.home_channel == HomeChannel(Platform.DINGTALK, "cid-primary", "Home")

    def test_get_connected_platforms_includes_dingtalk_with_credentials(self):
        config = GatewayConfig(
            platforms={
                Platform.DINGTALK: PlatformConfig(
                    enabled=True,
                    extra={"client_id": "ding-app-id", "client_secret": "ding-secret"},
                ),
            }
        )

        assert Platform.DINGTALK in config.get_connected_platforms()


# ---------------------------------------------------------------------------
# Platform enum
# ---------------------------------------------------------------------------


class TestPlatformEnum:

    def test_dingtalk_in_platform_enum(self):
        assert Platform.DINGTALK.value == "dingtalk"
