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


def test_build_report_highlights_distributed_task_state():
    mod = load_module()
    snapshot = {
        "capability_runs": [{"run_id": "run-1", "status": "queued", "title": "Weixin watchdog recovery"}],
        "background_jobs": [{"job_id": "job-1", "status": "failed", "title": "OpenClaw retry"}],
        "subagent_tasks": [{"status": "created", "worker_role": "ops-worker", "goal": "Check watchdog chain"}],
        "run_counts": {"queued": 1},
        "job_counts": {"failed": 1},
        "subagent_counts": {"created": 1},
        "active_runs": [{"run_id": "run-1", "status": "queued", "title": "Weixin watchdog recovery"}],
        "active_jobs": [],
        "active_subagents": [{"status": "created", "worker_role": "ops-worker", "goal": "Check watchdog chain"}],
    }

    report = mod.build_report(snapshot, limit=5)

    assert "统一运行任务总览" in report
    assert "状态源仍分散在 capability runs / background jobs / delegation tasks 三层" in report
    assert "背景任务失败较多（failed=1）" in report
    assert "queued=1" in report
