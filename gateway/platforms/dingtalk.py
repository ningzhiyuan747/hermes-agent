"""
DingTalk platform adapter using Stream Mode.

Uses dingtalk-stream SDK for real-time message reception without webhooks.
Responses are sent via DingTalk's session webhook (markdown format).

Requires:
    pip install dingtalk-stream httpx
    DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET env vars

Configuration in config.yaml:
    platforms:
      dingtalk:
        enabled: true
        extra:
          client_id: "your-app-key"      # or DINGTALK_CLIENT_ID env var
          client_secret: "your-secret"   # or DINGTALK_CLIENT_SECRET env var
"""

import asyncio
import inspect
import logging
import os
import re
import uuid
from datetime import datetime, timezone
from typing import Any, Dict, Optional

try:
    import dingtalk_stream
    from dingtalk_stream import ChatbotHandler, ChatbotMessage
    DINGTALK_STREAM_AVAILABLE = True
except ImportError:
    DINGTALK_STREAM_AVAILABLE = False
    dingtalk_stream = None  # type: ignore[assignment]

try:
    import httpx
    HTTPX_AVAILABLE = True
except ImportError:
    HTTPX_AVAILABLE = False
    httpx = None  # type: ignore[assignment]

from gateway.config import Platform, PlatformConfig
from gateway.platforms.helpers import MessageDeduplicator
from gateway.platforms.base import (
    BasePlatformAdapter,
    MessageEvent,
    MessageType,
    SendResult,
)
from gateway.platforms.adapter_context import build_adapter_context_for_event
from gateway.session import build_session_key

logger = logging.getLogger(__name__)

MAX_MESSAGE_LENGTH = 20000
RECONNECT_BACKOFF = [2, 5, 10, 30, 60]
_SESSION_WEBHOOKS_MAX = 500
_DINGTALK_WEBHOOK_RE = re.compile(r"^https://(?:api|oapi)\.dingtalk\.com/")

try:
    from agent.business_command_service import dispatch_business_text_command
    from agent.business_db import get_channel, get_user, upsert_channel, upsert_user
except Exception:  # pragma: no cover - gateway should still boot without business DB.
    dispatch_business_text_command = None  # type: ignore[assignment]
    get_channel = None  # type: ignore[assignment]
    get_user = None  # type: ignore[assignment]
    upsert_channel = None  # type: ignore[assignment]
    upsert_user = None  # type: ignore[assignment]


def check_dingtalk_requirements() -> bool:
    """Check if DingTalk dependencies are available and configured."""
    if not DINGTALK_STREAM_AVAILABLE or not HTTPX_AVAILABLE:
        return False
    if not os.getenv("DINGTALK_CLIENT_ID") or not os.getenv("DINGTALK_CLIENT_SECRET"):
        return False
    return True


