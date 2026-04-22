import agent.control_tower_service as service


def test_build_control_tower_snapshot_combines_board_secretary_and_operator(monkeypatch):
    monkeypatch.setattr(service.time, "time", lambda: 2_000)
    monkeypatch.setattr(
        service,
        "build_operational_task_snapshot",
        lambda limit=0: {
            "counts": {
                "tasks": {
                    "completed": 3,
                    "pending_approval": 2,
                    "failed": 1,
                }
            },
            "units": [
                {
                    "unit_id": "task:task-1",
                    "unit_type": "task",
                    "related_ids": {"task_id": "task-1"},
                    "last_follow_up_action_id": "wait_approval:task-1",
                    "last_follow_up_target_ref": "feishu:oc_1",
                    "last_follow_up_at_unix": 1_500,
                    "last_follow_up_ok": True,
                },
                {
                    "unit_id": "task:task-2",
                    "unit_type": "task",
                    "related_ids": {"task_id": "task-2"},
                },
            ],
            "derived_signals": ["signal-a"],
            "reconcile_summary": {"tasks_scanned": 4},
        },
    )
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "all_action_count": 3,
            "action_counts": {"wait_approval": 2, "retry_delivery": 1},
            "follow_up_counts": {"nudge": 1},
            "follow_up_due_count": 1,
            "escalation_due_count": 0,
            "all_follow_up_item_count": 1,
            "actions": [
                {
                    "action_id": "wait_approval:task-1",
                    "dispatch_action": "wait_approval",
                    "auto_safe": False,
                    "follow_up_due": True,
                    "escalation_due": False,
                    "task_id": "task-1",
                    "task_title": "Need approval",
                },
                {"dispatch_action": "retry_delivery", "auto_safe": True, "task_id": "task-2", "task_title": "Retry delivery"},
            ],
            "follow_up_items": [
                {
                    "item_id": "follow-up:wait_approval:task-1",
                    "action_id": "wait_approval:task-1",
                    "target_kind": "origin_chat",
                    "target_ref": "feishu:oc_1",
                    "task_id": "task-1",
                    "summary": "Nudge owner/admin.",
                }
            ],
        },
    )
    monkeypatch.setattr(
        service,
        "list_operator_worklist",
        lambda limit=0: {
            "all_item_count": 1,
            "items": [
                {"queue_item_id": "switch_executor:task-3:1", "task_id": "task-3", "summary": "Switch task to executor 'codex'."}
            ],
        },
    )

    snapshot = service.build_control_tower_snapshot(limit=5)

    assert snapshot["summary"]["active_task_count"] == 2
    assert snapshot["summary"]["secretary_action_count"] == 3
    assert snapshot["summary"]["auto_safe_action_count"] == 1
    assert snapshot["summary"]["manual_action_count"] == 1
    assert snapshot["summary"]["follow_up_due_count"] == 1
    assert snapshot["summary"]["escalation_due_count"] == 0
    assert snapshot["summary"]["follow_up_item_count"] == 1
    assert snapshot["summary"]["reminded_follow_up_count"] == 1
    assert snapshot["summary"]["cooling_follow_up_count"] == 1
    assert snapshot["summary"]["unsent_follow_up_count"] == 0
    assert snapshot["summary"]["operator_queue_count"] == 1
    assert snapshot["secretary"]["action_counts"] == {"wait_approval": 2, "retry_delivery": 1}
    assert snapshot["secretary"]["follow_up_counts"] == {"nudge": 1}
    assert snapshot["secretary"]["follow_up_actions"][0]["task_id"] == "task-1"
    assert snapshot["secretary"]["follow_up_items"][0]["target_ref"] == "feishu:oc_1"
    assert snapshot["secretary"]["reminded_follow_up_items"][0]["task_id"] == "task-1"
    assert snapshot["secretary"]["cooling_follow_up_items"][0]["task_id"] == "task-1"
    assert snapshot["operator_worklist"]["items"][0]["task_id"] == "task-3"
