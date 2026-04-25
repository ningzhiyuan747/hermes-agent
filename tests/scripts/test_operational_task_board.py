from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "operational_task_board.py"


def load_module():
    spec = importlib.util.spec_from_file_location("operational_task_board_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_snapshot_delegates_to_operational_task_board_service(monkeypatch):
    mod = load_module()
    expected = {"units": [{"unit_id": "run:run-1"}], "counts": {}}
    monkeypatch.setattr(mod, "build_operational_task_snapshot", lambda limit=8: expected)

    snapshot = mod.collect_snapshot(limit=5)

    assert snapshot is expected


def test_build_report_highlights_distributed_task_state():
    mod = load_module()
    snapshot = {
        "tasks": [{"task_id": "task-1", "status": "queued", "title": "Weixin watchdog recovery"}],
        "capability_runs": [{"run_id": "run-1", "status": "queued", "title": "Weixin watchdog recovery"}],
        "background_jobs": [
            {"job_id": "job-1", "status": "failed", "title": "OpenClaw retry"},
            {"job_id": "job-2", "status": "running", "title": "Scope-aware worker"},
        ],
        "delegation_tasks": [{"status": "created", "worker_role": "ops-worker", "goal": "Check watchdog chain"}],
        "counts": {
            "tasks": {"queued": 1},
            "capability_runs": {"queued": 1},
            "background_jobs": {"failed": 1, "running": 1},
            "delegation_tasks": {"created": 1},
        },
        "scope_summary": {
            "task_scopes": {"dingtalk:chat:group-1": 1, "dingtalk:chat:group-2": 1},
            "person_memories": {"dingtalk:user:alice": 1, "dingtalk:user:bob": 1},
            "conversation_roles": {"task_group": 2},
            "secretary_actions": {"retry_delivery": 1},
            "executor_overrides": {"codex": 1},
            "operator_queue_next": {"Switch task to executor 'codex'.": 1},
        },
        "task_truth_summary": {
            "active_tasks": 1,
            "active_tasks_with_active_trace": 1,
            "active_tasks_without_active_trace": 0,
            "active_task_ids_without_active_trace": [],
            "linked_active_traces": {"capability_runs": 1, "background_jobs": 0, "delegation_tasks": 0},
            "orphaned_active_traces": {"capability_runs": 0, "background_jobs": 1, "delegation_tasks": 1},
            "terminal_task_active_traces": {"capability_runs": 0, "background_jobs": 0, "delegation_tasks": 0},
        },
        "task_failure_summary": {
            "failure_kinds": {"routing_failed": 1},
            "delivery_statuses": {"pending": 1, "failed": 1},
            "dispatch_actions": {"continue_current": 1, "retry_delivery": 1},
            "delivery_platforms": {"dingtalk": 1},
        },
        "reconcile_summary": {
            "runs_scanned": 1,
            "runs_linked": 1,
            "jobs_scanned": 2,
            "jobs_linked": 1,
            "delegations_scanned": 1,
            "delegations_linked": 1,
            "tasks_scanned": 3,
            "tasks_terminalized": 2,
        },
        "derived_signals": ["OpenClaw 启动冒烟已成功，旧的启动故障应视为历史阻塞。"],
        "units": [
            {
                "unit_id": "task:task-1",
                "unit_type": "task",
                "status": "queued",
                "is_active": True,
                "failure_kind": "",
                "delivery_status": "pending",
                "recovery_hint": "Wait for the current executor to finish and capture the result.",
                "dispatch_action": "continue_current",
                "suggested_executor": "openclaw",
                "last_secretary_action": "retry_delivery",
                "last_secretary_action_summary": "Retried delivery for job 'job-1' -> delivered.",
                "requested_executor_override": "codex",
                "operator_queue_count": 1,
                "operator_queue_next": "Switch task to executor 'codex'.",
                "title": "Weixin watchdog recovery",
                "task_scope_key": "dingtalk:chat:group-1",
                "person_memory_key": "dingtalk:user:alice",
                "current_focus": "Queued for worker",
                "next_step": "Dispatch worker",
                "related_ids": {"task_id": "task-1"},
            },
            {
                "unit_id": "subagent:deleg-1",
                "unit_type": "delegation_task",
                "status": "created",
                "owner": "ops-worker",
                "title": "Check watchdog chain",
                "task_scope_key": "dingtalk:chat:group-1",
                "person_memory_key": "dingtalk:user:alice",
            },
            {
                "unit_id": "run:run-1",
                "unit_type": "capability_run",
                "status": "queued",
                "title": "Weixin watchdog recovery",
                "task_scope_key": "dingtalk:chat:group-1",
                "person_memory_key": "dingtalk:user:alice",
                "related_ids": {"run_id": "run-1"},
            },
            {"unit_id": "job:job-1", "unit_type": "background_job", "status": "failed", "title": "OpenClaw retry", "related_ids": {"job_id": "job-1"}},
            {"unit_id": "job:job-2", "unit_type": "background_job", "status": "running", "title": "Scope-aware worker", "task_scope_key": "dingtalk:chat:group-2", "person_memory_key": "dingtalk:user:bob", "related_ids": {"job_id": "job-2"}},
            {"unit_id": "task:task-fail-1", "unit_type": "task", "status": "failed", "failure_kind": "routing_failed", "delivery_status": "failed", "dispatch_action": "retry_delivery", "suggested_executor": "openclaw", "title": "Failed task", "task_scope_key": "dingtalk:chat:group-2", "person_memory_key": "dingtalk:user:bob", "related_ids": {"task_id": "task-fail-1"}},
        ],
    }

    report = mod.build_report(snapshot, limit=5)

    assert "统一运行任务总览" in report
    assert "task 层已经作为主记录" in report
    assert "一、任务主记录（tasks）" in report
    assert "总数: 1" in report
    assert "背景任务失败较多（failed=1）" in report
    assert "活跃数: 1" in report
    assert "queued=1" in report
    assert "Queued for worker" in report
    assert "恢复建议: Wait for the current executor to finish and capture the result." in report
    assert "调度: continue_current -> openclaw" in report
    assert "Check watchdog chain | task_scope=dingtalk:chat:group-1" in report
    assert "task_scope=dingtalk:chat:group-1" in report
    assert "person_memory=dingtalk:user:alice" in report
    assert "task_scope=dingtalk:chat:group-2" in report
    assert "person_memory=dingtalk:user:bob" in report
    assert "任务面热点: {'dingtalk:chat:group-1': 1, 'dingtalk:chat:group-2': 1}" in report
    assert "人物记忆热点: {'dingtalk:user:alice': 1, 'dingtalk:user:bob': 1}" in report
    assert "会话角色分布: {'task_group': 2}" in report
    assert "task 真相摘要: active=1, backed=1, unbacked=0" in report
    assert "已挂到 task 主记录的活跃 traces: {'capability_runs': 1, 'background_jobs': 0, 'delegation_tasks': 0}" in report
    assert "游离活跃 traces: {'capability_runs': 0, 'background_jobs': 1, 'delegation_tasks': 1}" in report
    assert "秘书动作分布: {'retry_delivery': 1}" in report
    assert "秘书改派请求: {'codex': 1}" in report
    assert "待处理操作队列: {\"Switch task to executor 'codex'.\": 1}" in report
    assert "失败分类分布: {'routing_failed': 1}" in report
    assert "投递状态分布: {'pending': 1, 'failed': 1}" in report
    assert "投递失败平台: {'dingtalk': 1}" in report
    assert "调度动作分布: {'continue_current': 1, 'retry_delivery': 1}" in report
    assert "本轮回灌: runs 1/1, jobs 1/2, delegations 1/1, tasks 2/3" in report
    assert "OpenClaw 启动冒烟已成功" in report
