#!/usr/bin/env python3
"""Summarize delegated worker handoffs from JSONL ledgers."""

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
                if not isinstance(value, dict):
                    continue
                if session_id and str(value.get("parent_session_id") or "") != session_id:
                    continue
                records.append(value)
    records.sort(key=lambda row: int(row.get("finished_at_unix") or 0), reverse=True)
    return records


def _clean(text: Any, limit: int = 180) -> str:
    value = " ".join(str(text or "").split())
    if len(value) > limit:
        value = value[: limit - 3] + "..."
    return value


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
        "unknown": "未知",
    }.get(str(value or "").strip(), str(value or "").strip())


def _translate_phrase(text: Any) -> str:
    value = str(text or "").strip()
    mapping = {
        "Completed delegated task": "已完成委派任务",
        "Task completed and handoff report is ready.": "任务已完成，交付报告已准备好。",
        "No explicit blockers remained at handoff time.": "交付时未发现明确阻塞项。",
        "Wait for the parent agent to review the handoff report and decide follow-up work.": "等待主智能体查看交付报告并决定后续工作。",
    }
    if value in mapping:
        return mapping[value]
    if value.startswith("Task started:"):
        return "任务已开始：" + value.split(":", 1)[1].strip()
    return value


def _role_label(record: dict[str, Any]) -> str:
    role_title = _clean(record.get("role_title"), 80)
    role = _clean(record.get("role"), 40)
    if role_title:
        return {
            "Operations Worker": "运维工作者",
            "Bidding Worker": "投标工作者",
        }.get(role_title, role_title)
    if role:
        return f"{role} 工作者"
    return "通用子智能体"


def build_rollup(days: int, session_id: str | None = None, limit: int = 6) -> str:
    records = _load_records(days, session_id=session_id)
    header = f"子智能体交付汇总（近 {days} 天）"
    if session_id:
        header += f"（会话 {session_id}）"
    if not records:
        return f"{header}\n\n没有找到交付记录。"

    status_counter = Counter(str(r.get("status") or "unknown") for r in records)
    worker_counter = Counter(_role_label(r) for r in records)
    top_risks: list[str] = []
    top_steps: list[str] = []
    top_deliverables: list[str] = []

    def push_unique(bucket: list[str], value: Any, max_items: int = 5) -> None:
        cleaned = _clean(value, 180)
        if cleaned and cleaned not in bucket and len(bucket) < max_items:
            bucket.append(cleaned)

    for record in records:
        structured = record.get("handoff_structured") or {}
        if isinstance(structured, dict):
            for item in structured.get("risks") or []:
                push_unique(top_risks, _translate_phrase(item))
            for item in structured.get("deliverables") or []:
                push_unique(top_deliverables, _translate_phrase(item))
            push_unique(top_steps, _translate_phrase(structured.get("next_step")))

    lines: list[str] = [header, ""]
    lines.append(f"交付总数: {len(records)}")
    lines.append("状态分布: " + ", ".join(f"{_translate_status(name)}={count}" for name, count in status_counter.most_common()))
    lines.append("Worker 分布: " + ", ".join(f"{name}={count}" for name, count in worker_counter.most_common(5)))

    if top_deliverables:
        lines.append("")
        lines.append("主要交付:")
        for item in top_deliverables[:5]:
            lines.append(f"- {item}")

    if top_risks:
        lines.append("")
        lines.append("主要风险:")
        for item in top_risks[:5]:
            lines.append(f"- {item}")

    if top_steps:
        lines.append("")
        lines.append("建议下一步:")
        for item in top_steps[:4]:
            lines.append(f"- {item}")

    lines.append("")
    lines.append("最近交付:")
    for record in records[: max(1, limit)]:
        structured = record.get("handoff_structured") or {}
        worker = _role_label(record)
        status = _translate_status(_clean(structured.get("status") or record.get("status"), 48) or "未知")
        summary = _translate_phrase(_clean(structured.get("summary") or record.get("summary"), 140) or "无摘要")
        next_step = _translate_phrase(_clean(structured.get("next_step"), 120))
        deliverables = structured.get("deliverables") or []
        stamp = int(record.get("finished_at_unix") or 0)
        stamp_text = datetime.fromtimestamp(stamp).strftime("%m-%d %H:%M") if stamp else "未知"
        lines.append(f"- {stamp_text} | {worker} | {status}")
        lines.append(f"  摘要: {summary}")
        if deliverables:
            lines.append(f"  交付: {', '.join(_translate_phrase(_clean(item, 60)) for item in deliverables[:3])}")
        if next_step:
            lines.append(f"  下一步: {next_step}")

    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="汇总委派 worker 的交付结果。")
    parser.add_argument("--days", type=int, default=7, help="汇总最近多少天。")
    parser.add_argument("--limit", type=int, default=6, help="最多显示多少条最近交付。")
    parser.add_argument("--session-id", type=str, default="", help="只汇总某个父会话。")
    args = parser.parse_args()
    print(build_rollup(max(1, args.days), session_id=(args.session_id or "").strip() or None, limit=max(1, args.limit)))


if __name__ == "__main__":
    main()
