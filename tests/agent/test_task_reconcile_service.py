import json

from agent import background_jobs
from agent import business_db
from agent import task_reconcile_service


def test_reconcile_task_records_links_legacy_run_and_job(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    with business_db.connect() as conn:
        cap = conn.execute("SELECT capability_id FROM capabilities WHERE name='meeting_minutes'").fetchone()
        assert cap is not None
        conn.execute(
            """
            INSERT INTO capability_runs(
                run_id, trace_id, capability_id, task_id, title, goal, status, priority,
                origin_json, actor_user_id, session_id, background_job_id, approval_id,
                current_focus, next_step, blocker, result, input_json, output_json,
                created_at_unix, updated_at_unix, started_at_unix, finished_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-legacy-1",
                "trace-legacy-1",
                cap["capability_id"],
                "",
                "Legacy run",
                "Summarize this old thread",
                "failed",
                "normal",
                '{"platform":"feishu","chat_id":"oc_legacy","chat_name":"Legacy","chat_type":"group","thread_id":""}',
                "ou_legacy",
                "agent:main:feishu:group:oc_legacy:ou_legacy",
                "",
                "",
                "",
                "",
                "",
                "",
                "{}",
                "{}",
                100,
                100,
                None,
                None,
            ),
        )
        conn.commit()

    job_id = "job-legacy-1"
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    job_record = {
        "job_id": job_id,
        "trace_id": "trace-legacy-job-1",
        "task_id": "",
        "title": "Legacy job",
        "prompt": "Investigate old job",
        "status": "failed",
        "priority": "normal",
        "tags": ["executor:openclaw"],
        "origin": {
            "platform": "feishu",
            "chat_id": "oc_legacy_job",
            "chat_name": "Legacy Job",
            "chat_type": "group",
            "thread_id": "",
        },
        "session_id": "agent:main:feishu:group:oc_legacy_job:ou_job",
        "user_id": "ou_job",
        "created_at_unix": 100,
        "updated_at_unix": 100,
        "started_at_unix": None,
        "finished_at_unix": None,
        "current_focus": "",
        "next_step": "",
        "blocker": "",
        "result": "",
        "artifact_paths": [],
        "job_dir": str(job_dir),
        "events_path": str(job_dir / "events.jsonl"),
        "executor": "openclaw",
        "runner_pid": None,
        "runner_runtime": "",
    }
    (job_dir / "job.json").write_text(json.dumps(job_record), encoding="utf-8")

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["runs_linked"] >= 1
    assert summary["jobs_linked"] >= 1
    run = business_db.get_capability_run("run-legacy-1")
    assert str(run.get("task_id") or "").strip()
    linked_job = background_jobs.get_job(job_id)
    assert str(linked_job.get("task_id") or "").strip()


def test_reconcile_task_records_links_legacy_delegation_meta_to_parent_task(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    parent_task = business_db.create_task(
        title="Parent task",
        goal="Coordinate a long-running task",
        owner_user_id="ou_parent",
        source_platform="feishu",
        source_chat_id="oc_parent",
        source_thread_id="",
        source_session_id="agent:main:feishu:group:oc_parent:ou_parent",
        metadata={},
    )
    parent_task_id = str(parent_task.get("task_id") or "").strip()
    assert parent_task_id

    task_dir = tmp_path / "delegation_tasks" / "agent_main_feishu_group_oc_parent" / "task-0-demo"
    task_dir.mkdir(parents=True, exist_ok=True)
    meta_path = task_dir / "task-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "goal": "Collect evidence from subagent",
                "status": "completed",
                "parent_session_id": "agent:main:feishu:group:oc_parent:ou_parent",
                "worker_role": "ops-worker",
                "created_at_unix": 100,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        task_reconcile_service,
        "_load_task_meta",
        lambda: [
            {
                "goal": "Collect evidence from subagent",
                "status": "completed",
                "parent_session_id": "agent:main:feishu:group:oc_parent:ou_parent",
                "worker_role": "ops-worker",
                "created_at_unix": 100,
                "_meta_path": str(meta_path),
            }
        ],
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["delegations_linked"] == 1
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    assert stored_meta["control_task_id"] == parent_task_id


def test_reconcile_task_records_materializes_standalone_delegation_task_state(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    task_dir = tmp_path / "delegation_tasks" / "cron-demo" / "task-0-demo"
    task_dir.mkdir(parents=True, exist_ok=True)
    meta_path = task_dir / "task-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "goal": "Collect cron evidence",
                "status": "completed",
                "parent_session_id": "cron-demo",
                "worker_role": "research-worker",
                "platform": "cron",
                "created_at_unix": 100,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        task_reconcile_service,
        "_load_task_meta",
        lambda: [
            {
                "goal": "Collect cron evidence",
                "status": "completed",
                "parent_session_id": "cron-demo",
                "worker_role": "research-worker",
                "platform": "cron",
                "created_at_unix": 100,
                "_meta_path": str(meta_path),
            }
        ],
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["delegations_linked"] == 1
    stored_meta = json.loads(meta_path.read_text(encoding="utf-8"))
    control_task_id = str(stored_meta.get("control_task_id") or "").strip()
    assert control_task_id
    task = business_db.get_task(control_task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["metadata"]["materialized_by"] == "task_reconcile_service.delegation"
    assert task["metadata"]["control_plane"]["current_executor"] == "research-worker"
    assert task["metadata"]["control_plane"]["task_scope_key"] == f"task:{control_task_id}"


def test_reconcile_task_records_refreshes_existing_control_task_state(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    created = business_db.create_task(
        title="Delegation shell",
        goal="Collect cron evidence",
        source_platform="cron",
        source_session_id="cron-demo",
        metadata={
            "materialized_by": "task_reconcile_service.delegation",
            "control_plane": {
                "status": "open",
                "task_scope_key": "task:stale",
            },
        },
    )
    control_task_id = str(created.get("task_id") or "").strip()
    assert control_task_id

    task_dir = tmp_path / "delegation_tasks" / "cron-demo" / "task-0-demo"
    task_dir.mkdir(parents=True, exist_ok=True)
    meta_path = task_dir / "task-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "goal": "Collect cron evidence",
                "status": "completed",
                "parent_session_id": "cron-demo",
                "worker_role": "research-worker",
                "platform": "cron",
                "control_task_id": control_task_id,
                "created_at_unix": 100,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        task_reconcile_service,
        "_load_task_meta",
        lambda: [
            {
                "goal": "Collect cron evidence",
                "status": "completed",
                "parent_session_id": "cron-demo",
                "worker_role": "research-worker",
                "platform": "cron",
                "control_task_id": control_task_id,
                "created_at_unix": 100,
                "_meta_path": str(meta_path),
            }
        ],
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["delegations_scanned"] == 0
    task = business_db.get_task(control_task_id)
    assert task is not None
    assert task["status"] == "completed"
    assert task["metadata"]["control_plane"]["current_executor"] == "research-worker"
    assert task["metadata"]["control_plane"]["task_scope_key"] == f"task:{control_task_id}"


def test_reconcile_task_records_terminalizes_task_from_completed_background_job(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    task = business_db.create_task(
        title="Completed async task",
        goal="Finish async work",
        owner_user_id="ou_job",
        source_platform="feishu",
        source_chat_id="oc_async_done",
        source_session_id="agent:main:feishu:group:oc_async_done:ou_job",
        metadata={},
    )
    task_id = str(task.get("task_id") or "").strip()
    assert task_id

    job_id = "job-legacy-terminal-1"
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "trace_id": "trace-legacy-job-terminal-1",
                "task_id": task_id,
                "title": "Done job",
                "prompt": "Finish the async work",
                "status": "completed",
                "priority": "normal",
                "tags": ["executor:openclaw"],
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_async_done",
                    "chat_name": "Async Done",
                    "chat_type": "group",
                    "thread_id": "",
                },
                "session_id": "agent:main:feishu:group:oc_async_done:ou_job",
                "user_id": "ou_job",
                "created_at_unix": 100,
                "updated_at_unix": 100,
                "started_at_unix": 100,
                "finished_at_unix": 120,
                "current_focus": "Background job completed.",
                "next_step": "Review the stored result.",
                "blocker": "",
                "result": "",
                "artifact_paths": [],
                "job_dir": str(job_dir),
                "events_path": str(job_dir / "events.jsonl"),
                "executor": "openclaw",
                "runner_pid": None,
                "runner_runtime": "",
            }
        ),
        encoding="utf-8",
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["tasks_scanned"] >= 0
    updated_task = business_db.get_task(task_id)
    assert updated_task is not None
    assert updated_task["status"] == "completed"


def test_reconcile_task_records_infers_completed_for_legacy_delegation_with_final_report(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    created = business_db.create_task(
        title="Legacy delegation shell",
        goal="Collect evidence",
        source_platform="cli",
        source_session_id="parent-session",
        metadata={
            "materialized_by": "task_reconcile_service.delegation",
        },
    )
    control_task_id = str(created.get("task_id") or "").strip()
    task_dir = tmp_path / "delegation_tasks" / "parent-session" / "task-0-demo"
    task_dir.mkdir(parents=True, exist_ok=True)
    report_path = task_dir / "final-report.md"
    report_path.write_text("done", encoding="utf-8")
    meta_path = task_dir / "task-meta.json"
    meta_path.write_text(
        json.dumps(
            {
                "goal": "Collect evidence",
                "parent_session_id": "parent-session",
                "worker_role": "bid-worker",
                "control_task_id": control_task_id,
                "final_report_path": str(report_path),
                "created_at_unix": 100,
            }
        ),
        encoding="utf-8",
    )

    monkeypatch.setattr(
        task_reconcile_service,
        "_load_task_meta",
        lambda: [
            {
                "goal": "Collect evidence",
                "parent_session_id": "parent-session",
                "worker_role": "bid-worker",
                "control_task_id": control_task_id,
                "final_report_path": str(report_path),
                "created_at_unix": 100,
                "_meta_path": str(meta_path),
            }
        ],
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["tasks_scanned"] >= 0
    updated_task = business_db.get_task(control_task_id)
    assert updated_task is not None
    assert updated_task["status"] == "completed"
    assert updated_task["metadata"]["control_plane"]["current_executor"] == "bid-worker"


def test_reconcile_task_records_cancels_stale_empty_smoke_task(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    created = business_db.create_task(
        title="feishu task aware smoke",
        goal="",
        source_platform="feishu",
        source_chat_id="",
        source_session_id="",
        metadata={},
    )
    task_id = str(created.get("task_id") or "").strip()
    assert task_id

    with business_db.connect() as conn:
        conn.execute(
            "UPDATE tasks SET created_at_unix=?, updated_at_unix=? WHERE task_id=?",
            (1, 1, task_id),
        )
        conn.commit()

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["tasks_terminalized"] >= 1
    updated_task = business_db.get_task(task_id)
    assert updated_task is not None
    assert updated_task["status"] == "cancelled"
    assert "stale smoke task shell" in updated_task["metadata"]["control_plane"]["current_focus"].lower()


def test_reconcile_task_records_refreshes_failed_terminal_task_control_plane(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    created = business_db.create_task(
        title="Failed delivery task",
        goal="Return the result to Feishu",
        owner_user_id="ou_job",
        source_platform="feishu",
        source_chat_id="oc_failed_delivery",
        source_session_id="agent:main:feishu:group:oc_failed_delivery:ou_job",
        metadata={},
    )
    task_id = str(created.get("task_id") or "").strip()
    assert task_id
    business_db.update_task(task_id, status="failed", metadata={})

    job_id = "job-terminal-failed-1"
    job_dir = jobs_dir / job_id
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": job_id,
                "trace_id": "trace-terminal-failed-1",
                "task_id": task_id,
                "title": "Failed delivery job",
                "prompt": "Return the result to Feishu",
                "status": "failed",
                "priority": "normal",
                "tags": ["executor:openclaw"],
                "origin": {
                    "platform": "feishu",
                    "chat_id": "oc_failed_delivery",
                    "chat_name": "Failed Delivery",
                    "chat_type": "group",
                    "thread_id": "",
                },
                "session_id": "agent:main:feishu:group:oc_failed_delivery:ou_job",
                "user_id": "ou_job",
                "created_at_unix": 100,
                "updated_at_unix": 120,
                "started_at_unix": 100,
                "finished_at_unix": 120,
                "current_focus": "Delivery failed after execution.",
                "next_step": "Repair delivery credentials and retry.",
                "blocker": "Feishu delivery auth is missing.",
                "result": "",
                "artifact_paths": [],
                "job_dir": str(job_dir),
                "events_path": str(job_dir / "events.jsonl"),
                "executor": "openclaw",
                "delivery_error": "app_id or app_secret not found",
                "runner_pid": None,
                "runner_runtime": "",
            }
        ),
        encoding="utf-8",
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["tasks_scanned"] >= 1
    refreshed = business_db.get_task(task_id)
    assert refreshed is not None
    assert refreshed["status"] == "failed"
    assert refreshed["metadata"]["control_plane"]["failure_kind"] == "credential_failed"


def test_reconcile_task_records_terminalizes_queued_run_when_background_job_is_completed(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    task = business_db.create_task(
        title="Queued run shell",
        goal="Follow background completion",
        owner_user_id="ou_job",
        source_platform="dingtalk",
        source_chat_id="cid_demo",
        source_session_id="agent:main:dingtalk:group:cid_demo:ou_job",
        metadata={},
    )
    task_id = str(task.get("task_id") or "").strip()

    with business_db.connect() as conn:
        cap = conn.execute("SELECT capability_id FROM capabilities WHERE name='bid_research'").fetchone()
        conn.execute(
            """
            INSERT INTO capability_runs(
                run_id, trace_id, capability_id, task_id, title, goal, status, priority,
                origin_json, actor_user_id, session_id, background_job_id, approval_id,
                current_focus, next_step, blocker, result, input_json, output_json,
                created_at_unix, updated_at_unix, started_at_unix, finished_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-stale-queued-1",
                "trace-stale-queued-1",
                cap["capability_id"],
                task_id,
                "Queued stale run",
                "Need the background result",
                "queued",
                "normal",
                '{"platform":"dingtalk","chat_id":"cid_demo","chat_type":"group","thread_id":""}',
                "ou_job",
                "agent:main:dingtalk:group:cid_demo:ou_job",
                "job-stale-completed-1",
                "",
                "Waiting for job",
                "Wait",
                "",
                "",
                "{}",
                "{}",
                100,
                100,
                None,
                None,
            ),
        )
        conn.commit()

    job_dir = jobs_dir / "job-stale-completed-1"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job-stale-completed-1",
                "trace_id": "trace-job-stale-completed-1",
                "task_id": task_id,
                "title": "Completed stale job",
                "prompt": "Need the background result",
                "status": "completed",
                "priority": "normal",
                "tags": ["capability_run:run-stale-queued-1"],
                "origin": {"platform": "dingtalk", "chat_id": "cid_demo", "thread_id": ""},
                "session_id": "agent:main:dingtalk:group:cid_demo:ou_job",
                "user_id": "ou_job",
                "created_at_unix": 100,
                "updated_at_unix": 120,
                "started_at_unix": 110,
                "finished_at_unix": 120,
                "current_focus": "Background job completed.",
                "next_step": "Review the result.",
                "blocker": "",
                "result": "done",
                "artifact_paths": [],
                "job_dir": str(job_dir),
                "events_path": str(job_dir / "events.jsonl"),
                "executor": "openclaw-worker",
                "runner_pid": None,
                "runner_runtime": "",
            }
        ),
        encoding="utf-8",
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["runs_terminalized"] >= 1
    run = business_db.get_capability_run("run-stale-queued-1")
    assert run is not None
    assert run["status"] == "completed"
    assert run["result"] == "done"


