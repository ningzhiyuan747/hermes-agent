from agent.capability_snapshots import (
    pick_background_job_snapshot,
    pick_capability_run_snapshot,
)


def test_pick_background_job_snapshot_prefers_task_match_and_enriches_run():
    rows = [
        {
            "job_id": "job-1",
            "session_id": "session-a",
            "updated_at_unix": 20,
            "tags": ["executor:openclaw", "capability_run:run-1", "person_memory:feishu:user:job-owner"],
            "status": "active",
            "executor": "openclaw",
            "origin": {"platform": "feishu", "chat_name": "商务"},
        }
    ]
    runs = [
        {
            "run_id": "run-1",
            "background_job_id": "job-1",
            "task_id": "task-1",
            "capability_name": "contract_retrieval",
            "status": "running",
            "trace_id": "trace-1",
            "result": "已命中合同公告",
            "output": {"person_memory_key": "feishu:user:run-owner"},
        }
    ]

    job = pick_background_job_snapshot(
        rows,
        runs,
        session_id="session-a",
        task_id="task-1",
        task_title="司羿合同任务",
    )

    assert job is not None
    assert job["job_id"] == "job-1"
    assert job["capability_run_id"] == "run-1"
    assert job["task_title"] == "司羿合同任务"
    assert job["trace_id"] == "trace-1"
    assert job["person_memory_key"] == "feishu:user:run-owner"


def test_pick_capability_run_snapshot_uses_global_fallback_without_task_binding():
    rows = [
        {
            "run_id": "run-1",
            "session_id": "other-session",
            "status": "running",
            "updated_at_unix": 30,
            "trace_id": "trace-1",
            "input": {"person_memory_key": "feishu:user:user-7"},
        }
    ]

    run = pick_capability_run_snapshot(
        rows,
        session_id="session-a",
        global_fallback=True,
    )

    assert run is not None
    assert run["run_id"] == "run-1"
    assert run["shared_scope"] == "global"
    assert run["person_memory_key"] == "feishu:user:user-7"
