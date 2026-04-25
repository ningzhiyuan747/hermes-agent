from __future__ import annotations

from agent import background_jobs as mod


def test_summarize_jobs_counts_status_and_runtime(monkeypatch):
    monkeypatch.setenv("HERMES_BACKGROUND_JOB_CONCURRENCY", "3")
    monkeypatch.setattr(
        mod,
        "iter_jobs",
        lambda: [
            {"job_id": "job-1", "status": "running", "runner_runtime": "openclaw", "executor": "openclaw-worker"},
            {"job_id": "job-2", "status": "queued", "runner_runtime": "", "executor": "background-job-worker"},
            {"job_id": "job-3", "status": "running", "runner_runtime": "hermes", "executor": "background-job-worker"},
            {"job_id": "job-4", "status": "failed", "runner_runtime": "", "executor": "background-job-worker"},
            {"job_id": "job-5", "status": "completed", "runner_runtime": "", "executor": "background-job-worker"},
        ],
    )

    summary = mod.summarize_jobs()

    assert summary["total"] == 5
    assert summary["active"] == 3
    assert summary["queued"] == 1
    assert summary["running"] == 2
    assert summary["failed"] == 1
    assert summary["completed"] == 1
    assert summary["configured_concurrency"] == 3
    assert summary["available_slots"] == 1
    assert summary["runtime_counts"] == {"openclaw": 1, "hermes": 1}


def test_render_jobs_status_includes_numeric_summary(monkeypatch):
    monkeypatch.setenv("HERMES_BACKGROUND_JOB_CONCURRENCY", "3")
    rows = [
        {
            "job_id": "job-1",
            "status": "running",
            "runner_runtime": "openclaw",
            "executor": "openclaw-worker",
            "title": "研究任务",
            "updated_at_unix": 1,
            "created_at_unix": 1,
            "current_focus": "Running OpenClaw.",
            "next_step": "Wait.",
            "blocker": "",
        },
        {
            "job_id": "job-2",
            "status": "queued",
            "runner_runtime": "",
            "executor": "background-job-worker",
            "title": "文档任务",
            "updated_at_unix": 1,
            "created_at_unix": 1,
            "current_focus": "Queued.",
            "next_step": "Start.",
            "blocker": "",
        },
    ]
    monkeypatch.setattr(mod, "iter_jobs", lambda: rows)
    monkeypatch.setattr(mod, "list_jobs", lambda status="", limit=10, active_only=False: rows)

    text = mod.render_jobs_status(limit=5)

    assert "总数: 2" in text
    assert "排队: 1" in text
    assert "运行中: 1" in text
    assert "后台并发: 3" in text
    assert "运行中分布: OpenClaw:1" in text
    assert "运行时: openclaw | 执行器: openclaw-worker" in text
