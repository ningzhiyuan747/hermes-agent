#!/usr/bin/env python3
"""Show a smart per-session worker overview: active status first, otherwise recent handoffs."""

from __future__ import annotations

import argparse
import sys
from datetime import datetime
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from scripts.subagent_task_rollup import build_rollup
from scripts.subagent_task_status import _load_task_meta, build_status


def build_overview(session_id: str | None = None, limit: int = 8, days: int = 7) -> str:
    rows = _load_task_meta(session_id=session_id)
    now_ts = datetime.now().timestamp()
    active = [
        r for r in rows
        if str(r.get("status") or "created") == "running"
        or (
            str(r.get("status") or "created") == "created"
            and now_ts - int(r.get("created_at_unix") or 0) <= 1800
        )
    ]
    if active:
        return build_status(limit=limit, session_id=session_id)
    return build_rollup(days=days, session_id=session_id, limit=min(limit, 6))


def main() -> None:
    parser = argparse.ArgumentParser(description="显示某个会话当前最合适的 worker 视图。")
    parser.add_argument("--session-id", type=str, default="", help="要查看的父会话。")
    parser.add_argument("--limit", type=int, default=8, help="最多显示多少个任务或交付。")
    parser.add_argument("--days", type=int, default=7, help="已完成交付的滚动时间窗口（天）。")
    args = parser.parse_args()
    print(
        build_overview(
            session_id=(args.session_id or "").strip() or None,
            limit=max(1, args.limit),
            days=max(1, args.days),
        )
    )


if __name__ == "__main__":
    main()
