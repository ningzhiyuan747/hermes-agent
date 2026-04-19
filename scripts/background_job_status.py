#!/usr/bin/env python3
"""Show Hermes background job status."""

from __future__ import annotations

import argparse
import sys
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from agent.background_jobs import get_job, recent_events, render_jobs_status


def render_one(job_id: str, limit: int) -> str:
    job = get_job(job_id)
    if not job:
        return f"后台任务不存在：{job_id}"
    lines = [
        f"后台任务详情：{job.get('job_id')}",
        "",
        f"标题: {job.get('title')}",
        f"状态: {job.get('status')}",
        f"当前: {job.get('current_focus') or '-'}",
        f"下一步: {job.get('next_step') or '-'}",
    ]
    if job.get("blocker"):
        lines.append(f"阻塞: {job.get('blocker')}")
    if job.get("result"):
        lines.extend(["", "结果:", str(job.get("result"))])
    events = recent_events(job_id, limit=limit)
    if events:
        lines.extend(["", "最近事件:"])
        for event in events:
            stamp = str(event.get("timestamp") or "")[-8:] or "-"
            kind = event.get("kind") or "event"
            message = event.get("message") or event.get("status") or ""
            lines.append(f"- {stamp} | {kind} | {message}")
    return "\n".join(lines)


def main() -> None:
    parser = argparse.ArgumentParser(description="显示 Hermes 后台任务。")
    parser.add_argument("--job-id", default="", help="只显示某个任务。")
    parser.add_argument("--status", default="", help="按任务状态筛选。")
    parser.add_argument("--active", action="store_true", help="只显示活跃任务。")
    parser.add_argument("--limit", type=int, default=10, help="最多显示多少个任务/事件。")
    args = parser.parse_args()

    if args.job_id:
        print(render_one(args.job_id.strip(), max(1, args.limit)))
    else:
        print(render_jobs_status(status=args.status.strip(), limit=max(1, args.limit), active_only=args.active))


if __name__ == "__main__":
    main()
