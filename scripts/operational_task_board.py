#!/usr/bin/env python3
"""Render a unified operational task board across capability runs, background jobs, and subagent tasks."""

from __future__ import annotations

import argparse
import json
import sys
import time
from collections import Counter
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.background_jobs import list_jobs
from agent.business_db import list_capability_runs
from scripts.subagent_task_status import _load_task_meta

ACTIVE_RUN_STATUSES = {"queued", "running", "pending_approval", "blocked", "paused"}
ACTIVE_JOB_STATUSES = {"queued", "running", "paused", "blocked"}
ACTIVE_SUBAGENT_STATUSES = {"created", "running"}


def _short(text: Any, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def _status_counts(rows: list[dict[str, Any]], key: str = "status") -> dict[str, int]:
    counter = Counter(str(row.get(key) or "unknown").strip() or "unknown" for row in rows)
    return dict(sorted(counter.items(), key=lambda item: (-item[1], item[0])))


def _active_runs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("status") or "").strip().lower() in ACTIVE_RUN_STATUSES]


def _active_jobs(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("status") or "").strip().lower() in ACTIVE_JOB_STATUSES]


def _active_subagents(rows: list[dict[str, Any]]) -> list[dict[str, Any]]:
    return [row for row in rows if str(row.get("status") or "").strip().lower() in ACTIVE_SUBAGENT_STATUSES]


def collect_snapshot(limit: int = 8) -> dict[str, Any]:
    runs = list_capability_runs(limit=max(20, limit * 4))
    jobs = list_jobs(limit=max(20, limit * 4), active_only=False)
    subagents = _load_task_meta()
    return {
        "generated_at_unix": int(time.time()),
        "capability_runs": runs,
        "background_jobs": jobs,
        "subagent_tasks": subagents,
        "run_counts": _status_counts(runs),
        "job_counts": _status_counts(jobs),
        "subagent_counts": _status_counts(subagents),
        "active_runs": _active_runs(runs)[:limit],
        "active_jobs": _active_jobs(jobs)[:limit],
        "active_subagents": _active_subagents(subagents)[:limit],
    }


def build_report(snapshot: dict[str, Any], *, limit: int = 8) -> str:
    run_counts = snapshot.get("run_counts") or {}
    job_counts = snapshot.get("job_counts") or {}
    subagent_counts = snapshot.get("subagent_counts") or {}
    active_runs = snapshot.get("active_runs") or []
    active_jobs = snapshot.get("active_jobs") or []
    active_subagents = snapshot.get("active_subagents") or []

    lines = ["统一运行任务总览", ""]
    lines.append("一、能力运行（capability runs）")
    lines.append(f"- 总数: {len(snapshot.get('capability_runs') or [])}")
    lines.append(f"- 状态分布: {run_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_runs)}")
    for row in active_runs[:limit]:
        lines.append(
            f"  - {str(row.get('run_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or row.get('goal') or '-', 90)}"
        )

    lines.extend(["", "二、后台任务（background jobs）"])
    lines.append(f"- 总数: {len(snapshot.get('background_jobs') or [])}")
    lines.append(f"- 状态分布: {job_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_jobs)}")
    for row in active_jobs[:limit]:
        lines.append(
            f"  - {str(row.get('job_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}"
        )

    lines.extend(["", "三、子智能体任务（delegation tasks）"])
    lines.append(f"- 总数: {len(snapshot.get('subagent_tasks') or [])}")
    lines.append(f"- 状态分布: {subagent_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_subagents)}")
    for row in active_subagents[:limit]:
        lines.append(
            f"  - {str(row.get('status') or '-').strip()} | {str(row.get('worker_role') or 'generic').strip()} | "
            f"{_short(row.get('goal') or '-', 90)}"
        )

    lines.extend(["", "四、系统判断"])
    if active_runs or active_jobs or active_subagents:
        lines.append("- 当前系统存在正在推进或待推进的任务，但状态源仍分散在 capability runs / background jobs / delegation tasks 三层。")
    else:
        lines.append("- 当前没有发现活跃任务，系统处于相对空闲状态。")
    if job_counts.get("failed", 0):
        lines.append(f"- 背景任务失败较多（failed={job_counts.get('failed', 0)}），说明后台执行链路仍需持续收敛。")
    if run_counts.get("queued", 0):
        lines.append(f"- capability run 中 queued 较多（queued={run_counts.get('queued', 0)}），需要继续推进状态回填与统一调度。")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="Render a unified operational task board.")
    parser.add_argument("--limit", type=int, default=8, help="How many active items to show per section.")
    parser.add_argument("--json", action="store_true", help="Emit JSON instead of text.")
    args = parser.parse_args()
    snapshot = collect_snapshot(limit=max(1, args.limit))
    if args.json:
        print(json.dumps(snapshot, ensure_ascii=False, indent=2))
    else:
        print(build_report(snapshot, limit=max(1, args.limit)))


if __name__ == "__main__":
    main()
