from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "operational_task_board.py"


def load_module():
    spec = importlib.util.spec_from_file_location("operational_task_board_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_collect_snapshot_delegates_to_operational_task_board_service(monkeypatch):
    mod = load_module()
    expected = {"units": [{"unit_id": "run:run-1"}], "counts": {}}
    monkeypatch.setattr(mod, "build_operational_task_snapshot", lambda limit=8: expected)

    snapshot = mod.collect_snapshot(limit=5)

    assert snapshot is expected


def test_build_report_highlights_distributed_task_state():
    mod = load_module()
    snapshot = {
        "capability_runs": [{"run_id": "run-1", "status": "queued", "title": "Weixin watchdog recovery"}],
        "background_jobs": [{"job_id": "job-1", "status": "failed", "title": "OpenClaw retry"}],
        "delegation_tasks": [{"status": "created", "worker_role": "ops-worker", "goal": "Check watchdog chain"}],
        "counts": {
            "capability_runs": {"queued": 1},
            "background_jobs": {"failed": 1},
            "delegation_tasks": {"created": 1},
        },
        "units": [
            {"unit_id": "subagent:deleg-1", "unit_type": "delegation_task", "status": "created", "owner": "ops-worker", "title": "Check watchdog chain"},
            {
                "unit_id": "run:run-1",
                "unit_type": "capability_run",
                "status": "queued",
                "title": "Weixin watchdog recovery",
                "task_scope_key": "dingtalk:chat:group-1",
                "person_memory_key": "dingtalk:user:alice",
                "related_ids": {"run_id": "run-1"},
            },
            {"unit_id": "job:job-1", "unit_type": "background_job", "status": "failed", "title": "OpenClaw retry"},
        ],
    }

    report = mod.build_report(snapshot, limit=5)

    assert "统一运行任务总览" in report
    assert "状态源仍分散在 capability runs / background jobs / delegation tasks 三层" in report
    assert "背景任务失败较多（failed=1）" in report
    assert "queued=1" in report
    assert "task_scope=dingtalk:chat:group-1" in report
    assert "person_memory=dingtalk:user:alice" in report
