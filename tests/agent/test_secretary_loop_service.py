import agent.secretary_loop_service as service


def test_plan_secretary_actions_extracts_actionable_task_units():
    now = 10_000
    original_time = service.time.time
    service.time.time = lambda: now
    try:
        snapshot = {
            "units": [
                {
                    "unit_id": "task:task-1",
                    "unit_type": "task",
                    "status": "failed",
                    "dispatch_action": "retry_delivery",
                    "suggested_executor": "openclaw",
                    "failure_kind": "credential_failed",
                    "task_scope_key": "feishu:chat:oc_1",
                    "person_memory_key": "feishu:user:ou_1",
                    "title": "Repair delivery",
                    "recovery_hint": "Repair credentials and redeliver.",
                    "current_focus": "Delivery failed.",
                    "next_step": "Retry delivery.",
                    "updated_at_unix": now - 60,
                    "related_ids": {"task_id": "task-1"},
                },
                {
                    "unit_id": "task:task-2",
                    "unit_type": "task",
                    "status": "pending_approval",
                    "dispatch_action": "wait_approval",
                    "suggested_executor": "hermes",
                    "failure_kind": "",
                    "task_scope_key": "dingtalk:chat:cid_2",
                    "person_memory_key": "dingtalk:user:u2",
                    "title": "Approval task",
                    "recovery_hint": "Resolve the pending approval before dispatch continues.",
                    "updated_at_unix": now - 3 * 60 * 60,
                    "related_ids": {"task_id": "task-2"},
                },
                {
                    "unit_id": "task:task-3",
                    "unit_type": "task",
                    "status": "running",
                    "dispatch_action": "continue_current",
                    "suggested_executor": "openclaw",
                    "title": "No action task",
                    "updated_at_unix": now - 10,
                    "related_ids": {"task_id": "task-3"},
                },
            ]
        }

        actions = service.plan_secretary_actions(snapshot)
    finally:
        service.time.time = original_time

    assert [item["action_id"] for item in actions] == [
        "wait_approval:task-2",
        "retry_delivery:task-1",
    ]
    assert actions[0]["priority"] == 95
    assert actions[0]["auto_safe"] is False
    assert actions[0]["follow_up_due"] is True
    assert actions[0]["escalation_due"] is True
    assert actions[0]["follow_up_level"] == "escalate"
    assert actions[1]["priority"] == 90
    assert actions[1]["auto_safe"] is True
    assert actions[1]["follow_up_due"] is False


def test_build_secretary_loop_snapshot_counts_actions(monkeypatch):
    now = 10_000
    monkeypatch.setattr(service.time, "time", lambda: now)
    monkeypatch.setattr(
        service,
        "build_operational_task_snapshot",
        lambda limit=0: {
            "generated_at_unix": 123,
            "derived_signals": ["signal-a"],
            "reconcile_summary": {"tasks_scanned": 2, "tasks_terminalized": 1},
            "units": [
                {
                    "unit_id": "task:task-1",
                    "unit_type": "task",
                    "status": "failed",
                    "dispatch_action": "switch_executor",
                    "suggested_executor": "hermes",
                    "failure_kind": "infra_failed",
                    "title": "Executor swap",
                    "updated_at_unix": now - 2 * 60 * 60 - 5,
                    "related_ids": {"task_id": "task-1"},
                },
                {
                    "unit_id": "task:task-2",
                    "unit_type": "task",
                    "status": "failed",
                    "dispatch_action": "retry_delivery",
                    "suggested_executor": "openclaw",
                    "failure_kind": "routing_failed",
                    "title": "Delivery retry",
                    "updated_at_unix": now - 90,
                    "related_ids": {"task_id": "task-2"},
                },
            ],
        },
    )

    snapshot = service.build_secretary_loop_snapshot(limit=8)

    assert snapshot["board_generated_at_unix"] == 123
    assert snapshot["all_action_count"] == 2
    assert snapshot["action_counts"] == {"retry_delivery": 1, "switch_executor": 1}
    assert snapshot["follow_up_counts"] == {"escalate": 1}
    assert snapshot["follow_up_due_count"] == 1
    assert snapshot["escalation_due_count"] == 1
    assert snapshot["all_follow_up_item_count"] == 1
    assert snapshot["follow_up_items"][0]["target_kind"] == "operator_queue"
    assert snapshot["follow_up_items"][0]["follow_up_level"] == "escalate"
    assert snapshot["derived_signals"] == ["signal-a"]


def test_plan_secretary_actions_ignores_terminal_switch_executor_noise():
    now = 10_000
    original_time = service.time.time
    service.time.time = lambda: now
    try:
        snapshot = {
            "units": [
                {
                    "unit_id": "task:task-cancelled",
                    "unit_type": "task",
                    "status": "cancelled",
                    "dispatch_action": "switch_executor",
                    "suggested_executor": "hermes",
                    "updated_at_unix": now - 10_000,
                    "related_ids": {"task_id": "task-cancelled"},
                },
                {
                    "unit_id": "task:task-failed",
                    "unit_type": "task",
                    "status": "failed",
                    "dispatch_action": "switch_executor",
                    "suggested_executor": "codex",
                    "updated_at_unix": now - 10_000,
                    "related_ids": {"task_id": "task-failed"},
                },
            ]
        }
        actions = service.plan_secretary_actions(snapshot)
    finally:
        service.time.time = original_time

    assert [item["task_id"] for item in actions] == ["task-failed"]
