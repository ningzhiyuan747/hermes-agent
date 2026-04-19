from agent import approval_scope_service as service


def test_list_scoped_approvals_prefers_task_binding(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_channel_task",
        lambda platform="", chat_id="", thread_id="": {"task": {"task_id": "task-1", "title": "任务 A"}},
    )
    monkeypatch.setattr(
        service,
        "list_approvals",
        lambda status="pending", limit=0: [
            {"approval_id": "approval-1", "payload": {"task_id": "task-1"}},
            {"approval_id": "approval-2", "payload": {"task_id": "task-2"}},
        ],
    )

    rows, task_id, task_title = service.list_scoped_approvals(
        platform="feishu",
        chat_id="chat-1",
        scope_all=False,
        status="pending",
    )

    assert [row["approval_id"] for row in rows] == ["approval-1"]
    assert task_id == "task-1"
    assert task_title == "任务 A"


def test_list_scoped_approvals_falls_back_to_origin_scope(monkeypatch):
    monkeypatch.setattr(service, "get_channel_task", lambda platform="", chat_id="", thread_id="": {})
    monkeypatch.setattr(
        service,
        "list_approvals",
        lambda status="pending", limit=0: [
            {"approval_id": "approval-1", "payload": {"origin": {"platform": "feishu", "chat_id": "chat-1"}}},
            {"approval_id": "approval-2", "payload": {"origin": {"platform": "dingtalk", "chat_id": "chat-1"}}},
        ],
    )

    rows, task_id, task_title = service.list_scoped_approvals(
        platform="feishu",
        chat_id="chat-1",
        scope_all=False,
        status="pending",
    )

    assert [row["approval_id"] for row in rows] == ["approval-1"]
    assert task_id == ""
    assert task_title == ""
