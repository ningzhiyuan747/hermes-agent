from __future__ import annotations

from dataclasses import dataclass
from typing import Any

from agent.incoming_message import IncomingMessage
from gateway.platforms.incoming_message_factory import build_incoming_message_for_event


@dataclass(frozen=True)
class GatewayAdapterContext:
    platform_name: str
    event: Any
    session_key: str
    incoming_message: IncomingMessage

    @property
    def text(self) -> str:
        return self.incoming_message.text

    @property
    def chat_id(self) -> str:
        return self.incoming_message.chat_id

    @property
    def thread_id(self) -> str:
        return self.incoming_message.thread_id

    @property
    def chat_type(self) -> str:
        return self.incoming_message.chat_type

    @property
    def chat_name(self) -> str:
        return self.incoming_message.chat_name

    @property
    def user_id(self) -> str:
        return self.incoming_message.user_id

    @property
    def user_name(self) -> str:
        return self.incoming_message.user_name

    def is_private_chat(self) -> bool:
        return self.incoming_message.is_private_chat()


def build_adapter_context_for_event(
    event: Any,
    *,
    session_key: str,
    platform_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> GatewayAdapterContext:
    resolved_platform = str(platform_name or "").strip()
    message = build_incoming_message_for_event(
        event,
        session_key=session_key,
        platform_name=resolved_platform,
        metadata=metadata,
    )
    return GatewayAdapterContext(
        platform_name=message.platform,
        event=event,
        session_key=session_key,
        incoming_message=message,
    )