class DingTalkAdapter(BasePlatformAdapter):
    """DingTalk chatbot adapter using Stream Mode.

    The dingtalk-stream SDK maintains a long-lived WebSocket connection.
    Incoming messages arrive via a ChatbotHandler callback. Replies are
    sent via the incoming message's session_webhook URL using httpx.
    """

    MAX_MESSAGE_LENGTH = MAX_MESSAGE_LENGTH

    def __init__(self, config: PlatformConfig):
        super().__init__(config, Platform.DINGTALK)

        extra = config.extra or {}
        self._client_id: str = extra.get("client_id") or os.getenv("DINGTALK_CLIENT_ID", "")
        self._client_secret: str = extra.get("client_secret") or os.getenv("DINGTALK_CLIENT_SECRET", "")

        self._stream_client: Any = None
        self._stream_task: Optional[asyncio.Task] = None
        self._http_client: Optional["httpx.AsyncClient"] = None
        owner_ids = (
            extra.get("owner_user_ids")
            or os.getenv("DINGTALK_OWNER_USER_IDS", "")
            or os.getenv("DINGTALK_OWNER_IDS", "")
        )
        self._owner_user_ids = {
            str(item).strip()
            for item in (owner_ids.split(",") if isinstance(owner_ids, str) else owner_ids or [])
            if str(item).strip()
        }

        # Message deduplication
        self._dedup = MessageDeduplicator(max_size=1000)
        # Map chat_id -> session_webhook for reply routing
        self._session_webhooks: Dict[str, str] = {}

    # -- Connection lifecycle -----------------------------------------------

    async def connect(self) -> bool:
        """Connect to DingTalk via Stream Mode."""
        if not DINGTALK_STREAM_AVAILABLE:
            logger.warning("[%s] dingtalk-stream not installed. Run: pip install dingtalk-stream", self.name)
            return False
        if not HTTPX_AVAILABLE:
            logger.warning("[%s] httpx not installed. Run: pip install httpx", self.name)
            return False
        if not self._client_id or not self._client_secret:
            logger.warning("[%s] DINGTALK_CLIENT_ID and DINGTALK_CLIENT_SECRET required", self.name)
            return False

        try:
            self._http_client = httpx.AsyncClient(timeout=30.0)

            credential = dingtalk_stream.Credential(self._client_id, self._client_secret)
            self._stream_client = dingtalk_stream.DingTalkStreamClient(credential)

            # Capture the current event loop for cross-thread dispatch
            loop = asyncio.get_running_loop()
            handler = _IncomingHandler(self, loop)
            self._stream_client.register_callback_handler(
                dingtalk_stream.ChatbotMessage.TOPIC, handler
            )

            self._stream_task = asyncio.create_task(self._run_stream())
            self._mark_connected()
            logger.info("[%s] Connected via Stream Mode", self.name)
            return True
        except Exception as e:
            logger.error("[%s] Failed to connect: %s", self.name, e)
            return False

    async def _run_stream(self) -> None:
        """Run the blocking stream client with auto-reconnection."""
        backoff_idx = 0
        while self._running:
            try:
                logger.debug("[%s] Starting stream client...", self.name)
                start_result = await asyncio.to_thread(self._stream_client.start)
                if inspect.isawaitable(start_result):
                    await start_result
            except asyncio.CancelledError:
                return
            except Exception as e:
                if not self._running:
                    return
                logger.warning("[%s] Stream client error: %s", self.name, e)

            if not self._running:
                return

            delay = RECONNECT_BACKOFF[min(backoff_idx, len(RECONNECT_BACKOFF) - 1)]
            logger.info("[%s] Reconnecting in %ds...", self.name, delay)
            await asyncio.sleep(delay)
            backoff_idx += 1

    async def disconnect(self) -> None:
        """Disconnect from DingTalk."""
        self._running = False
        self._mark_disconnected()

        if self._stream_task:
            self._stream_task.cancel()
            try:
                await self._stream_task
            except asyncio.CancelledError:
                pass
            self._stream_task = None

        if self._http_client:
            await self._http_client.aclose()
            self._http_client = None

        self._stream_client = None
        self._session_webhooks.clear()
        self._dedup.clear()
        logger.info("[%s] Disconnected", self.name)

    # -- Inbound message processing -----------------------------------------

    async def _on_message(self, message: "ChatbotMessage") -> None:
        """Process an incoming DingTalk chatbot message."""
        msg_id = getattr(message, "message_id", None) or uuid.uuid4().hex
        if self._dedup.is_duplicate(msg_id):
            logger.debug("[%s] Duplicate message %s, skipping", self.name, msg_id)
            return

        text = self._extract_text(message)
        if not text:
            logger.debug("[%s] Empty message, skipping", self.name)
            return

        # Chat context
        conversation_id = getattr(message, "conversation_id", "") or ""
        conversation_type = getattr(message, "conversation_type", "1")
        is_group = str(conversation_type) == "2"
        sender_id = getattr(message, "sender_id", "") or ""
        sender_nick = getattr(message, "sender_nick", "") or sender_id
        sender_staff_id = getattr(message, "sender_staff_id", "") or ""

        chat_id = conversation_id or sender_id
        chat_type = "group" if is_group else "dm"

        # Store session webhook for reply routing (validate origin to prevent SSRF)
        session_webhook = getattr(message, "session_webhook", None) or ""
        if session_webhook and chat_id and _DINGTALK_WEBHOOK_RE.match(session_webhook):
            if len(self._session_webhooks) >= _SESSION_WEBHOOKS_MAX:
                # Evict oldest entry to cap memory growth
                try:
                    self._session_webhooks.pop(next(iter(self._session_webhooks)))
                except StopIteration:
                    pass
            self._session_webhooks[chat_id] = session_webhook

        source = self.build_source(
            chat_id=chat_id,
            chat_name=getattr(message, "conversation_title", None),
            chat_type=chat_type,
            user_id=sender_id,
            user_name=sender_nick,
            user_id_alt=sender_staff_id if sender_staff_id else None,
        )

        # Parse timestamp
        create_at = getattr(message, "create_at", None)
        try:
            timestamp = datetime.fromtimestamp(int(create_at) / 1000, tz=timezone.utc) if create_at else datetime.now(tz=timezone.utc)
        except (ValueError, OSError, TypeError):
            timestamp = datetime.now(tz=timezone.utc)

        event = MessageEvent(
            text=text,
            message_type=MessageType.TEXT,
            source=source,
            message_id=msg_id,
            raw_message=message,
            timestamp=timestamp,
        )

        self._sync_business_identity(event)
        if await self._handle_native_business_command(event, session_webhook=session_webhook):
            return

        logger.debug("[%s] Message from %s in %s: %s",
                      self.name, sender_nick, chat_id[:20] if chat_id else "?", text[:50])
        await self.handle_message(event)

    @staticmethod
    def _extract_text(message: "ChatbotMessage") -> str:
        """Extract plain text from a DingTalk chatbot message."""
        text = getattr(message, "text", None) or ""
        if isinstance(text, dict):
            content = text.get("content", "").strip()
        elif hasattr(text, "content"):
            content = str(getattr(text, "content", "") or "").strip()
        else:
            content = str(text).strip()

        # Fall back to rich text if present
        if not content:
            rich_text = getattr(message, "rich_text", None)
            if rich_text and isinstance(rich_text, list):
                parts = [item["text"] for item in rich_text
                         if isinstance(item, dict) and item.get("text")]
                content = " ".join(parts).strip()
        return content

    # -- Outbound messaging -------------------------------------------------

    async def send(
        self,
        chat_id: str,
        content: str,
        reply_to: Optional[str] = None,
        metadata: Optional[Dict[str, Any]] = None,
    ) -> SendResult:
        """Send a markdown reply via DingTalk session webhook."""
        metadata = metadata or {}

        session_webhook = metadata.get("session_webhook") or self._session_webhooks.get(chat_id)
        if not session_webhook:
            return SendResult(success=False,
                              error="No session_webhook available. Reply must follow an incoming message.")

        if not self._http_client:
            return SendResult(success=False, error="HTTP client not initialized")

        payload = {
            "msgtype": "markdown",
            "markdown": {"title": "Hermes", "text": content[:self.MAX_MESSAGE_LENGTH]},
        }

        try:
            resp = await self._http_client.post(session_webhook, json=payload, timeout=15.0)
            if resp.status_code < 300:
                return SendResult(success=True, message_id=uuid.uuid4().hex[:12])
            body = resp.text
            logger.warning("[%s] Send failed HTTP %d: %s", self.name, resp.status_code, body[:200])
            return SendResult(success=False, error=f"HTTP {resp.status_code}: {body[:200]}")
        except httpx.TimeoutException:
            return SendResult(success=False, error="Timeout sending message to DingTalk")
        except Exception as e:
            logger.error("[%s] Send error: %s", self.name, e)
            return SendResult(success=False, error=str(e))

    async def send_typing(self, chat_id: str, metadata=None) -> None:
        """DingTalk does not support typing indicators."""
        pass

    async def get_chat_info(self, chat_id: str) -> Dict[str, Any]:
        """Return basic info about a DingTalk conversation."""
        return {"name": chat_id, "type": "group" if "group" in chat_id.lower() else "dm"}

    async def _send_plain(self, event: MessageEvent, text: str, *, session_webhook: str = "") -> SendResult:
        metadata = {"session_webhook": session_webhook} if session_webhook else None
        return await self.send(
            str(event.source.chat_id or "").strip(),
            text,
            reply_to=event.message_id,
            metadata=metadata,
        )

    def _is_owner_or_admin(self, event: MessageEvent) -> bool:
        sender_ids = {
            str(event.source.user_id or "").strip(),
            str(getattr(event.source, "user_id_alt", "") or "").strip(),
        } - {""}
        if sender_ids & self._owner_user_ids:
            return True
        if get_user is None:
            return False
        for identifier in sender_ids:
            try:
                user = get_user(platform="dingtalk", user_id=identifier)
            except Exception:
                continue
            if str((user or {}).get("role") or "").strip().lower() == "owner":
                return True
        return False

    def _sync_business_identity(self, event: MessageEvent) -> None:
        if upsert_user is None or upsert_channel is None:
            return
        try:
            sender_ids = {
                str(event.source.user_id or "").strip(),
                str(getattr(event.source, "user_id_alt", "") or "").strip(),
            } - {""}
            preferred_user_id = str(getattr(event.source, "user_id_alt", "") or event.source.user_id or "").strip()
            existing_user = get_user(platform="dingtalk", user_id=preferred_user_id) if get_user is not None and preferred_user_id else {}
            role = "owner" if sender_ids & self._owner_user_ids else str((existing_user or {}).get("role") or "user").strip().lower() or "user"
            permissions = dict((existing_user or {}).get("permissions") or {}) if isinstance((existing_user or {}).get("permissions"), dict) else {}
            if role == "owner":
                permissions["bypass_approval"] = True
                permissions["global_owner"] = True
            if preferred_user_id:
                upsert_user(
                    platform="dingtalk",
                    user_id=preferred_user_id,
                    display_name=str(event.source.user_name or "").strip(),
                    role=role,
                    permissions=permissions,
                )
            existing_channel = get_channel(
                platform="dingtalk",
                chat_id=str(event.source.chat_id or "").strip(),
                thread_id=str(event.source.thread_id or "").strip(),
            ) if get_channel is not None else {}
            upsert_channel(
                platform="dingtalk",
                chat_id=str(event.source.chat_id or "").strip(),
                thread_id=str(event.source.thread_id or "").strip(),
                task_id=str((existing_channel or {}).get("task_id") or "").strip(),
                chat_name=str(event.source.chat_name or "").strip(),
                chat_type=str(event.source.chat_type or "").strip(),
                worker_role=str((existing_channel or {}).get("worker_role") or "").strip(),
                allow_free_chat=bool((existing_channel or {}).get("allow_free_chat")) or str(event.source.chat_type or "").strip() == "dm",
                policy=(existing_channel or {}).get("policy") or {},
            )
        except Exception:
            logger.debug("[DingTalk] Failed to sync business identity", exc_info=True)

    async def _handle_native_business_command(self, event: MessageEvent, *, session_webhook: str = "") -> bool:
        if dispatch_business_text_command is None:
            return False
        session_key = build_session_key(
            event.source,
            group_sessions_per_user=self.config.extra.get("group_sessions_per_user", True),
            thread_sessions_per_user=self.config.extra.get("thread_sessions_per_user", False),
        )
        context = build_adapter_context_for_event(
            event,
            platform_name="dingtalk",
            session_key=session_key,
        )
        response = dispatch_business_text_command(
            context.incoming_message,
            can_manage_bindings=self._is_owner_or_admin(event),
        )
        if not response:
            return False
        await self._send_plain(event, response, session_webhook=session_webhook)
        return True


