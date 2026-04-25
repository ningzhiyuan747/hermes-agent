import agent.operational_task_board_service as service


def test_build_scope_fields_prefers_platform_chat_and_person_memory_keys():
    scopes = service._build_scope_fields(
        origin={"platform": "dingtalk", "chat_id": "group-42", "thread_id": "task-thread"},
        actor_user_id="user-7",
        task_id="task-99",
    )

    assert scopes["task_scope_key"] == "dingtalk:chat:group-42:thread:task-thread"
    assert scopes["person_memory_key"] == "dingtalk:user:user-7"
    assert scopes["conversation_role"] == "task_group"


def test_build_operational_task_units_normalizes_runs_jobs_and_subagents(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_tasks",
        lambda **kwargs: [
            {
                "task_id": "task-1",
                "title": "Weixin task shell",
                "goal": "Handle Weixin follow-up",
                "status": "queued",
                "owner_user_id": "wx-user-9",
                "source_platform": "weixin",
                "source_chat_id": "wx-1",
                "source_thread_id": "",
                "source_session_id": "sess-1",
                "metadata": {
                    "control_plane": {
                        "status": "queued",
                        "current_executor": "bid_research",
                        "current_focus": "Queued for worker",
                        "next_step": "Dispatch worker",
                        "blocker": "",
                        "failure_kind": "",
                        "execution_status": "queued",
                        "delivery_status": "pending",
                        "recovery_hint": "Wait for the current executor to finish and capture the result.",
                        "dispatch_action": "continue_current",
                        "suggested_executor": "openclaw",
                        "task_scope_key": "weixin:chat:wx-1",
                        "person_memory_key": "weixin:user:wx-user-9",
                        "last_secretary_action": "retry_delivery",
                        "last_secretary_action_summary": "Retried delivery for job 'job-1' -> delivered.",
                        "requested_executor_override": "codex",
                        "last_follow_up_action_id": "wait_approval:task-1",
                        "last_follow_up_ok": True,
                        "last_follow_up_at_unix": 150,
                        "last_follow_up_summary": "Approval has been pending.",
                        "last_follow_up_target_ref": "weixin:wx-1",
                        "operator_queue_count": 1,
                        "operator_queue_next": "Switch task to executor 'codex'.",
                    }
                },
                "updated_at_unix": 160,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda **kwargs: [
            {
                "run_id": "run-1",
                "session_id": "run-session-1",
                "status": "queued",
                "title": "Weixin follow-up",
                "capability_name": "bid_research",
                "current_focus": "Queued for worker",
                "next_step": "Dispatch worker",
                "blocker": "TypeError: Popen.__init__() got an unexpected keyword argument 'capture_output'",
                "task_id": "task-1",
                "background_job_id": "job-1",
                "approval_id": "",
                "actor_user_id": "wx-user-9",
                "origin": {"platform": "weixin", "chat_id": "wx-1"},
                "updated_at_unix": 100,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda **kwargs: [
            {
                "job_id": "job-1",
                "status": "failed",
                "title": "OpenClaw retry",
                "current_focus": "Background job crashed.",
                "next_step": "Inspect traceback",
                "blocker": "network",
                "session_id": "sess-1",
                "executor": "openclaw-worker",
                "updated_at_unix": 120,
                "tags": [
                    "capability_run:run-1",
                    "task_scope:dingtalk:chat:ops-group",
                    "person_memory:dingtalk:user:alice",
                ],
                "delivery_error": "RuntimeError: Feishu send failed: app_id or app_secret not found",
            },
            {
                "job_id": "job-3",
                "status": "queued",
                "title": "Task scoped executor",
                "current_focus": "queued",
                "next_step": "start",
                "blocker": "",
                "session_id": "sess-3",
                "executor": "codex-worker",
                "updated_at_unix": 140,
                "tags": [
                    "task_scope:task:task-1",
                    "person_memory:user:operator-1",
                ],
            },
            {
                "job_id": "job-2",
                "status": "completed",
                "title": "openclaw launch regression smoke",
                "current_focus": "done",
                "next_step": "",
                "blocker": "",
                "session_id": "sess-2",
                "executor": "openclaw-worker",
                "updated_at_unix": 150,
                "tags": [],
                "result": "OPENCLAW_LAUNCH_OK",
                "delivery_error": "RuntimeError: Could not resolve 'origin-chat' on cli. no delivery target resolved for deliver=origin",
            },
        ],
    )
    monkeypatch.setattr(
        service,
        "_load_task_meta",
        lambda: [
            {
                "status": "created",
                "goal": "Check watchdog chain",
                "worker_role": "ops-worker",
                "created_at_unix": 130,
                "started_at_unix": 130,
                "task_id": "deleg-1",
                "parent_session_id": "sess-1",
            }
        ],
    )

    snapshot = service.build_operational_task_snapshot(limit=5)

    assert snapshot["counts"]["tasks"]["queued"] == 1
    assert snapshot["counts"]["capability_runs"]["queued"] == 1
    assert snapshot["counts"]["background_jobs"]["failed"] == 1
    assert snapshot["counts"]["background_jobs"]["completed"] == 1
    assert snapshot["counts"]["background_jobs"]["queued"] == 1
    assert snapshot["counts"]["delegation_tasks"]["created"] == 1
    assert len(snapshot["units"]) == 6
    assert snapshot["scope_summary"]["task_scopes"] == {
        "task:task-1": 1,
        "dingtalk:chat:ops-group": 1,
        "weixin:chat:wx-1": 3,
    }
    assert snapshot["scope_summary"]["person_memories"] == {
        "dingtalk:user:alice": 1,
        "user:operator-1": 1,
        "weixin:user:wx-user-9": 3,
    }
    assert snapshot["scope_summary"]["conversation_roles"] == {
        "task_group": 1,
        "chat_surface": 3,
        "task_unit": 1,
    }
    assert snapshot["scope_summary"]["origin_platforms"] == {
        "dingtalk": 1,
        "task": 1,
        "weixin": 3,
    }
    assert snapshot["scope_summary"]["secretary_actions"] == {"retry_delivery": 1}
    assert snapshot["scope_summary"]["executor_overrides"] == {"codex": 1}
    assert snapshot["scope_summary"]["follow_up_targets"] == {"weixin:wx-1": 1}
    assert snapshot["scope_summary"]["operator_queue_next"] == {"Switch task to executor 'codex'.": 1}
    assert len(snapshot["derived_signals"]) == 3
    assert "OpenClaw 启动冒烟已成功" in snapshot["derived_signals"][0]
    assert "飞书投递凭证仍未就绪" in snapshot["derived_signals"][1]
    assert "deliver=origin" in snapshot["derived_signals"][2]

    first = snapshot["units"][0]
    assert first["unit_id"] == "task:task-1"
    assert first["unit_type"] == "task"
    assert first["current_focus"] == "Queued for worker"
    assert first["next_step"] == "Dispatch worker"
    assert first["failure_kind"] == ""
    assert first["execution_status"] == "queued"
    assert first["delivery_status"] == "pending"
    assert first["recovery_hint"] == "Wait for the current executor to finish and capture the result."
    assert first["dispatch_action"] == "continue_current"
    assert first["suggested_executor"] == "openclaw"
    assert first["last_secretary_action"] == "retry_delivery"
    assert first["last_follow_up_action_id"] == "wait_approval:task-1"
    assert first["last_follow_up_target_ref"] == "weixin:wx-1"
    assert first["requested_executor_override"] == "codex"
    assert first["operator_queue_count"] == 1
    assert first["operator_queue_next"] == "Switch task to executor 'codex'."
    assert first["related_ids"]["source_session_id"] == "sess-1"
    assert first["related_ids"]["owner_user_id"] == "wx-user-9"
    assert first["related_ids"]["source_thread_id"] == ""

    run_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "run:run-1")
    assert run_unit["origin_platform"] == "weixin"
    assert run_unit["related_ids"]["background_job_id"] == "job-1"
    assert run_unit["related_ids"]["session_id"] == "run-session-1"
    assert run_unit["task_scope_key"] == "weixin:chat:wx-1"
    assert run_unit["person_memory_key"] == "weixin:user:wx-user-9"
    assert run_unit["conversation_role"] == "chat_surface"

    task_job_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "job:job-3")
    assert task_job_unit["task_scope_key"] == "task:task-1"
    assert task_job_unit["person_memory_key"] == "user:operator-1"
    assert task_job_unit["conversation_role"] == "task_unit"
    assert task_job_unit["origin_platform"] == "task"

    job_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "job:job-1")
    assert job_unit["related_ids"]["capability_run_id"] == "run-1"
    assert job_unit["related_ids"]["session_id"] == "sess-1"
    assert job_unit["owner"] == "sess-1"
    assert job_unit["task_scope_key"] == "dingtalk:chat:ops-group"
    assert job_unit["person_memory_key"] == "dingtalk:user:alice"
    assert job_unit["conversation_role"] == "task_group"
    assert job_unit["origin_platform"] == "dingtalk"

    subagent_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "subagent:deleg-1")
    assert subagent_unit["task_scope_key"] == "weixin:chat:wx-1"
    assert subagent_unit["person_memory_key"] == "weixin:user:wx-user-9"
    assert subagent_unit["conversation_role"] == "chat_surface"
    assert subagent_unit["origin_platform"] == "weixin"
    assert subagent_unit["related_ids"]["parent_session_id"] == "sess-1"


