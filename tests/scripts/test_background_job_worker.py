from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "background_job_worker.py"


def load_module():
    spec = importlib.util.spec_from_file_location("background_job_worker_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_run_openclaw_for_job_uses_popen_pipe_streams(monkeypatch):
    mod = load_module()
    updates: list[dict] = []

    class FakeProcess:
        def __init__(
            self,
            cmd,
            *,
            cwd=None,
            start_new_session=None,
            stdout=None,
            stderr=None,
            text=None,
            encoding=None,
            errors=None,
        ):
            self.cmd = cmd
            self.pid = 4321
            self.returncode = 0
            self.kwargs = {
                "cwd": cwd,
                "start_new_session": start_new_session,
                "stdout": stdout,
                "stderr": stderr,
                "text": text,
                "encoding": encoding,
                "errors": errors,
            }

        def communicate(self, timeout=None):
            return ('{"payloads":[{"text":"done"}]}', "")

    def fake_update_job(job_id, **kwargs):
        updates.append({"job_id": job_id, **kwargs})

    monkeypatch.setattr(mod.subprocess, "Popen", FakeProcess)
    monkeypatch.setattr(mod, "update_job", fake_update_job)

    job = {"job_id": "job-123", "title": "demo"}
    returncode, stdout, stderr = mod._run_openclaw_for_job(job, timeout=5)

    assert returncode == 0
    assert stdout == "done\n\nOpenClaw agent: hermes-research"
    assert stderr == ""
    assert updates == [
        {"job_id": "job-123", "runner_pid": 4321, "runner_runtime": "openclaw"},
        {"job_id": "job-123", "runner_pid": None, "runner_runtime": ""},
    ]
