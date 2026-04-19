from __future__ import annotations

from typing import Any

from agent.incoming_message import IncomingMessage


def build_incoming_message_for_event(
    event: Any,
    *,
    session_key: str,
    platform_name: str = "",
    metadata: dict[str, Any] | None = None,
) -> IncomingMessage:
    source = getattr(event, "source", None)
    resolved_platform = platform_name or getattr(getattr(source, "platform", None), "value", "") or getattr(source, "platform", "")
    return IncomingMessage.from_event(
        event,
        platform=str(resolved_platform or "").strip(),
        session_key=session_key,
        metadata=metadata,
    )