def test_subagent_scope_fields_falls_back_to_control_task(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "metadata": {
                "control_plane": {
                    "task_scope_key": "task:task-parent-1",
                    "person_memory_key": "feishu:user:ou_parent",
                }
            },
        },
    )

    fields = service._subagent_scope_fields(
        {
            "control_task_id": "task-parent-1",
            "parent_session_id": "",
        },
        parent_scopes={},
    )

    assert fields["task_scope_key"] == "task:task-parent-1"
    assert fields["person_memory_key"] == "feishu:user:ou_parent"
    assert fields["conversation_role"] == "task_unit"


def test_task_unit_surfaces_failure_kind_from_control_plane():
    unit = service._task_unit(
        {
            "task_id": "task-fail-1",
            "title": "Failed task",
            "goal": "Recover",
            "status": "failed",
            "owner_user_id": "ou_1",
            "source_platform": "feishu",
            "source_chat_id": "oc_fail",
            "source_thread_id": "",
            "source_session_id": "sess-fail-1",
            "metadata": {
                "control_plane": {
                    "status": "failed",
                    "execution_status": "completed",
                    "delivery_status": "failed",
                    "current_executor": "openclaw",
                    "current_focus": "Delivery failed.",
                    "next_step": "Fix routing.",
                    "blocker": "No delivery target resolved.",
                    "failure_kind": "routing_failed",
                    "recovery_hint": "Repair the delivery target mapping and redeliver the existing result.",
                    "dispatch_action": "retry_delivery",
                    "suggested_executor": "openclaw",
                    "task_scope_key": "feishu:chat:oc_fail",
                    "person_memory_key": "feishu:user:ou_1",
                }
            },
            "updated_at_unix": 200,
        }
    )

    assert unit["status"] == "failed"
    assert unit["failure_kind"] == "routing_failed"
    assert unit["execution_status"] == "completed"
    assert unit["delivery_status"] == "failed"
    assert unit["recovery_hint"] == "Repair the delivery target mapping and redeliver the existing result."
    assert unit["dispatch_action"] == "retry_delivery"
    assert unit["suggested_executor"] == "openclaw"
