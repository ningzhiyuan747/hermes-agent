from __future__ import annotations

from dataclasses import dataclass, field
from typing import Any, Dict


PRIVATE_CHAT_TYPES = {"1", "single", "singlechat", "dm", "p2p", "private"}


@dataclass(frozen=True)
class IncomingMessage:
    platform: str
    chat_id: str
    thread_id: str = ""
    chat_type: str = ""
    chat_name: str = ""
    user_id: str = ""
    user_name: str = ""
    text: str = ""
    session_key: str = ""
    metadata: Dict[str, Any] = field(default_factory=dict)

    def is_private_chat(self) -> bool:
        return str(self.chat_type or "").strip().lower() in PRIVATE_CHAT_TYPES

    def to_dict(self) -> Dict[str, Any]:
        return {
            "platform": self.platform,
            "chat_id": self.chat_id,
            "thread_id": self.thread_id,
            "chat_type": self.chat_type,
            "chat_name": self.chat_name,
            "user_id": self.user_id,
            "user_name": self.user_name,
            "text": self.text,
            "session_key": self.session_key,
            "metadata": dict(self.metadata or {}),
        }

    @staticmethod
    def _normalize_platform_name(value: Any) -> str:
        raw = getattr(value, "value", value)
        return str(raw or "").strip().lower()

    @classmethod
    def from_source(
        cls,
        source: Any,
        *,
        platform: str = "",
        text: str = "",
        session_key: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> "IncomingMessage":
        source_obj = source or object()
        return cls(
            platform=cls._normalize_platform_name(platform or getattr(source_obj, "platform", "")),
            chat_id=str(getattr(source_obj, "chat_id", "") or "").strip(),
            thread_id=str(getattr(source_obj, "thread_id", "") or "").strip(),
            chat_type=str(getattr(source_obj, "chat_type", "") or "").strip(),
            chat_name=str(
                getattr(source_obj, "chat_name", "")
                or getattr(source_obj, "conversation_title", "")
                or getattr(source_obj, "conversation_name", "")
                or ""
            ).strip(),
            user_id=str(
                getattr(source_obj, "user_id", "")
                or getattr(source_obj, "sender_staff_id", "")
                or getattr(source_obj, "sender_id", "")
                or ""
            ).strip(),
            user_name=str(
                getattr(source_obj, "user_name", "")
                or getattr(source_obj, "sender_nick", "")
                or ""
            ).strip(),
            text=str(text or "").strip(),
            session_key=str(session_key or "").strip(),
            metadata=dict(metadata or {}),
        )

    @classmethod
    def from_event(
        cls,
        event: Any,
        *,
        platform: str = "",
        session_key: str = "",
        metadata: Dict[str, Any] | None = None,
    ) -> "IncomingMessage":
        return cls.from_source(
            getattr(event, "source", None),
            platform=platform,
            text=str(getattr(event, "text", "") or "").strip(),
            session_key=session_key,
            metadata=metadata,
        )

    @classmethod
    def from_dict(cls, payload: Dict[str, Any]) -> "IncomingMessage":
        metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
        return cls(
            platform=str(payload.get("platform") or "").strip().lower(),
            chat_id=str(payload.get("chat_id") or "").strip(),
            thread_id=str(payload.get("thread_id") or "").strip(),
            chat_type=str(payload.get("chat_type") or "").strip(),
            chat_name=str(payload.get("chat_name") or "").strip(),
            user_id=str(payload.get("user_id") or "").strip(),
            user_name=str(payload.get("user_name") or "").strip(),
            text=str(payload.get("text") or "").strip(),
            session_key=str(payload.get("session_key") or "").strip(),
            metadata=dict(metadata),
        )
