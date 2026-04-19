from agent import capability_status_service as service


def test_get_background_job_snapshot_uses_shared_task_binding(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=True: [
            {
                "job_id": "job-1",
                "tags": ["executor:openclaw", "person_memory:feishu:user:user-7"],
                "session_id": "sess-1",
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [{"run_id": "run-1", "background_job_id": "job-1", "task_id": "task-1"}],
    )
    monkeypatch.setattr(
        service,
        "get_channel_task",
        lambda platform="", chat_id="", thread_id="": {"task": {"task_id": "task-1", "title": "任务 A"}},
    )
    monkeypatch.setattr(
        service,
        "list_capability_artifacts",
        lambda run_id, limit=0: [{"kind": "deliverable", "label": "结果", "summary": "ok"}],
    )

    result = service.get_background_job_snapshot(
        session_id="sess-1",
        platform="feishu",
        chat_id="chat-1",
    )

    assert result is not None
    assert result["job_id"] == "job-1"
    assert result["task_id"] == "task-1"
    assert result["capability_run_id"] == "run-1"
    assert result["person_memory_key"] == "feishu:user:user-7"


def test_get_capability_run_snapshot_supports_task_binding_without_session_match(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [
            {"run_id": "run-1", "session_id": "other", "task_id": "task-1", "status": "running", "updated_at_unix": 10}
        ],
    )
    monkeypatch.setattr(
        service,
        "get_channel_task",
        lambda platform="", chat_id="", thread_id="": {"task": {"task_id": "task-1", "title": "任务 A"}},
    )
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    result = service.get_capability_run_snapshot(
        session_id="sess-1",
        platform="dingtalk",
        chat_id="chat-1",
        global_fallback=True,
    )

    assert result is not None
    assert result["run_id"] == "run-1"
    assert result["task_id"] == "task-1"
    assert result["task_title"] == "任务 A"


def test_cancel_background_jobs_cancels_task_bound_openclaw_job(monkeypatch):
    updated_jobs = []
    appended_events = []
    updated_runs = []

    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=True: [
            {
                "job_id": "job-1",
                "session_id": "sess-1",
                "tags": ["executor:openclaw"],
                "runner_pid": None,
                "updated_at_unix": 10,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [{"run_id": "run-1", "background_job_id": "job-1", "task_id": "task-1"}],
    )
    monkeypatch.setattr(
        service,
        "get_channel_task",
        lambda platform="", chat_id="", thread_id="": {"task": {"task_id": "task-1", "title": "任务 A"}},
    )
    monkeypatch.setattr(service, "update_job", lambda job_id, **fields: updated_jobs.append((job_id, fields)))
    monkeypatch.setattr(service, "append_job_event", lambda job_id, **fields: appended_events.append((job_id, fields)))
    monkeypatch.setattr(service, "update_capability_run", lambda run_id, **fields: updated_runs.append((run_id, fields)))

    result = service.cancel_background_jobs(
        session_id="sess-1",
        platform="feishu",
        chat_id="chat-1",
        cancel_label="Feishu control command",
        cancel_source="feishu_control",
    )

    assert result["cancelled"] is True
    assert result["count"] == 1
    assert updated_jobs[0][0] == "job-1"
    assert updated_runs[0][0] == "run-1"
    assert appended_events[0][0] == "job-1"


def test_cancel_background_jobs_uses_global_fallback_for_latest_job_without_task_binding(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=True: [
            {
                "job_id": "job-1",
                "session_id": "other",
                "tags": ["executor:openclaw"],
                "runner_pid": None,
                "updated_at_unix": 10,
            },
            {
                "job_id": "job-2",
                "session_id": "other",
                "tags": ["executor:openclaw"],
                "runner_pid": None,
                "updated_at_unix": 20,
            },
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [
            {"run_id": "run-1", "background_job_id": "job-1", "task_id": "task-1"},
            {"run_id": "run-2", "background_job_id": "job-2", "task_id": "task-1"},
        ],
    )
    monkeypatch.setattr(service, "get_channel_task", lambda platform="", chat_id="", thread_id="": {})
    updated_jobs = []
    monkeypatch.setattr(service, "update_job", lambda job_id, **fields: updated_jobs.append(job_id))
    monkeypatch.setattr(service, "append_job_event", lambda job_id, **fields: None)
    monkeypatch.setattr(service, "update_capability_run", lambda run_id, **fields: None)

    result = service.cancel_background_jobs(
        session_id="sess-x",
        platform="dingtalk",
        chat_id="chat-1",
        global_fallback=True,
        cancel_label="DingTalk control command",
        cancel_source="dingtalk_control",
    )

    assert result["cancelled"] is True
    assert result["count"] == 1
    assert updated_jobs == ["job-2"]


def test_get_background_job_snapshot_prefers_task_scope_key_over_session_match(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=True: [
            {
                "job_id": "job-1",
                "session_id": "sess-1",
                "tags": ["executor:openclaw", "task_scope:feishu:chat:other-chat"],
                "updated_at_unix": 10,
            },
            {
                "job_id": "job-2",
                "session_id": "other-session",
                "tags": ["executor:openclaw", "task_scope:feishu:chat:chat-1:thread:topic-1"],
                "updated_at_unix": 20,
            },
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [
            {"run_id": "run-1", "background_job_id": "job-1", "task_id": "", "status": "running"},
            {"run_id": "run-2", "background_job_id": "job-2", "task_id": "", "status": "running"},
        ],
    )
    monkeypatch.setattr(service, "get_channel_task", lambda platform="", chat_id="", thread_id="": {})
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    result = service.get_background_job_snapshot(
        session_id="sess-1",
        platform="feishu",
        chat_id="chat-1",
        thread_id="topic-1",
    )

    assert result is not None
    assert result["job_id"] == "job-2"
    assert result["task_scope_key"] == "feishu:chat:chat-1:thread:topic-1"


def test_get_capability_run_snapshot_prefers_task_scope_key_from_run_payload(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [
            {
                "run_id": "run-1",
                "session_id": "sess-1",
                "task_id": "",
                "status": "running",
                "updated_at_unix": 10,
                "input": {"task_scope_key": "feishu:chat:other-chat"},
            },
            {
                "run_id": "run-2",
                "session_id": "other-session",
                "task_id": "",
                "status": "running",
                "updated_at_unix": 20,
                "output": {
                    "task_scope_key": "feishu:chat:chat-1:thread:topic-1",
                    "person_memory_key": "feishu:user:user-7",
                },
            },
        ],
    )
    monkeypatch.setattr(service, "get_channel_task", lambda platform="", chat_id="", thread_id="": {})
    monkeypatch.setattr(service, "list_capability_artifacts", lambda run_id, limit=0: [])

    result = service.get_capability_run_snapshot(
        session_id="sess-1",
        platform="feishu",
        chat_id="chat-1",
        thread_id="topic-1",
    )

    assert result is not None
    assert result["run_id"] == "run-2"
    assert result["task_scope_key"] == "feishu:chat:chat-1:thread:topic-1"
    assert result["person_memory_key"] == "feishu:user:user-7"


def test_cancel_background_jobs_prefers_task_scope_key_over_session_match(monkeypatch):
    updated_jobs = []
    updated_runs = []

    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=True: [
            {
                "job_id": "job-1",
                "session_id": "sess-1",
                "tags": ["executor:openclaw", "task_scope:feishu:chat:other-chat"],
                "runner_pid": None,
                "updated_at_unix": 10,
            },
            {
                "job_id": "job-2",
                "session_id": "other-session",
                "tags": ["executor:openclaw", "task_scope:feishu:chat:chat-1:thread:topic-1"],
                "runner_pid": None,
                "updated_at_unix": 20,
            },
        ],
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda status="", limit=0: [
            {"run_id": "run-1", "background_job_id": "job-1", "task_id": "", "input": {}},
            {"run_id": "run-2", "background_job_id": "job-2", "task_id": "", "output": {}},
        ],
    )
    monkeypatch.setattr(service, "get_channel_task", lambda platform="", chat_id="", thread_id="": {})
    monkeypatch.setattr(service, "update_job", lambda job_id, **fields: updated_jobs.append(job_id))
    monkeypatch.setattr(service, "append_job_event", lambda job_id, **fields: None)
    monkeypatch.setattr(service, "update_capability_run", lambda run_id, **fields: updated_runs.append(run_id))

    result = service.cancel_background_jobs(
        session_id="sess-1",
        platform="feishu",
        chat_id="chat-1",
        thread_id="topic-1",
        cancel_label="Feishu control command",
        cancel_source="feishu_control",
    )

    assert result["cancelled"] is True
    assert result["count"] == 1
    assert updated_jobs == ["job-2"]
    assert updated_runs == ["run-2"]
