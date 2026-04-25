#!/usr/bin/env python3
"""Hermes-owned memory writeback entrypoints for platform adapters."""

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

from agent.user_profile_distiller import distill_user_profile  # noqa: E402
from tools.memory_tool import MemoryStore, memory_tool  # noqa: E402


def _load_payload(encoded: str) -> dict[str, Any]:
    raw = base64.b64decode(str(encoded or "").strip()).decode("utf-8")
    payload = json.loads(raw)
    return payload if isinstance(payload, dict) else {}


def run_private_preference_writeback(payload: dict[str, Any]) -> dict[str, Any]:
    platform = str(payload.get("platform") or "dingtalk").strip().lower()
    user_id = str(payload.get("user_id") or "").strip()
    entry = str(payload.get("entry") or "").strip()
    write_result: dict[str, Any] | None = None
    if entry:
        store = MemoryStore()
        try:
            write_result = json.loads(memory_tool(action="add", target="user", content=entry, store=store))
        except Exception as exc:
            write_result = {"success": False, "error": str(exc)}
    distilled = distill_user_profile(platform=platform, user_id=user_id, since_days=30, limit=20) if user_id else None
    return {
        "ok": True,
        "write": write_result,
        "distilled_summary": str((distilled or {}).get("summary") or "").strip(),
    }


def main() -> int:
    parser = argparse.ArgumentParser(description="Hermes-owned platform memory writeback.")
    parser.add_argument("mode", choices=["private-preference"])
    parser.add_argument("--payload-b64", required=True)
    args = parser.parse_args()
    payload = _load_payload(args.payload_b64)
    if args.mode == "private-preference":
        result = run_private_preference_writeback(payload)
    else:  # pragma: no cover
        result = {"ok": False, "error": f"Unsupported mode: {args.mode}"}
    print(json.dumps(result, ensure_ascii=False))
    return 0 if result.get("ok") else 1


if __name__ == "__main__":
    raise SystemExit(main())
