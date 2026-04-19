from agent import task_panel_service as service


def test_get_task_panel_snapshot_aggregates_run_job_approval_and_artifacts(monkeypatch):
    monkeypatch.setattr(
        service,
        "get_task",
        lambda task_id: {
            "task_id": task_id,
            "title": "任务 A",
            "status": "open",
            "goal": "完成任务 A",
        },
    )
    monkeypatch.setattr(
        service,
        "list_capability_runs",
        lambda task_id="", limit=0: [
            {
                "run_id": "run-1",
                "status": "running",
                "background_job_id": "job-1",
                "task_id": task_id,
            }
        ],
    )
    monkeypatch.setattr(
        service,
        "list_approvals",
        lambda status="", limit=0: [
            {"approval_id": "approval-1", "target_id": "run-1", "payload": {"task_id": "task-1"}}
        ],
    )
    monkeypatch.setattr(service, "get_job", lambda job_id: {"job_id": job_id, "status": "running"})
    monkeypatch.setattr(
        service,
        "list_capability_artifacts",
        lambda run_id, limit=0: [{"artifact_id": "artifact-1", "label": "结果", "kind": "file"}],
    )

    snapshot = service.get_task_panel_snapshot("task-1", active_only=False)

    assert snapshot is not None
    assert snapshot["task"]["task_id"] == "task-1"
    assert snapshot["current_run"]["run_id"] == "run-1"
    assert snapshot["current_job"]["job_id"] == "job-1"
    assert snapshot["approvals"][0]["approval_id"] == "approval-1"
    assert snapshot["artifact_items"][0]["artifact_id"] == "artifact-1"
