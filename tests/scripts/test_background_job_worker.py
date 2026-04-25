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
    assert "OpenClaw 研究交付" in stdout
    assert "任务: demo" in stdout
    assert "Agent: hermes-research" in stdout
    assert "结论\ndone" in stdout
    assert stderr == ""
    assert updates == [
        {"job_id": "job-123", "runner_pid": 4321, "runner_runtime": "openclaw"},
        {"job_id": "job-123", "runner_pid": None, "runner_runtime": ""},
    ]


def test_openclaw_runner_prompt_includes_shared_collaboration_policy(monkeypatch, tmp_path):
    policy_path = tmp_path / "hermes-openclaw-collaboration-policy.md"
    policy_path.write_text("Hermes orchestrates.\nOpenClaw investigates.", encoding="utf-8")
    monkeypatch.setenv("HERMES_COLLABORATION_POLICY_FILE", str(policy_path))
    monkeypatch.setenv("HERMES_OPENCLAW_AGENT", "hermes-research")

    mod = load_module()
    prompt = mod._build_openclaw_runner_prompt({"job_id": "job-1", "title": "demo", "tags": []})

    assert "Hermes orchestrates." in prompt
    assert "OpenClaw investigates." in prompt


def test_format_openclaw_research_report_preserves_sections():
    mod = load_module()

    report = mod._format_openclaw_research_report(
        {"job_id": "job-1", "title": "合同检索", "tags": ["capability:contract_retrieval"]},
        "Outcome\n拿到 1 条高可信线索\n\nEvidence\n- https://example.com/contract.pdf\n\nBest next move\n继续核验附件原件",
        agent="hermes-research",
        provider="openrouter",
        model="gpt-5.4",
        usage_total=321,
    )

    assert "OpenClaw 研究交付" in report
    assert "能力: contract_retrieval" in report
    assert "模型: openrouter / gpt-5.4" in report
    assert "Tokens(total): 321" in report
    assert "结论\n拿到 1 条高可信线索" in report
    assert "证据与来源\n- https://example.com/contract.pdf" in report
    assert "建议下一步\n继续核验附件原件" in report


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
            "conversation_role:chat_surface",
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
                "conversation_role": "chat_surface",
            },
        }
    ]
    assert deliveries == [{"record": {"job_id": "job-123", "status": "completed", "current_focus": "Background job completed.", "next_step": "Review the stored result; delivery to the originating chat has been attempted when available.", "result": "done"}, "failed": False}]
    assert released == ["job-123"]


def test_run_once_batch_respects_requested_concurrency(monkeypatch):
    mod = load_module()
    calls: list[tuple[int, str]] = []

    def fake_run_one(*, timeout: int, executor: str) -> bool:
        calls.append((timeout, executor))
        return len(calls) == 2

    monkeypatch.setattr(mod, "run_one", fake_run_one)

    result = mod._run_once_batch(timeout=9, executor="background-job-worker", concurrency=3)

    assert result is True
    assert calls == [(9, "background-job-worker")] * 3


def test_main_reads_background_job_concurrency_from_env(monkeypatch):
    mod = load_module()
    monkeypatch.setenv("HERMES_BACKGROUND_JOB_CONCURRENCY", "3")
    monkeypatch.setattr(mod, "_run_once_batch", lambda *, timeout, executor, concurrency: concurrency == 3)
    monkeypatch.setattr(sys, "argv", ["background_job_worker.py", "--once"])

    assert mod.main() == 0
