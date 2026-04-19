#!/usr/bin/env python3
"""Summarize delegated worker task volume from JSONL ledgers."""

from __future__ import annotations

import argparse
import json
import sys
from collections import Counter
from datetime import datetime, timedelta
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_constants import get_hermes_home


def _reports_root() -> Path:
    return get_hermes_home() / "delegation_reports"


def _iter_ledger_files(days: int) -> list[Path]:
    root = _reports_root()
    if not root.exists():
        return []
    today = datetime.now().date()
    files: list[Path] = []
    for offset in range(days):
        day = today - timedelta(days=offset)
        candidate = root / f"delegation-tasks-{day.isoformat()}.jsonl"
        if candidate.exists():
            files.append(candidate)
    return sorted(files)


def _load_records(days: int, session_id: str | None = None) -> list[dict[str, Any]]:
    records: list[dict[str, Any]] = []
    for path in _iter_ledger_files(days):
        with path.open("r", encoding="utf-8") as fh:
            for line in fh:
                line = line.strip()
                if not line:
                    continue
                try:
                    value = json.loads(line)
                except json.JSONDecodeError:
                    continue
                if isinstance(value, dict):
                    if session_id and str(value.get("parent_session_id") or "") != session_id:
                        continue
                    records.append(value)
    return records


def _goal_label(goal: str, limit: int = 48) -> str:
    text = " ".join(str(goal or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _role_label(record: dict[str, Any]) -> str:
    role_title = str(record.get("role_title") or "").strip()
    role = str(record.get("role") or "").strip()
    if role_title:
        return role_title
    if role:
        return f"{role} worker"
    return "generic subagent"


def build_report(days: int, session_id: str | None = None) -> str:
    records = _load_records(days, session_id=session_id)
    header = f"子智能体任务周报（近 {days} 天）"
    if session_id:
        header += f"（会话 {session_id}）"
    if not records:
        return f"{header}\n\n没有找到任务台账。"

    total = len(records)
    status_counter = Counter(str(r.get("status") or "unknown") for r in records)
    platform_counter = Counter(str(r.get("parent_platform") or "unknown") for r in records)
    role_counter = Counter(_role_label(r) for r in records)
    user_counter = Counter(
        str(r.get("parent_user_id") or "unknown")
        for r in records
        if r.get("parent_user_id")
    )
    duration_values = [float(r.get("duration_seconds") or 0) for r in records]
    avg_duration = sum(duration_values) / len(duration_values) if duration_values else 0.0
    total_duration = sum(duration_values)

    top_goals = Counter(
        _goal_label(str(r.get("goal") or ""))
        for r in records
        if r.get("goal")
    ).most_common(8)
    top_tools = Counter(
        tool
        for r in records
        for tool in (r.get("tool_names") or [])
        if isinstance(tool, str) and tool
    ).most_common(8)

    slowest = sorted(
        records,
        key=lambda r: float(r.get("duration_seconds") or 0),
        reverse=True,
    )[:5]
    failures = [
        r for r in records if str(r.get("status")) in {"failed", "error", "interrupted"}
    ][:8]

    lines: list[str] = [header, ""]
    lines.append(f"总任务数: {total}")
    lines.append(f"完成: {status_counter.get('completed', 0)}")
    lines.append(
        "失败 / 错误 / 中断: "
        f"{status_counter.get('failed', 0) + status_counter.get('error', 0) + status_counter.get('interrupted', 0)}"
    )
    lines.append(f"总耗时: {total_duration:.1f} 秒")
    lines.append(f"平均耗时: {avg_duration:.1f} 秒")

    lines.append("")
    lines.append("Worker 分布:")
    for name, count in role_counter.most_common():
        lines.append(f"- {name}: {count}")

    lines.append("")
    lines.append("平台分布:")
    for name, count in platform_counter.most_common():
        lines.append(f"- {name}: {count}")

    if user_counter:
        lines.append("")
        lines.append("最活跃用户:")
        for name, count in user_counter.most_common(5):
            lines.append(f"- {name}: {count}")

    if top_goals:
        lines.append("")
        lines.append("高频任务:")
        for goal, count in top_goals:
            lines.append(f"- {goal}: {count}")

    if top_tools:
        lines.append("")
        lines.append("常用工具:")
        for tool, count in top_tools:
            lines.append(f"- {tool}: {count}")

    if slowest:
        lines.append("")
        lines.append("最耗时任务:")
        for item in slowest:
            lines.append(
                f"- {float(item.get('duration_seconds') or 0):.1f}s | {_role_label(item)} | "
                f"{item.get('status')} | {_goal_label(str(item.get('goal') or ''), 64)}"
            )

    if failures:
        lines.append("")
        lines.append("需要关注的任务:")
        for item in failures:
            err = str(item.get("error") or item.get("exit_reason") or "").strip()
            if len(err) > 72:
                err = err[:69] + "..."
            lines.append(
                f"- {_role_label(item)} | {item.get('status')} | "
                f"{_goal_label(str(item.get('goal') or ''), 56)} | {err or '无详细错误'}"
            )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Summarize delegated worker task volume.")
    parser.add_argument("--days", type=int, default=7, help="Rolling window to summarize.")
    parser.add_argument("--session-id", type=str, default="", help="Only summarize tasks for one parent session.")
    args = parser.parse_args()
    print(build_report(max(1, args.days), session_id=(args.session_id or "").strip() or None))


if __name__ == "__main__":
    main()
