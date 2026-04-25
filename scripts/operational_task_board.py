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
    task_counts = counts.get("tasks") if isinstance(counts.get("tasks"), dict) else {}
    run_counts = counts.get("capability_runs") if isinstance(counts.get("capability_runs"), dict) else {}
    job_counts = counts.get("background_jobs") if isinstance(counts.get("background_jobs"), dict) else {}
    subagent_counts = counts.get("delegation_tasks") if isinstance(counts.get("delegation_tasks"), dict) else {}
    units = snapshot.get("units") if isinstance(snapshot.get("units"), list) else []

    active_tasks = [unit for unit in units if unit.get("unit_type") == "task" and bool(unit.get("is_active"))]
    active_runs = [unit for unit in units if unit.get("unit_type") == "capability_run" and str(unit.get("status") or "") in {"queued", "running", "pending_approval", "blocked", "paused"}]
    active_jobs = [unit for unit in units if unit.get("unit_type") == "background_job" and str(unit.get("status") or "") in {"queued", "running", "paused", "blocked"}]
    active_subagents = [unit for unit in units if unit.get("unit_type") == "delegation_task" and str(unit.get("status") or "") in {"created", "running"}]

    lines = ["统一运行任务总览", ""]
    lines.append("一、任务主记录（tasks）")
    lines.append(f"- 总数: {len(snapshot.get('tasks') or [])}")
    lines.append(f"- 状态分布: {task_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_tasks)}")
    for row in active_tasks[:limit]:
        scope_bits = []
        task_scope_key = str(row.get("task_scope_key") or "").strip()
        person_memory_key = str(row.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_bits.append(f"task_scope={task_scope_key}")
        if person_memory_key:
            scope_bits.append(f"person_memory={person_memory_key}")
        scope_suffix = f" | {' | '.join(scope_bits)}" if scope_bits else ""
        lines.append(
            f"  - {str(row.get('related_ids', {}).get('task_id') or row.get('unit_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}{scope_suffix}"
        )
        if str(row.get("current_focus") or "").strip():
            lines.append(f"    当前: {_short(row.get('current_focus') or '-', 96)}")
        if str(row.get("next_step") or "").strip():
            lines.append(f"    下一步: {_short(row.get('next_step') or '-', 96)}")
        if str(row.get("recovery_hint") or "").strip():
            lines.append(f"    恢复建议: {_short(row.get('recovery_hint') or '-', 96)}")
        if str(row.get("dispatch_action") or "").strip() or str(row.get("suggested_executor") or "").strip():
            lines.append(
                "    调度: "
                f"{_short(row.get('dispatch_action') or '-', 32)}"
                f" -> {_short(row.get('suggested_executor') or '-', 32)}"
            )

    lines.extend(["", "二、能力运行（capability runs）"])
    lines.append(f"- 总数: {len(snapshot.get('capability_runs') or [])}")
    lines.append(f"- 状态分布: {run_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_runs)}")
    for row in active_runs[:limit]:
        scope_bits = []
        task_scope_key = str(row.get("task_scope_key") or "").strip()
        person_memory_key = str(row.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_bits.append(f"task_scope={task_scope_key}")
        if person_memory_key:
            scope_bits.append(f"person_memory={person_memory_key}")
        scope_suffix = f" | {' | '.join(scope_bits)}" if scope_bits else ""
        lines.append(
            f"  - {str(row.get('related_ids', {}).get('run_id') or row.get('unit_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}{scope_suffix}"
        )

    lines.extend(["", "三、后台任务（background jobs）"])
    lines.append(f"- 总数: {len(snapshot.get('background_jobs') or [])}")
    lines.append(f"- 状态分布: {job_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_jobs)}")
    for row in active_jobs[:limit]:
        scope_bits = []
        task_scope_key = str(row.get("task_scope_key") or "").strip()
        person_memory_key = str(row.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_bits.append(f"task_scope={task_scope_key}")
        if person_memory_key:
            scope_bits.append(f"person_memory={person_memory_key}")
        scope_suffix = f" | {' | '.join(scope_bits)}" if scope_bits else ""
        lines.append(
            f"  - {str(row.get('related_ids', {}).get('job_id') or row.get('unit_id') or '-').strip()} | {str(row.get('status') or '-').strip()} | "
            f"{_short(row.get('title') or '-', 90)}{scope_suffix}"
        )

    lines.extend(["", "四、子智能体任务（delegation tasks）"])
    lines.append(f"- 总数: {len(snapshot.get('delegation_tasks') or [])}")
    lines.append(f"- 状态分布: {subagent_counts or {'none': 0}}")
    lines.append(f"- 活跃数: {len(active_subagents)}")
    for row in active_subagents[:limit]:
        scope_bits = []
        task_scope_key = str(row.get("task_scope_key") or "").strip()
        person_memory_key = str(row.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_bits.append(f"task_scope={task_scope_key}")
        if person_memory_key:
            scope_bits.append(f"person_memory={person_memory_key}")
        scope_suffix = f" | {' | '.join(scope_bits)}" if scope_bits else ""
        lines.append(
            f"  - {str(row.get('status') or '-').strip()} | {str(row.get('owner') or 'generic').strip()} | "
            f"{_short(row.get('title') or '-', 90)}{scope_suffix}"
        )

    lines.extend(["", "五、系统判断"])
    if active_tasks:
        lines.append("- 当前系统存在正在推进或待推进的任务，task 层已经作为主记录，run/job/delegation 主要用于承载执行痕迹。")
    elif active_runs or active_jobs or active_subagents:
        lines.append("- 当前还有执行痕迹活跃，但 task 主记录没有同步活跃状态，需要继续补强回填。")
    else:
        lines.append("- 当前没有发现活跃任务，系统处于相对空闲状态。")
    if job_counts.get("failed", 0):
        lines.append(f"- 背景任务失败较多（failed={job_counts.get('failed', 0)}），说明后台执行链路仍需持续收敛。")
    if run_counts.get("queued", 0):
        lines.append(f"- capability run 中 queued 较多（queued={run_counts.get('queued', 0)}），需要继续推进状态回填与统一调度。")
    scope_summary = snapshot.get("scope_summary") if isinstance(snapshot.get("scope_summary"), dict) else {}
    task_truth_summary = snapshot.get("task_truth_summary") if isinstance(snapshot.get("task_truth_summary"), dict) else {}
    task_failure_summary = snapshot.get("task_failure_summary") if isinstance(snapshot.get("task_failure_summary"), dict) else {}
    top_task_scopes = scope_summary.get("task_scopes") if isinstance(scope_summary.get("task_scopes"), dict) else {}
    top_person_memories = scope_summary.get("person_memories") if isinstance(scope_summary.get("person_memories"), dict) else {}
    top_roles = scope_summary.get("conversation_roles") if isinstance(scope_summary.get("conversation_roles"), dict) else {}
    top_secretary_actions = scope_summary.get("secretary_actions") if isinstance(scope_summary.get("secretary_actions"), dict) else {}
    top_executor_overrides = scope_summary.get("executor_overrides") if isinstance(scope_summary.get("executor_overrides"), dict) else {}
    top_follow_up_targets = scope_summary.get("follow_up_targets") if isinstance(scope_summary.get("follow_up_targets"), dict) else {}
    top_operator_queue_next = scope_summary.get("operator_queue_next") if isinstance(scope_summary.get("operator_queue_next"), dict) else {}
    top_operator_resolutions = scope_summary.get("operator_resolutions") if isinstance(scope_summary.get("operator_resolutions"), dict) else {}
    failure_kinds = task_failure_summary.get("failure_kinds") if isinstance(task_failure_summary.get("failure_kinds"), dict) else {}
    delivery_statuses = task_failure_summary.get("delivery_statuses") if isinstance(task_failure_summary.get("delivery_statuses"), dict) else {}
    dispatch_actions = task_failure_summary.get("dispatch_actions") if isinstance(task_failure_summary.get("dispatch_actions"), dict) else {}
    delivery_platforms = task_failure_summary.get("delivery_platforms") if isinstance(task_failure_summary.get("delivery_platforms"), dict) else {}
    linked_traces = task_truth_summary.get("linked_active_traces") if isinstance(task_truth_summary.get("linked_active_traces"), dict) else {}
    orphaned_traces = task_truth_summary.get("orphaned_active_traces") if isinstance(task_truth_summary.get("orphaned_active_traces"), dict) else {}
    terminal_task_traces = task_truth_summary.get("terminal_task_active_traces") if isinstance(task_truth_summary.get("terminal_task_active_traces"), dict) else {}
    if top_task_scopes:
        lines.append(f"- 任务面热点: {top_task_scopes}")
        if top_person_memories:
            lines.append(f"- 人物记忆热点: {top_person_memories}")
        if top_roles:
            lines.append(f"- 会话角色分布: {top_roles}")
    if task_truth_summary:
        lines.append(
            "- task 真相摘要: "
            f"active={int(task_truth_summary.get('active_tasks') or 0)}, "
            f"backed={int(task_truth_summary.get('active_tasks_with_active_trace') or 0)}, "
            f"unbacked={int(task_truth_summary.get('active_tasks_without_active_trace') or 0)}"
        )
        if linked_traces:
            lines.append(f"- 已挂到 task 主记录的活跃 traces: {linked_traces}")
        if any(int(value or 0) > 0 for value in orphaned_traces.values()):
            lines.append(f"- 游离活跃 traces: {orphaned_traces}")
        if any(int(value or 0) > 0 for value in terminal_task_traces.values()):
            lines.append(f"- 已终态 task 下仍活跃的 traces: {terminal_task_traces}")
    if top_secretary_actions:
        lines.append(f"- 秘书动作分布: {top_secretary_actions}")
    if top_executor_overrides:
        lines.append(f"- 秘书改派请求: {top_executor_overrides}")
    if top_follow_up_targets:
        lines.append(f"- 最近秘书提醒目标: {top_follow_up_targets}")
    if top_operator_queue_next:
        lines.append(f"- 待处理操作队列: {top_operator_queue_next}")
    if top_operator_resolutions:
        lines.append(f"- 操作处理结果: {top_operator_resolutions}")
    if failure_kinds:
        lines.append(f"- 失败分类分布: {failure_kinds}")
    if delivery_statuses:
        lines.append(f"- 投递状态分布: {delivery_statuses}")
    if delivery_platforms:
        lines.append(f"- 投递失败平台: {delivery_platforms}")
    if dispatch_actions:
        lines.append(f"- 调度动作分布: {dispatch_actions}")
    derived_signals = snapshot.get("derived_signals") if isinstance(snapshot.get("derived_signals"), list) else []
    reconcile_summary = snapshot.get("reconcile_summary") if isinstance(snapshot.get("reconcile_summary"), dict) else {}
    if reconcile_summary:
        lines.append(
            "- 本轮回灌: "
            f"runs {int(reconcile_summary.get('runs_linked') or 0)}/{int(reconcile_summary.get('runs_scanned') or 0)}, "
            f"jobs {int(reconcile_summary.get('jobs_linked') or 0)}/{int(reconcile_summary.get('jobs_scanned') or 0)}, "
            f"delegations {int(reconcile_summary.get('delegations_linked') or 0)}/{int(reconcile_summary.get('delegations_scanned') or 0)}, "
            f"tasks {int(reconcile_summary.get('tasks_terminalized') or 0)}/{int(reconcile_summary.get('tasks_scanned') or 0)}"
        )
    for signal in derived_signals[:limit]:
        lines.append(f"- {signal}")
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
