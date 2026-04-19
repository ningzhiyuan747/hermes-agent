#!/usr/bin/env python3
"""Render a unified operational task board across capability runs, background jobs, and subagent tasks."""

from __future__ import annotations

import argparse
import json
import sys
from pathlib import Path
from typing import Any

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.operational_task_board_service import build_operational_task_snapshot


def _short(text: Any, limit: int = 72) -> str:
    value = " ".join(str(text or "").split())
    return value if len(value) <= limit else value[: limit - 3] + "..."


def collect_snapshot(limit: int = 8) -> dict[str, Any]:
    return build_operational_task_snapshot(limit=max(1, limit))


def build_report(snapshot: dict[str, Any], *, limit: int = 8) -> str:
    counts = snapshot.get("counts") if isinstance(snapshot.get("counts"), dict) else {}
    run_counts = counts.get("capability_runs") if isinstance(counts.get("capability_runs"), dict) else {}
    job_counts = counts.get("background_jobs") if isinstance(counts.get("background_jobs"), dict) else {}
    subagent_counts = counts.get("delegation_tasks") if isinstance(counts.get("delegation_tasks"), dict) else {}
    units = snapshot.get("units") if isinstance(snapshot.get("units"), list) else []

    active_runs = [unit for unit in units if unit.get("unit_type") == "capability_run" and str(unit.get("status") or "") in {"queued", "running", "pending_approval", "blocked", "paused"}]
    active_jobs = [unit for unit in units if unit.get("unit_type") == "background_job" and str(unit.get("status") or "") in {"queued", "running", "paused", "blocked"}]
    active_subagents = [unit for unit in units if unit.get("unit_type") == "delegation_task" and str(unit.get("status") or "") in {"created", "running"}]

    lines = ["统一运行任务总览", ""]
    lines.append("一、能力运行（capability runs）")
    lines.append(f"- 总数: {len(snapshot.get('capability_runs') or [])}")
    lines.append(f"- 状态分布: {run_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_runs)}")
    for row in active_runs[:limit]:
        lines.append(
            f"  - {str(row.get('related_ids', {}).get('run_id') or row.get('unit_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}"
        )

    lines.extend(["", "二、后台任务（background jobs）"])
    lines.append(f"- 总数: {len(snapshot.get('background_jobs') or [])}")
    lines.append(f"- 状态分布: {job_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_jobs)}")
    for row in active_jobs[:limit]:
        lines.append(
            f"  - {str(row.get('related_ids', {}).get('job_id') or row.get('unit_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}"
        )

    lines.extend(["", "三、子智能体任务（delegation tasks）"])
    lines.append(f"- 总数: {len(snapshot.get('delegation_tasks') or [])}")
    lines.append(f"- 状态分布: {subagent_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_subagents)}")
    for row in active_subagents[:limit]:
        lines.append(
            f"  - {str(row.get('status') or '-').strip()} | {str(row.get('owner') or 'generic').strip()} | "
            f"{_short(row.get('title') or '-', 90)}"
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
