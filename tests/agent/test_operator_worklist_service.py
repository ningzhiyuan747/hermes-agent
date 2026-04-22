import agent.operator_worklist_service as service


def test_list_operator_worklist_collects_pending_items(monkeypatch):
    monkeypatch.setattr(
        service,
        "list_tasks",
        lambda limit=0: [
            {
                "task_id": "task-1",
                "title": "Task One",
                "metadata": {
                    "operator_queue": {
                        "pending": [
                            {
                                "queue_item_id": "switch_executor:task-1:1",
                                "kind": "switch_executor",
                                "requested_executor": "codex",
                                "route_behavior": "external",
                                "fallback_executor_key": "hermes",
                                "supports_background_job": False,
                                "summary": "Switch task to executor 'codex'.",
                                "created_at_unix": 200,
                            }
                        ]
                    }
                },
            }
        ],
    )

    snapshot = service.list_operator_worklist(limit=5)

    assert snapshot["all_item_count"] == 1
    assert snapshot["items"][0]["task_id"] == "task-1"
    assert snapshot["items"][0]["requested_executor"] == "codex"


def test_complete_operator_queue_item_resolves_pending_item(monkeypatch):
    updates: list[dict] = []
    syncs: list[str] = []
    task = {
        "task_id": "task-2",
        "metadata": {
            "control_plane": {
                "operator_queue_count": 1,
                "operator_queue_next": "Switch task to executor 'codex'.",
            },
            "operator_queue": {
                "pending": [
                    {
                        "queue_item_id": "switch_executor:task-2:1",
                        "kind": "switch_executor",
                        "requested_executor": "codex",
                        "summary": "Switch task to executor 'codex'.",
                        "created_at_unix": 100,
                    }
                ]
            },
        },
    }
    monkeypatch.setattr(service, "get_task", lambda task_id: task)
    monkeypatch.setattr(service, "update_task", lambda task_id, **fields: updates.append({"task_id": task_id, **fields}) or {"task_id": task_id, "metadata": fields.get("metadata", {})})
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: syncs.append(task_id) or {"task": {"task_id": task_id}})

    result = service.complete_operator_queue_item(
        task_id="task-2",
        queue_item_id="switch_executor:task-2:1",
        resolution="completed",
        note="Redispatched manually.",
    )

    assert result["ok"] is True
    assert updates[0]["metadata"]["operator_queue"]["pending"] == []
    assert updates[0]["metadata"]["operator_queue"]["completed"][0]["resolution_note"] == "Redispatched manually."
    assert updates[0]["metadata"]["control_plane"]["operator_queue_count"] == 0
    assert updates[0]["metadata"]["control_plane"]["last_operator_resolution"] == "completed"
    assert syncs == ["task-2"]
