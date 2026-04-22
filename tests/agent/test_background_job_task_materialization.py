from agent import background_jobs
from agent import business_db


def test_background_job_create_auto_materializes_task_and_binds_channel(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    record = background_jobs.create_job(
        title="Background auto task",
        prompt="Investigate the issue",
        origin={
            "platform": "feishu",
            "chat_id": "oc_bg_group_1",
            "chat_name": "异步群",
            "chat_type": "group",
            "thread_id": "",
        },
        session_id="agent:main:feishu:group:oc_bg_group_1:ou_1",
        user_id="ou_1",
        executor="openclaw",
        tags=["executor:openclaw"],
    )

    task_id = str(record.get("task_id") or "").strip()
    assert task_id
    task = business_db.get_task(task_id)
    assert task is not None
    assert task["metadata"]["background_job"] is True
    assert task["metadata"]["materialized_by"] == "background_job.create_job"
    bound = business_db.get_channel_task(platform="feishu", chat_id="oc_bg_group_1", thread_id="")
    assert isinstance(bound, dict)
    assert (bound.get("task") or {}).get("task_id") == task_id

    updated = background_jobs.update_job(
        str(record.get("job_id") or ""),
        status="running",
        current_focus="Async worker picked the job up.",
    )
    assert updated is not None
    assert updated["task_id"] == task_id


def test_background_job_ignores_nonexistent_task_scope_tag_and_materializes_real_task(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    jobs_dir = tmp_path / "background_jobs"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setattr(background_jobs, "jobs_root", lambda: jobs_dir)

    record = background_jobs.create_job(
        title="Invalid task scope tag",
        prompt="Investigate invalid task scope",
        origin={
            "platform": "feishu",
            "chat_id": "oc_bg_group_2",
            "chat_name": "异步群 2",
            "chat_type": "group",
            "thread_id": "",
        },
        session_id="agent:main:feishu:group:oc_bg_group_2:ou_1",
        user_id="ou_1",
        executor="openclaw",
        tags=["executor:openclaw", "task_scope:task:not-a-real-task"],
    )

    task_id = str(record.get("task_id") or "").strip()
    assert task_id
    assert task_id != "not-a-real-task"
    assert business_db.get_task(task_id) is not None