def test_reconcile_task_records_fails_stale_running_job_with_dead_pid(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)
    monkeypatch.setenv("HERMES_RECONCILE_STALE_RUNNING_JOB_SECONDS", "300")
    monkeypatch.setattr(task_reconcile_service, "_pid_exists", lambda pid: False)

    task = business_db.create_task(
        title="Stale running shell",
        goal="Detect dead worker",
        owner_user_id="ou_job",
        source_platform="dingtalk",
        source_chat_id="cid_demo",
        source_session_id="agent:main:dingtalk:group:cid_demo:ou_job",
        metadata={},
    )
    task_id = str(task.get("task_id") or "").strip()

    with business_db.connect() as conn:
        cap = conn.execute("SELECT capability_id FROM capabilities WHERE name='bid_research'").fetchone()
        conn.execute(
            """
            INSERT INTO capability_runs(
                run_id, trace_id, capability_id, task_id, title, goal, status, priority,
                origin_json, actor_user_id, session_id, background_job_id, approval_id,
                current_focus, next_step, blocker, result, input_json, output_json,
                created_at_unix, updated_at_unix, started_at_unix, finished_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "run-stale-running-1",
                "trace-stale-running-1",
                cap["capability_id"],
                task_id,
                "Running stale run",
                "Detect dead worker",
                "queued",
                "normal",
                '{"platform":"dingtalk","chat_id":"cid_demo","chat_type":"group","thread_id":""}',
                "ou_job",
                "agent:main:dingtalk:group:cid_demo:ou_job",
                "job-stale-running-1",
                "",
                "Waiting for running job",
                "Wait",
                "",
                "",
                "{}",
                "{}",
                100,
                100,
                None,
                None,
            ),
        )
        conn.commit()

    job_dir = jobs_dir / "job-stale-running-1"
    job_dir.mkdir(parents=True, exist_ok=True)
    (job_dir / "job.json").write_text(
        json.dumps(
            {
                "job_id": "job-stale-running-1",
                "trace_id": "trace-job-stale-running-1",
                "task_id": task_id,
                "title": "Dead worker job",
                "prompt": "Detect dead worker",
                "status": "running",
                "priority": "normal",
                "tags": ["capability_run:run-stale-running-1"],
                "origin": {"platform": "dingtalk", "chat_id": "cid_demo", "thread_id": ""},
                "session_id": "agent:main:dingtalk:group:cid_demo:ou_job",
                "user_id": "ou_job",
                "created_at_unix": 100,
                "updated_at_unix": 100,
                "started_at_unix": 100,
                "finished_at_unix": None,
                "current_focus": "Running.",
                "next_step": "Wait.",
                "blocker": "",
                "result": "",
                "artifact_paths": [],
                "job_dir": str(job_dir),
                "events_path": str(job_dir / "events.jsonl"),
                "executor": "openclaw-worker",
                "runner_pid": 303,
                "runner_runtime": "openclaw",
            }
        ),
        encoding="utf-8",
    )

    summary = task_reconcile_service.reconcile_task_records(limit=20)

    assert summary["jobs_failed"] >= 1
    job = background_jobs.get_job("job-stale-running-1")
    run = business_db.get_capability_run("run-stale-running-1")
    assert job is not None
    assert run is not None
    assert job["status"] == "failed"
    assert run["status"] == "failed"
    assert "no longer alive" in str(job.get("blocker") or "").lower()
