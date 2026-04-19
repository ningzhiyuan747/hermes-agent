#!/usr/bin/env python3
"""Show recent delegated worker tasks and likely in-flight tasks."""

from __future__ import annotations

import argparse
import json
import re
import sys
from collections import Counter
from datetime import datetime
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_constants import get_hermes_home


def _tasks_root() -> Path:
    return get_hermes_home() / "delegation_tasks"


def _goal_label(goal: str, limit: int = 64) -> str:
    text = " ".join(str(goal or "").split())
    if len(text) <= limit:
        return text
    return text[: limit - 3] + "..."


def _translate_status(value: str) -> str:
    return {
        "created": "已创建",
        "running": "运行中",
        "completed": "已完成",
        "cancelled": "已取消",
        "failed": "失败",
        "pending": "待处理",
        "pending_approval": "待审批",
        "queued": "已排队",
    }.get(str(value or "").strip(), str(value or "").strip())


def _role_label(row: dict[str, Any]) -> str:
    role_title = str(row.get("role_title") or "").strip()
    role = str(row.get("role") or "").strip()
    if role_title:
        return {
            "Operations Worker": "运维工作者",
            "Bidding Worker": "投标工作者",
        }.get(role_title, role_title)
    if role:
        return f"{role} 工作者"
    return "通用子智能体"


def _load_task_state_excerpt(row: dict[str, Any]) -> dict[str, str]:
    current_state_path = Path(str(row.get("current_state_path") or "").strip())
    if current_state_path.exists():
        try:
            current_text = current_state_path.read_text(encoding="utf-8")
        except Exception:
            current_text = ""
        parsed_current = _parse_markdown_status_sections(current_text)
        if any(parsed_current.values()):
            return parsed_current

    path_value = str(row.get("task_state_path") or "").strip()
    if not path_value:
        return {}
    path = Path(path_value)
    if not path.exists():
        return {}
    try:
        text = path.read_text(encoding="utf-8")
    except Exception:
        return {}

    return _parse_markdown_status_sections(text)


def _parse_markdown_status_sections(text: str) -> dict[str, str]:
    sections: dict[str, list[str]] = {}
    current_key = ""
    for raw_line in text.splitlines():
        line = raw_line.rstrip()
        heading = re.match(r"^##\s+(.*)$", line.strip())
        if heading:
            current_key = heading.group(1).strip().lower()
            sections.setdefault(current_key, [])
            continue
        if not current_key:
            continue
        stripped = line.strip()
        if not stripped:
            continue
        stripped = re.sub(r"^[-*]\s*", "", stripped)
        lowered = stripped.lower()
        if lowered.startswith("note the highest-value slice"):
            continue
        if lowered.startswith("state the next concrete action"):
            continue
        if lowered.startswith("note missing information"):
            continue
        sections.setdefault(current_key, []).append(stripped)

    def _first(*keys: str) -> str:
        for key in keys:
            values = sections.get(key.lower()) or []
            if values:
                return " ".join(values[:2]).strip()
        return ""

    return {
        "focus": _first("Current Focus", "当前重点", "当前在做"),
        "next_step": _first("Next Step", "下一步"),
        "blockers": _first("Blockers", "Blocker", "阻塞", "阻塞项"),
    }


def _load_recent_progress_events(row: dict[str, Any], limit: int = 3) -> list[str]:
    path_value = str(row.get("progress_log_path") or "").strip()
    if not path_value:
        meta_path = Path(str(row.get("_meta_path") or ""))
        if meta_path:
            path_value = str(meta_path.with_name("progress.jsonl"))
    path = Path(path_value)
    if not path.exists() or not path.is_file():
        return []

    events: list[str] = []
    try:
        lines = path.read_text(encoding="utf-8").splitlines()[-max(limit * 4, limit):]
    except Exception:
        return []

    for raw in lines:
        raw = raw.strip()
        if not raw:
            continue
        try:
            event = json.loads(raw)
        except json.JSONDecodeError:
            continue
        if not isinstance(event, dict):
            continue
        parts = []
        kind = str(event.get("kind") or "STATUS").strip()
        stamp = str(event.get("timestamp") or "").strip()
        if stamp:
            parts.append(stamp[-8:])
        if kind:
            parts.append(kind)
        note = str(event.get("note") or event.get("focus") or event.get("next_step") or event.get("blocker") or "").strip()
        if note:
            parts.append(_goal_label(note, limit=90))
        if len(parts) >= 2:
            events.append(" | ".join(parts))
    return events[-limit:]


