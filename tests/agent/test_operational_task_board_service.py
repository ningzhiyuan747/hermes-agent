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
                "blocker": "",
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
                "tags": ["capability_run:run-1"],
            }
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
    assert snapshot["counts"]["delegation_tasks"]["created"] == 1
    assert len(snapshot["units"]) == 3

    first = snapshot["units"][0]
    assert first["unit_id"] == "subagent:deleg-1"
    assert first["unit_type"] == "delegation_task"

    run_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "run:run-1")
    assert run_unit["origin_platform"] == "weixin"
    assert run_unit["related_ids"]["background_job_id"] == "job-1"
    assert run_unit["task_scope_key"] == "weixin:chat:wx-1"
    assert run_unit["person_memory_key"] == "weixin:user:wx-user-9"
    assert run_unit["conversation_role"] == "chat_surface"

    job_unit = next(unit for unit in snapshot["units"] if unit["unit_id"] == "job:job-1")
    assert job_unit["related_ids"]["capability_run_id"] == "run-1"
    assert job_unit["owner"] == "sess-1"
