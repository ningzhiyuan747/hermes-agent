from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "background_job_worker.py"


def load_module():
    sys.modules.pop("background_job_worker_test", None)
    spec = importlib.util.spec_from_file_location("background_job_worker_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_background_job_worker_loads_openclaw_env_from_hermes_home(monkeypatch, tmp_path):
    env_path = tmp_path / ".env"
    env_path.write_text("HERMES_OPENCLAW_AGENT=dotenv-agent\n", encoding="utf-8")
    monkeypatch.setenv("HERMES_HOME", str(tmp_path))
    monkeypatch.delenv("HERMES_OPENCLAW_AGENT", raising=False)

    mod = load_module()

    assert mod.OPENCLAW_AGENT == "dotenv-agent"


def test_run_openclaw_for_job_uses_popen_pipe_streams(monkeypatch):
    monkeypatch.setenv("HERMES_OPENCLAW_AGENT", "hermes-research")
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


def test_run_one_syncs_capability_run_on_background_job_completion(monkeypatch):
    mod = load_module()
    job_updates: list[dict] = []
    events: list[dict] = []
    capability_updates: list[dict] = []
    deliveries: list[dict] = []
    released: list[str] = []

    job = {
        "job_id": "job-123",
        "title": "demo",
        "status": "running",
        "tags": [
            "executor:openclaw",
            "capability_run:run-123",
            "task_scope:feishu:chat:Group-42:thread:Task-9",
            "person_memory:feishu:user:User-7",
        ],
    }

    monkeypatch.setattr(mod, "claim_next_job", lambda executor: job)
    monkeypatch.setattr(mod, "get_job", lambda job_id: {"job_id": job_id, "status": "running"})
    monkeypatch.setattr(mod, "_run_openclaw_for_job", lambda claimed_job, timeout: (0, "done", ""))
    monkeypatch.setattr(mod, "_deliver_job_result", lambda record, failed=False: deliveries.append({"record": record, "failed": failed}))
    monkeypatch.setattr(mod, "release_job_lock", lambda job_id: released.append(job_id))

    def fake_update_job(job_id, **kwargs):
        record = {"job_id": job_id, **kwargs}
        job_updates.append(record)
        return record

    def fake_append_job_event(job_id, **kwargs):
        events.append({"job_id": job_id, **kwargs})

    def fake_update_capability_run(run_id, **kwargs):
        capability_updates.append({"run_id": run_id, **kwargs})

    monkeypatch.setattr(mod, "update_job", fake_update_job)
    monkeypatch.setattr(mod, "append_job_event", fake_append_job_event)
    monkeypatch.setattr(mod, "update_capability_run", fake_update_capability_run)

    assert mod.run_one(timeout=5, executor="background-job-worker") is True
    assert capability_updates == [
        {
            "run_id": "run-123",
            "status": "completed",
            "current_focus": "Background job completed.",
            "next_step": "Review the stored result delivered from the background worker.",
            "result": "done",
            "blocker": None,
            "output": {
                "background_job_result": "done",
                "background_job_status": "completed",
                "task_scope_key": "feishu:chat:Group-42:thread:Task-9",
                "person_memory_key": "feishu:user:User-7",
            },
        }
    ]
    assert deliveries == [{"record": {"job_id": "job-123", "status": "completed", "current_focus": "Background job completed.", "next_step": "Review the stored result; delivery to the originating chat has been attempted when available.", "result": "done"}, "failed": False}]
    assert released == ["job-123"]
