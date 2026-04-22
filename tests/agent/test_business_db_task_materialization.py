from agent import business_db


def test_create_capability_run_auto_materializes_task_and_binds_channel(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    first = business_db.create_capability_run(
        capability="meeting_minutes",
        title="Feishu auto task",
        goal="Summarize this thread",
        origin={
            "platform": "feishu",
            "chat_id": "oc_group_1",
            "chat_name": "商务群",
            "chat_type": "group",
            "thread_id": "",
        },
        actor_user_id="ou_1",
        session_id="agent:main:feishu:group:oc_group_1:ou_1",
        priority="normal",
        input_data={},
    )

    assert str(first.get("task_id") or "").strip()
    task_id = str(first.get("task_id") or "").strip()
    task = business_db.get_task(task_id)
    assert task is not None
    assert task["source_platform"] == "feishu"
    assert task["source_chat_id"] == "oc_group_1"
    assert task["metadata"]["auto_materialized"] is True
    assert task["metadata"]["materialized_by"] == "create_capability_run"
    bound = business_db.get_channel_task(platform="feishu", chat_id="oc_group_1", thread_id="")
    assert isinstance(bound, dict)
    assert (bound.get("task") or {}).get("task_id") == task_id

    second = business_db.create_capability_run(
        capability="meeting_minutes",
        title="Feishu auto task again",
        goal="Continue summarizing",
        origin={
            "platform": "feishu",
            "chat_id": "oc_group_1",
            "chat_name": "商务群",
            "chat_type": "group",
            "thread_id": "",
        },
        actor_user_id="ou_1",
        session_id="agent:main:feishu:group:oc_group_1:ou_1",
        priority="normal",
        input_data={},
    )

    assert second["task_id"] == task_id
    tasks = business_db.list_tasks(limit=10)
    assert len(tasks) == 1
