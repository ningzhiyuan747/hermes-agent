from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "secretary_loop.py"


def load_module():
    spec = importlib.util.spec_from_file_location("secretary_loop_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_snapshot_delegates_to_secretary_loop_service(monkeypatch):
    mod = load_module()
    expected = {"actions": [{"action_id": "retry_delivery:task-1"}]}
    monkeypatch.setattr(mod, "build_secretary_loop_snapshot", lambda limit=8: expected)

    snapshot = mod.collect_snapshot(limit=5)

    assert snapshot is expected


def test_build_report_renders_secretary_actions():
    mod = load_module()
    snapshot = {
        "all_action_count": 2,
        "action_counts": {"wait_approval": 1, "retry_delivery": 1},
        "follow_up_counts": {"escalate": 1},
        "follow_up_due_count": 1,
        "escalation_due_count": 1,
        "all_follow_up_item_count": 1,
        "reconcile_summary": {
            "runs_scanned": 1,
            "runs_linked": 1,
            "jobs_scanned": 2,
            "jobs_linked": 1,
            "delegations_scanned": 0,
            "delegations_linked": 0,
            "tasks_scanned": 3,
            "tasks_terminalized": 1,
        },
        "actions": [
            {
                "dispatch_action": "wait_approval",
                "suggested_executor": "openclaw",
                "task_id": "task-1",
                "task_title": "Trace smoke",
                "task_scope_key": "feishu:chat:trace-smoke-chat",
                "person_memory_key": "feishu:user:trace-smoke-user",
                "reason": "Task is pending_approval and waiting for approval resolution.",
                "proposed_operator_action": "Notify the owner/admin to approve or deny the pending task.",
                "recovery_hint": "Resolve the pending approval before dispatch continues.",
                "auto_safe": False,
                "priority": 95,
                "follow_up_due": True,
                "follow_up_level": "escalate",
                "follow_up_summary": "Approval has been pending for 180 minutes; escalate owner/admin on feishu:chat:trace-smoke-chat.",
            },
            {
                "dispatch_action": "retry_delivery",
                "suggested_executor": "openclaw",
                "task_id": "task-2",
                "task_title": "Delivery failed",
                "task_scope_key": "dingtalk:chat:cid_2",
                "person_memory_key": "dingtalk:user:u2",
                "reason": "Delivery failed (routing_failed) while execution output already exists.",
                "proposed_operator_action": "Retry delivery without rerunning the task executor.",
                "recovery_hint": "Repair routing and redeliver.",
                "auto_safe": True,
                "priority": 90,
            },
        ],
        "follow_up_items": [
            {
                "follow_up_level": "escalate",
                "target_ref": "feishu:trace-smoke-chat",
                "target_kind": "origin_chat",
                "task_id": "task-1",
                "task_title": "Trace smoke",
                "summary": "Approval has been pending for 180 minutes; escalate owner/admin on feishu:chat:trace-smoke-chat.",
            }
        ],
    }

    report = mod.build_report(snapshot, limit=5)

    assert "Secretary Loop 动作建议" in report
    assert "动作总数: 2" in report
    assert "动作分布: {'wait_approval': 1, 'retry_delivery': 1}" in report
    assert "催办到期: 1" in report
    assert "升级到期: 1" in report
    assert "催办分布: {'escalate': 1}" in report
    assert "最新回灌: runs 1/1, jobs 1/2, delegations 0/0, tasks 1/3" in report
    assert "wait_approval -> openclaw | task-1 | Trace smoke" in report
    assert "task_scope=feishu:chat:trace-smoke-chat" in report
    assert "原因: Task is pending_approval" in report
    assert "建议: Notify the owner/admin to approve or deny the pending task." in report
    assert "跟进: escalate" in report
    assert "自动安全: no | 优先级: 95" in report
    assert "retry_delivery -> openclaw | task-2 | Delivery failed" in report
    assert "自动安全: yes | 优先级: 90" in report
    assert "提醒草案:" in report
    assert "escalate | feishu:trace-smoke-chat | task-1 | Trace smoke" in report
