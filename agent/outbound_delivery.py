from __future__ import annotations

import json
from dataclasses import dataclass
from typing import Any


@dataclass(frozen=True)
class DeliveryTarget:
    platform: str
    chat_id: str
    thread_id: str = ""

    def is_valid(self) -> bool:
        return bool(str(self.platform or "").strip() and str(self.chat_id or "").strip())

    def to_target_ref(self) -> str:
        base = f"{str(self.platform or '').strip().lower()}:{str(self.chat_id or '').strip()}"
        thread_id = str(self.thread_id or "").strip()
        return f"{base}:{thread_id}" if thread_id else base

    @classmethod
    def from_channel(cls, channel: dict[str, Any]) -> "DeliveryTarget":
        return cls(
            platform=str(channel.get("platform") or "").strip().lower(),
            chat_id=str(channel.get("chat_id") or "").strip(),
            thread_id=str(channel.get("thread_id") or "").strip(),
        )

    @classmethod
    def from_origin(cls, origin: dict[str, Any]) -> "DeliveryTarget":
        return cls(
            platform=str(origin.get("platform") or "").strip().lower(),
            chat_id=str(origin.get("chat_id") or "").strip(),
            thread_id=str(origin.get("thread_id") or "").strip(),
        )


def send_text_to_target(target: DeliveryTarget, text: str) -> dict[str, Any]:
    from tools.send_message_tool import send_message_tool

    raw_result = send_message_tool(
        {
            "action": "send",
            "target": target.to_target_ref(),
            "message": str(text or "").strip(),
        }
    )
    return json.loads(raw_result) if isinstance(raw_result, str) else raw_result
