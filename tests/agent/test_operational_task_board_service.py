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
        "list_capability_runs",
        lambda **kwargs: [
            {
                "run_id": "run-1",
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
            }
        ],
    )

    snapshot = service.build_operational_task_snapshot(limit=2)

    assert snapshot["counts"]["capability_runs"]["queued"] == 1
    assert snapshot["counts"]["background_jobs"]["failed"] == 1
    assert snapshot["counts"]["background_jobs"]["completed"] == 1
    assert snapshot["counts"]["background_jobs"]["queued"] == 1
    assert snapshot["counts"]["delegation_tasks"]["created"] == 1
    assert len(snapshot["units"]) == 5
    assert snapshot["scope_summary"]["task_scopes"] == {
        "task:task-1": 1,
        "dingtalk:chat:ops-group": 1,
    }
    assert snapshot["scope_summary"]["person_memories"] == {
        "dingtalk:user:alice": 1,
        "user:operator-1": 1,
    }
    assert snapshot["scope_summary"]["conversation_roles"] == {
        "task_group": 1,
        "task_unit": 1,
    }
    assert snapshot["scope_summary"]["origin_platforms"] == {
        "dingtalk": 1,
        "task": 1,
    }
    assert len(snapshot["derived_signals"]) == 3
    assert "OpenClaw 启动冒烟已成功" in snapshot["derived_signals"][0]
    assert "飞书投递凭证仍未就绪" in snapshot["derived_signals"][1]
    assert "deliver=origin" in snapshot["derived_signals"][2]

    first = snapshot["units"][0]
    assert first["unit_id"] == "job:job-2"
    assert first["unit_type"] == "background_job"

    run_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "run:run-1")
    assert run_unit["origin_platform"] == "weixin"
    assert run_unit["related_ids"]["background_job_id"] == "job-1"
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
    assert job_unit["owner"] == "sess-1"
    assert job_unit["task_scope_key"] == "dingtalk:chat:ops-group"
    assert job_unit["person_memory_key"] == "dingtalk:user:alice"
    assert job_unit["conversation_role"] == "task_group"
    assert job_unit["origin_platform"] == "dingtalk"
