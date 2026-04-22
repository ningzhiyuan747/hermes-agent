from agent import task_panel_service as service


def test_get_task_panel_snapshot_aggregates_run_job_approval_and_artifacts(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 A",
            "status": "open",
            "goal": "完成任务 A",
        },
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda task_id="", limit=0: [
            {
                "run_id": "run-1",
                "status": "running",
                "background_job_id": "job-1",
                "task_id": task_id,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_approvals",
        lambda status="", limit=0: [
            {"approval_id": "approval-1", "target_id": "run-1", "payload": {"task_id": "task-1"}}
        ],
    )
    monkeypatch.setattr(service, "get_job", lambda job_id: {"job_id": job_id, "status": "running", "executor": "openclaw"})
    monkeypatch.setattr(service, "list_jobs", lambda limit=0, active_only=False: [])
    monkeypatch.setattr(
        service,
        "list_capability_artifacts",
        lambda run_id, limit=0: [{"artifact_id": "artifact-1", "label": "结果", "kind": "file"}],
    )

    snapshot = service.get_task_panel_snapshot("task-1", active_only=False)

    assert snapshot is not None
    assert snapshot["task"]["task_id"] == "task-1"
    assert snapshot["current_run"]["run_id"] == "run-1"
    assert snapshot["current_job"]["job_id"] == "job-1"
    assert snapshot["approvals"][0]["approval_id"] == "approval-1"
    assert snapshot["artifact_items"][0]["artifact_id"] == "artifact-1"
    assert snapshot["control_summary"]["status"] == "pending_approval"
    assert snapshot["control_summary"]["current_executor"] == "openclaw"
    assert snapshot["control_summary"]["task_scope_key"] == ""


def test_sync_task_control_state_writes_derived_status_and_metadata(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 B",
            "status": "open",
            "goal": "完成任务 B",
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda task_id="", limit=0: [
            {
                "run_id": "run-2",
                "status": "running",
                "background_job_id": "job-2",
                "task_id": task_id,
                "capability_name": "ops_recovery",
                "current_focus": "Scanning logs",
                "next_step": "Summarize issues",
                "input": {
                    "task_scope_key": "feishu:chat:group-1",
                    "person_memory_key": "feishu:user:user-1",
                },
            }
        ],
    )
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(
        service,
        "get_job",
        lambda job_id: {
            "job_id": job_id,
            "status": "running",
            "executor": "openclaw",
            "current_focus": "Background scanning",
            "next_step": "Report back",
            "tags": [
                "task_scope:feishu:chat:group-1",
                "person_memory:feishu:user:user-1",
            ],
        },
    )
    monkeypatch.setattr(service, "list_jobs", lambda limit=0, active_only=False: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])
    captured: dict[str, object] = {}

    def _fake_update_task(task_id: str, **fields):
        captured["task_id"] = task_id
        captured["fields"] = fields
        return {
            "task_id": task_id,
            "title": "任务 B",
            "status": fields.get("status"),
            "goal": "完成任务 B",
            "metadata": fields.get("metadata"),
        }

    monkeypatch.setattr(service, "update_task", _fake_update_task)

    snapshot = service.sync_task_control_state("task-2")

    assert snapshot is not None
    assert captured["task_id"] == "task-2"
    fields = captured["fields"]
    assert fields["status"] == "running"
    assert fields["metadata"]["control_plane"]["current_executor"] == "openclaw"
    assert fields["metadata"]["control_plane"]["current_focus"] == "Background scanning"
    assert fields["metadata"]["control_plane"]["execution_status"] == "running"
    assert fields["metadata"]["control_plane"]["delivery_status"] == "pending"
    assert fields["metadata"]["control_plane"]["dispatch_action"] == "continue_current"
    assert fields["metadata"]["control_plane"]["suggested_executor"] == "openclaw"
    assert fields["metadata"]["control_plane"]["task_scope_key"] == "feishu:chat:group-1"


