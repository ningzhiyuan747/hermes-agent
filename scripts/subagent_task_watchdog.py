#!/usr/bin/env python3
"""Detect delegated subagent tasks that appear stuck."""

from __future__ import annotations

import argparse
import json
import sys
import time
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_constants import get_hermes_home


def _tasks_root() -> Path:
    return get_hermes_home() / "delegation_tasks"


def _load_task_meta() -> list[dict[str, Any]]:
    root = _tasks_root()
    if not root.exists():
        return []
    rows: list[dict[str, Any]] = []
    for path in root.rglob("task-meta.json"):
        try:
            value = json.loads(path.read_text(encoding="utf-8"))
        except Exception:
            continue
        if isinstance(value, dict):
            value["_meta_path"] = str(path)
            rows.append(value)
    return rows


def _goal_label(goal: str, limit: int = 72) -> str:
    text = " ".join(str(goal or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def build_watchdog_report(stuck_minutes: int) -> tuple[str, int]:
    rows = _load_task_meta()
    now = int(time.time())
    threshold_seconds = max(1, stuck_minutes) * 60
    suspects: list[dict[str, Any]] = []

    for row in rows:
        status = str(row.get("status") or "created")
        if status not in {"created", "running"}:
            continue
        started = int(row.get("started_at_unix") or row.get("created_at_unix") or 0)
        age_seconds = max(0, now - started) if started else 0
        if age_seconds >= threshold_seconds:
            row["_age_seconds"] = age_seconds
            suspects.append(row)

    suspects.sort(key=lambda row: row.get("_age_seconds", 0), reverse=True)

    header = f"子智能体卡住任务检查（阈值 {stuck_minutes} 分钟）"
    if not suspects:
        return f"{header}\n\n没有发现疑似卡住的任务。", 0

    lines = [header, ""]
    for item in suspects:
        age_minutes = item.get("_age_seconds", 0) / 60
        lines.append(
            f"- {item.get('status', 'created')} | 持续 {age_minutes:.1f} 分钟 | "
            f"{_goal_label(str(item.get('goal') or ''))}"
        )
    return "\n".join(lines), len(suspects)


def main() -> None:
    parser = argparse.ArgumentParser(description="Detect stuck delegated subagent tasks.")
    parser.add_argument("--stuck-minutes", type=int, default=20, help="Mark created/running tasks older than this as suspect.")
    args = parser.parse_args()
    report, suspects = build_watchdog_report(max(1, args.stuck_minutes))
    print(report)
    raise SystemExit(1 if suspects else 0)


if __name__ == "__main__":
    main()
