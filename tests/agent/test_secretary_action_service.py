import agent.secretary_action_service as service


def test_execute_secretary_action_retries_failed_delivery(monkeypatch):
    updates: list[dict] = []
    delivered: list[dict] = []
    task_metadata_updates: list[dict] = []
    task_ref = {
        "task_id": "task-1",
        "metadata": {
            "control_plane": {
                "dispatch_action": "retry_delivery",
                "recovery_hint": "Retry delivery without rerunning the executor.",
            }
        },
    }

    monkeypatch.setattr(
        service,
        "sync_task_control_state",
        lambda task_id: {"task": task_ref},
    )
    monkeypatch.setattr(service, "get_task", lambda task_id: None)
    monkeypatch.setattr(
        service,
        "list_jobs",
        lambda limit=0, active_only=False: [
            {
                "job_id": "job-1",
                "task_id": "task-1",
                "status": "completed",
                "delivery_status": "failed",
                "delivery_error": "RuntimeError: route failed",
                "updated_at_unix": 200,
            }
        ],
    )

    def fake_update_job(job_id: str, **fields):
        record = {"job_id": job_id, **fields}
        updates.append(record)
        return {
            "job_id": job_id,
            "status": "completed",
            "delivery_status": fields.get("delivery_status", "failed"),
            "delivery_error": fields.get("delivery_error", ""),
        }

    monkeypatch.setattr(service, "update_job", fake_update_job)
    monkeypatch.setattr(service, "get_job", lambda job_id: {"job_id": job_id, "status": "completed"})
    monkeypatch.setattr(
        service,
        "update_task",
        lambda task_id, **fields: task_metadata_updates.append({"task_id": task_id, **fields}) or {
            "task_id": task_id,
            "metadata": fields.get("metadata", {}),
        },
    )

    def fake_deliver(job: dict, *, failed: bool = False, force: bool = False):
        delivered.append({"job": dict(job), "failed": failed, "force": force})
        return {
            "job_id": str(job.get("job_id") or ""),
            "status": "completed",
            "delivery_status": "delivered",
            "delivery_error": "",
        }

    monkeypatch.setattr(service, "deliver_job_result", fake_deliver)

    result = service.execute_secretary_action(task_id="task-1", action="retry_delivery", auto_safe_only=True)

    assert result["ok"] is True
    assert updates == [
        {
            "job_id": "job-1",
            "delivery_status": "retrying",
            "delivery_error": "",
            "delivered_at_unix": 0,
        }
    ]
    assert delivered == [
        {
            "job": {
                "job_id": "job-1",
                "status": "completed",
                "delivery_status": "retrying",
                "delivery_error": "",
            },
            "failed": False,
            "force": True,
        }
    ]
    assert task_metadata_updates[0]["metadata"]["secretary_action"]["last_action"]["action"] == "retry_delivery"
    assert task_metadata_updates[0]["metadata"]["control_plane"]["last_secretary_action"] == "retry_delivery"
    assert result["details"]["delivery_status"] == "delivered"


def test_execute_secretary_action_rejects_non_auto_safe_action(monkeypatch):
    result = service.execute_secretary_action(task_id="task-1", action="switch_executor", auto_safe_only=True)

    assert result["ok"] is False
    assert "not marked auto_safe" in result["message"]


def test_execute_secretary_action_rejects_stale_dispatch_action(monkeypatch):
    monkeypatch.setattr(
        service,
        "sync_task_control_state",
        lambda task_id: {
            "task": {
                "task_id": task_id,
                "metadata": {
                    "control_plane": {
                        "dispatch_action": "wait_approval",
                    }
                },
            }
        },
    )

    result = service.execute_secretary_action(task_id="task-1", action="retry_delivery", auto_safe_only=True)

    assert result["ok"] is False
    assert "no longer requires 'retry_delivery'" in result["message"]


def test_execute_secretary_action_records_manual_switch_executor(monkeypatch):
    task_updates: list[dict] = []
    task_ref = {
        "task_id": "task-2",
        "metadata": {
            "control_plane": {
                "dispatch_action": "switch_executor",
                "suggested_executor": "codex",
                "recovery_hint": "Retry execution or switch to another executor.",
            }
        },
    }
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": task_ref})
    monkeypatch.setattr(service, "get_task", lambda task_id: task_ref)
    monkeypatch.setattr(
        service,
        "update_task",
        lambda task_id, **fields: task_updates.append({"task_id": task_id, **fields}) or {
            "task_id": task_id,
            "metadata": fields.get("metadata", {}),
        },
    )

    result = service.execute_secretary_action(task_id="task-2", action="switch_executor", auto_safe_only=False)

    assert result["ok"] is True
    assert result["details"]["requested_executor"] == "codex"
    assert task_updates[0]["metadata"]["secretary_action"]["last_action"]["requested_executor"] == "codex"
    assert task_updates[0]["metadata"]["control_plane"]["requested_executor_override"] == "codex"
    assert task_updates[1]["metadata"]["operator_queue"]["pending"][0]["requested_executor"] == "codex"
    assert task_updates[1]["metadata"]["control_plane"]["operator_queue_count"] == 1