def test_get_task_panel_snapshot_falls_back_to_task_scoped_background_job(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 C",
            "status": "open",
            "goal": "完成任务 C",
            "metadata": {},
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=False: [
            {
                "job_id": "job-task-only",
                "task_id": "task-3",
                "status": "running",
                "executor": "openclaw",
                "current_focus": "Task-scoped background execution",
                "next_step": "Return the result",
                "tags": [
                    "task_scope:task:task-3",
                    "person_memory:user:operator-3",
                ],
            }
        ],
    )
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-3", active_only=False)

    assert snapshot is not None
    assert snapshot["current_job"]["job_id"] == "job-task-only"
    assert snapshot["control_summary"]["status"] == "running"
    assert snapshot["control_summary"]["current_executor"] == "openclaw"
    assert snapshot["control_summary"]["task_scope_key"] == "task:task-3"


def test_get_task_panel_snapshot_surfaces_last_secretary_action(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务秘书",
            "status": "failed",
            "goal": "恢复任务",
            "metadata": {
                "control_plane": {
                    "dispatch_action": "switch_executor",
                    "suggested_executor": "codex",
                },
                "secretary_action": {
                    "last_action": {
                        "action": "retry_delivery",
                        "ok": True,
                        "recorded_at_unix": 321,
                        "summary": "Retried delivery for job 'job-9' -> delivered.",
                        "requested_executor": "codex",
                    }
                },
                "operator_queue": {
                    "pending": [
                        {
                            "kind": "switch_executor",
                            "summary": "Switch task to executor 'codex'.",
                        }
                    ]
                },
            },
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(service, "list_jobs", lambda limit=0, active_only=False: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-secretary", active_only=False)

    assert snapshot is not None
    assert snapshot["control_summary"]["last_secretary_action"] == "retry_delivery"
    assert snapshot["control_summary"]["last_secretary_action_ok"] is True
    assert snapshot["control_summary"]["requested_executor_override"] == "codex"
    assert snapshot["control_summary"]["operator_queue_count"] == 1
    assert snapshot["control_summary"]["operator_queue_next"] == "Switch task to executor 'codex'."


def test_get_task_panel_snapshot_surfaces_last_secretary_follow_up(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务提醒",
            "status": "pending_approval",
            "goal": "继续审批",
            "metadata": {
                "secretary_follow_up": {
                    "last_item": {
                        "action_id": "wait_approval:task-follow-up",
                        "target_ref": "feishu:oc_123",
                        "summary": "Approval has been pending.",
                        "ok": True,
                        "recorded_at_unix": 456,
                    }
                }
            },
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(service, "list_jobs", lambda limit=0, active_only=False: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-follow-up", active_only=False)

    assert snapshot is not None
    assert snapshot["control_summary"]["last_follow_up_action_id"] == "wait_approval:task-follow-up"
    assert snapshot["control_summary"]["last_follow_up_target_ref"] == "feishu:oc_123"
    assert snapshot["control_summary"]["last_follow_up_summary"] == "Approval has been pending."


def test_get_task_panel_snapshot_prefers_active_delegation_for_control_summary(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 D",
            "status": "open",
            "goal": "完成任务 D",
            "source_session_id": "agent:main:feishu:group:oc_task_d:ou_9",
            "metadata": {},
        },
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda task_id="", limit=0: [
            {
                "run_id": "run-4",
                "status": "running",
                "background_job_id": "job-4",
                "task_id": task_id,
                "capability_name": "ops_recovery",
                "current_focus": "Capability running",
                "next_step": "Collect worker output",
            }
        ],
    )
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(
        service,
        "get_job",
        lambda job_id: {
            "job_id": job_id,
            "status": "running",
            "executor": "openclaw",
            "current_focus": "Background running",
            "next_step": "Wait",
            "tags": ["task_scope:feishu:chat:oc_task_d", "person_memory:feishu:user:ou_9"],
        },
    )
    monkeypatch.setattr(service, "list_jobs", lambda limit=0, active_only=False: [])
    monkeypatch.setattr(
        service,
        "_load_task_meta",
        lambda: [
            {
                "task_id": "deleg-4",
                "control_task_id": "task-4",
                "parent_session_id": "agent:main:feishu:group:oc_task_d:ou_9",
                "status": "running",
                "worker_role": "ops-worker",
                "current_focus": "Delegation worker is checking logs.",
                "next_step": "Return a concise diagnosis.",
                "task_scope_key": "feishu:chat:oc_task_d",
                "person_memory_key": "feishu:user:ou_9",
            }
        ],
    )
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-4", active_only=False)

    assert snapshot is not None
    assert snapshot["current_delegation"]["task_id"] == "deleg-4"
    assert snapshot["control_summary"]["status"] == "running"
    assert snapshot["control_summary"]["current_executor"] == "ops-worker"
    assert snapshot["control_summary"]["current_focus"] == "Delegation worker is checking logs."
    assert snapshot["control_summary"]["next_step"] == "Return a concise diagnosis."
    assert snapshot["control_summary"]["task_scope_key"] == "feishu:chat:oc_task_d"


def test_task_control_summary_classifies_delivery_credential_failure(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 E",
            "status": "open",
            "goal": "完成任务 E",
            "metadata": {},
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=False: [
            {
                "job_id": "job-cred-fail",
                "task_id": "task-5",
                "status": "failed",
                "executor": "openclaw",
                "current_focus": "Delivery failed.",
                "next_step": "Fix credentials and retry.",
                "blocker": "",
                "delivery_error": "RuntimeError: Feishu send failed: app_id or app_secret not found",
                "tags": [],
            }
        ],
    )
    monkeypatch.setattr(service, "_load_task_meta", lambda: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-5", active_only=False)

    assert snapshot is not None
    assert snapshot["control_summary"]["status"] == "failed"
    assert snapshot["control_summary"]["failure_kind"] == "credential_failed"
    assert snapshot["control_summary"]["execution_status"] == "failed"
    assert snapshot["control_summary"]["delivery_status"] == "failed"
    assert snapshot["control_summary"]["recovery_hint"] == "Repair platform credentials and redeliver the existing result."
    assert snapshot["control_summary"]["dispatch_action"] == "retry_delivery"
    assert snapshot["control_summary"]["suggested_executor"] == "openclaw"


def test_task_control_summary_separates_completed_execution_from_failed_delivery(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 F",
            "status": "completed",
            "goal": "完成任务 F",
            "metadata": {},
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=False: [
            {
                "job_id": "job-delivery-fail",
                "task_id": "task-6",
                "status": "completed",
                "delivery_status": "failed",
                "delivery_error": "RuntimeError: Could not resolve 'origin-chat' on cli. no delivery target resolved for deliver=origin",
                "executor": "openclaw",
                "current_focus": "Execution finished.",
                "next_step": "Retry delivery only.",
                "blocker": "",
                "tags": [],
            }
        ],
    )
    monkeypatch.setattr(service, "_load_task_meta", lambda: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-6", active_only=False)

    assert snapshot is not None
    assert snapshot["control_summary"]["status"] == "completed"
    assert snapshot["control_summary"]["execution_status"] == "completed"
    assert snapshot["control_summary"]["delivery_status"] == "failed"
    assert snapshot["control_summary"]["failure_kind"] == "routing_failed"
    assert snapshot["control_summary"]["recovery_hint"] == "Repair the delivery target mapping and redeliver the existing result."
    assert snapshot["control_summary"]["dispatch_action"] == "retry_delivery"
    assert snapshot["control_summary"]["suggested_executor"] == "openclaw"


def test_task_control_summary_switches_executor_after_execution_failure(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 G",
            "status": "failed",
            "goal": "完成任务 G",
            "metadata": {},
        },
    )
    monkeypatch.setattr(service, "list_capability_runs", lambda task_id="", limit=0: [])
    monkeypatch.setattr(service, "list_approvals", lambda status="", limit=0: [])
    monkeypatch.setattr(service, "get_job", lambda job_id: None)
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=False: [
            {
                "job_id": "job-exec-fail",
                "task_id": "task-7",
                "status": "failed",
                "executor": "openclaw-worker",
                "current_focus": "Execution failed.",
                "next_step": "Retry with another executor.",
                "blocker": "subprocess timeout while running worker",
                "delivery_error": "",
                "tags": [],
            }
        ],
    )
    monkeypatch.setattr(service, "_load_task_meta", lambda: [])
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    snapshot = service.get_task_panel_snapshot("task-7", active_only=False)

    assert snapshot is not None
    assert snapshot["control_summary"]["failure_kind"] == "infra_failed"
    assert snapshot["control_summary"]["dispatch_action"] == "switch_executor"
    assert snapshot["control_summary"]["suggested_executor"] == "hermes"