# ---------------------------------------------------------------------------
# Internal stream handler
# ---------------------------------------------------------------------------

class _IncomingHandler(ChatbotHandler if DINGTALK_STREAM_AVAILABLE else object):
    """dingtalk-stream ChatbotHandler that forwards messages to the adapter."""

    def __init__(self, adapter: DingTalkAdapter, loop: asyncio.AbstractEventLoop):
        if DINGTALK_STREAM_AVAILABLE:
            super().__init__()
        self._adapter = adapter
        self._loop = loop

    @staticmethod
    def _normalize_message(message: Any) -> "ChatbotMessage":
        if (
            DINGTALK_STREAM_AVAILABLE
            and hasattr(message, "data")
            and isinstance(getattr(message, "data", None), dict)
        ):
            return dingtalk_stream.ChatbotMessage.from_dict(message.data)
        return message

    async def process(self, message: Any):
        """Called by dingtalk-stream when a callback arrives.

        The SDK passes a CallbackMessage whose `.data` payload contains the
        actual chatbot message body, so normalize it before dispatch.
        """
        try:
            await self._adapter._on_message(self._normalize_message(message))
        except Exception:
            logger.exception("[DingTalk] Error processing incoming message")

        return dingtalk_stream.AckMessage.STATUS_OK, "OK"
