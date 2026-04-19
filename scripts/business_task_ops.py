#!/usr/bin/env python3
"""Shared task, approval, and channel operations for Hermes chat bridges."""

from __future__ import annotations

import argparse
import base64
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.business_command_ops import run_approval_command, run_channel_command, run_task_command  # noqa: E402
from agent.business_command_service import dispatch_business_text_command  # noqa: E402
from agent.incoming_message import IncomingMessage  # noqa: E402


def _load_payload(encoded: str) -> dict[str, Any]:
    raw = base64.b64decode(str(encoded or "").strip()).decode("utf-8")
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def run_dispatch_command(payload: dict[str, Any]) -> str:
    message_payload = payload.get("message") if isinstance(payload.get("message"), dict) else {}
    message = IncomingMessage.from_dict(message_payload)
    result = dispatch_business_text_command(
        message,
        can_manage_bindings=bool(payload.get("can_manage_bindings", True)),
        can_manage_profiles=bool(payload.get("can_manage_profiles", payload.get("can_manage_bindings", True))),
    )
    return result or ""


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes shared task and approval ops.")
    parser.add_argument("mode", choices=["task", "approval", "channel", "dispatch"])
    parser.add_argument("--payload-b64", required=True)
    args = parser.parse_args()
    payload = _load_payload(args.payload_b64)

    if args.mode == "task":
        print(run_task_command(payload))
    elif args.mode == "approval":
        print(run_approval_command(payload))
    elif args.mode == "channel":
        print(run_channel_command(payload))
    else:
        print(run_dispatch_command(payload))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