def _load_task_meta(session_id: str | None = None) -> list[dict[str, Any]]:
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
            if session_id and str(value.get("parent_session_id") or "") != session_id:
                continue
            value["_meta_path"] = str(path)
            rows.append(value)
    rows.sort(key=lambda row: int(row.get("created_at_unix") or 0), reverse=True)
    return rows


def build_status(limit: int, session_id: str | None = None) -> str:
    rows = _load_task_meta(session_id=session_id)
    header = "子智能体任务看板"
    if session_id:
        header += f"（会话 {session_id}）"
    if not rows:
        return f"{header}\n\n没有找到任务记录。"

    now_ts = datetime.now().timestamp()
    active = [
        r for r in rows
        if str(r.get("status") or "created") == "running"
        or (
            str(r.get("status") or "created") == "created"
            and now_ts - int(r.get("created_at_unix") or 0) <= 1800
        )
    ]
    recent = rows[: max(1, limit)]
    status_counter = Counter(str(r.get("status") or "created") for r in rows)
    role_counter = Counter(_role_label(r) for r in rows)

    lines: list[str] = [header, ""]
    lines.append(f"任务总数: {len(rows)}")
    lines.append(f"运行中 / 待完成: {len(active)}")
    lines.append(
        "状态分布: "
        + ", ".join(f"{_translate_status(name)}={count}" for name, count in status_counter.most_common())
    )
    lines.append(
        "活跃 worker: "
        + ", ".join(f"{name}={count}" for name, count in role_counter.most_common(5))
    )

    if active:
        lines.append("")
        lines.append("当前任务:")
        for item in active[:limit]:
            created = int(item.get("created_at_unix") or 0)
            created_text = (
                datetime.fromtimestamp(created).strftime("%m-%d %H:%M")
                if created else "未知"
            )
            lines.append(
                f"- {_translate_status(str(item.get('status', 'created')))} | {_role_label(item)} | {created_text} | "
                f"{_goal_label(str(item.get('goal') or ''))}"
            )
            excerpt = _load_task_state_excerpt(item)
            if excerpt.get("focus"):
                lines.append(f"  当前在做: {excerpt['focus']}")
            if excerpt.get("next_step"):
                lines.append(f"  下一步: {excerpt['next_step']}")
            if excerpt.get("blockers"):
                lines.append(f"  阻塞: {excerpt['blockers']}")
            progress_events = _load_recent_progress_events(item, limit=3)
            if progress_events:
                lines.append("  最近进展:")
                for event in progress_events:
                    lines.append(f"  - {event}")

    lines.append("")
    lines.append("最近任务:")
    for item in recent:
        finished = int(item.get("finished_at_unix") or 0)
        stamp = datetime.fromtimestamp(finished).strftime("%m-%d %H:%M") if finished else "未完成"
        duration = float(item.get("duration_seconds") or 0)
        lines.append(
            f"- {stamp} | {_role_label(item)} | {_translate_status(str(item.get('status', 'created')))} | {duration:.1f}s | "
            f"{_goal_label(str(item.get('goal') or ''))}"
        )

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="显示最近的委派 worker 任务。")
    parser.add_argument("--limit", type=int, default=10, help="最多显示多少个任务。")
    parser.add_argument("--session-id", type=str, default="", help="只显示某个父会话的任务。")
    args = parser.parse_args()
    print(build_status(max(1, args.limit), session_id=(args.session_id or "").strip() or None))


if __name__ == "__main__":
    main()
