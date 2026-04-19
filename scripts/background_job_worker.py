#!/usr/bin/env python3
"""Run queued Hermes background jobs.

First version: a conservative one-shot/loop worker that claims queued jobs,
runs `hermes chat` with a job-specific prompt, and stores the final result.
Delivery back to DingTalk/Feishu is intentionally separate so execution state
stays reliable before notification routing is attached.
"""

from __future__ import annotations

import argparse
import json
import os
import shlex
import signal
import subprocess
import sys
import time
import re
from pathlib import Path

REPO_ROOT = Path(__file__).resolve().parents[1]
if str(REPO_ROOT) not in sys.path:
    sys.path.insert(0, str(REPO_ROOT))

from hermes_constants import get_hermes_home
from hermes_cli.env_loader import load_hermes_dotenv
from agent.background_jobs import (
    append_job_event,
    claim_next_job,
    get_job,
    release_job_lock,
    update_job,
)
from agent.outbound_delivery import DeliveryTarget, send_text_to_target
from agent.user_profile_distiller import distill_recent_users

try:
    from agent.business_db import update_capability_run
except Exception:
    update_capability_run = None  # type: ignore[assignment]


_HERMES_HOME = get_hermes_home()
load_hermes_dotenv(hermes_home=_HERMES_HOME, project_env=REPO_ROOT / ".env")


MAX_DELIVERY_CHARS = int(os.getenv("HERMES_BACKGROUND_JOB_DELIVERY_CHARS", "3200"))
OPENCLAW_AGENT = str(os.getenv("HERMES_OPENCLAW_AGENT", "hermes-research") or "hermes-research").strip()
OPENCLAW_THINKING = str(os.getenv("HERMES_OPENCLAW_THINKING", "medium") or "medium").strip()
_ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]")
try:
    _OPENCLAW_AGENT_BY_KIND_RAW = json.loads(
        os.getenv(
            "HERMES_OPENCLAW_AGENT_BY_KIND",
            '{"research":"hermes-research","sales":"hermes-sales","operations":"hermes-ops"}',
        )
    )
except Exception:
    _OPENCLAW_AGENT_BY_KIND_RAW = {
        "research": "hermes-research",
        "sales": "hermes-sales",
        "operations": "hermes-ops",
    }
OPENCLAW_AGENT_BY_KIND = (
    {
        str(key).strip().lower(): str(value).strip()
        for key, value in _OPENCLAW_AGENT_BY_KIND_RAW.items()
        if str(key).strip() and str(value).strip()
    }
    if isinstance(_OPENCLAW_AGENT_BY_KIND_RAW, dict)
    else {
        "research": "hermes-research",
        "sales": "hermes-sales",
        "operations": "hermes-ops",
    }
)


def _job_tags(job: dict) -> set[str]:
    return {
        str(item).strip().lower()
        for item in (job.get("tags") or [])
        if str(item).strip()
    }


def _capability_run_id(job: dict) -> str:
    for tag in _job_tags(job):
        if tag.startswith("capability_run:"):
            return tag.split(":", 1)[1].strip()
    return ""


def _scope_payload_from_job(job: dict) -> dict[str, str]:
    payload: dict[str, str] = {}
    task_scope_key = _tag_value(job, "task_scope")
    person_memory_key = _tag_value(job, "person_memory")
    conversation_role = _tag_value(job, "conversation_role")
    if task_scope_key:
        payload["task_scope_key"] = task_scope_key
    if person_memory_key:
        payload["person_memory_key"] = person_memory_key
    if conversation_role:
        payload["conversation_role"] = conversation_role
    return payload


def _sync_capability_run_status(job: dict, *, status: str, current_focus: str, next_step: str, result: str = "", blocker: str = "") -> None:
    run_id = _capability_run_id(job)
    if not run_id or update_capability_run is None:
        return
    payload = {"background_job_status": status, **_scope_payload_from_job(job)}
    if result:
        payload["background_job_result"] = result
    if blocker:
        payload["background_job_blocker"] = blocker
    try:
        update_capability_run(
            run_id,
            status=status,
            current_focus=current_focus,
            next_step=next_step,
            result=result or None,
            blocker=blocker or None,
            output=payload,
        )
    except Exception:
        pass


def _requested_runtime(job: dict) -> str:
    tags = _job_tags(job)
    if "executor:openclaw" in tags or "runtime:openclaw" in tags:
        return "openclaw"
    return "hermes"


def _capability_from_job(job: dict) -> str:
    for tag in _job_tags(job):
        if tag.startswith("capability:"):
            return tag.split(":", 1)[1].strip().lower()
    return ""


def _worker_kind_from_job(job: dict) -> str:
    for tag in _job_tags(job):
        if tag.startswith("worker_kind:"):
            return tag.split(":", 1)[1].strip().lower()
    return ""


def _tag_value(job: dict, prefix: str) -> str:
    needle = str(prefix or "").strip().lower() + ":"
    for raw_tag in (job.get("tags") or []):
        tag = str(raw_tag or "").strip()
        if tag.lower().startswith(needle):
            return tag[len(needle):].strip()
    return ""


def _resolve_openclaw_agent(job: dict) -> str:
    explicit = _tag_value(job, "openclaw_agent")
    if explicit:
        return explicit
    worker_kind = _worker_kind_from_job(job)
    if worker_kind:
        env_name = f"HERMES_OPENCLAW_AGENT_{worker_kind.upper().replace('-', '_')}"
        if str(os.getenv(env_name, "") or "").strip():
            return str(os.getenv(env_name) or "").strip()
        mapped = str(OPENCLAW_AGENT_BY_KIND.get(worker_kind) or "").strip()
        if mapped:
            return mapped
    return OPENCLAW_AGENT


def _openclaw_capability_brief(capability: str) -> tuple[str, str, list[str], list[str]]:
    normalized = str(capability or "").strip().lower()
    if normalized == "contract_retrieval":
        return (
            "Trace a specific contract or attachment from local files, shared exports, and public procurement sources.",
            "Treat this as evidence retrieval, not broad chatting or generic web search.",
            [
                "expand aliases for project names, vendors, product lines, and dates before searching",
                "search local or reachable file paths first when the task suggests an internal document may exist",
                "follow the evidence chain from award notice to contract notice to attachment or archived file",
                "state the evidence grade clearly: original file, contract notice, award notice, or indirect clue",
            ],
            [
                "do not present an award notice as if it were the contract itself",
                "do not invent contract terms, amounts, signatures, or attachments",
            ],
        )
    if normalized == "bid_research":
        return (
            "Find and rank bid or tender opportunities that fit the business context.",
            "Treat this as opportunity research and triage, not a one-keyword search dump.",
            [
                "expand the search into product aliases, buyer types, regions, time windows, and scenario terms",
                "search multiple source layers such as public procurement portals, public resource exchanges, hospitals, and schools",
                "deduplicate overlapping notices and explain why each retained result is relevant",
                "prioritize actionable opportunities and call out the best next move for follow-up",
            ],
            [
                "do not keep irrelevant search hits just to pad the result",
                "do not claim fit unless the notice content supports it",
            ],
        )
    if normalized == "customer_followup":
        return (
            "Prepare customer follow-up guidance from conversation history, account context, and concrete commercial signals.",
            "Treat this as sales support: crisp, useful, and grounded in actual customer context.",
            [
                "separate facts, assumptions, and recommended follow-up actions",
                "look for concrete customer signals such as requests, blockers, urgency, and buying stage",
                "draft next-step language that is specific enough to use, not generic sales filler",
                "surface missing context that would materially change the recommendation",
            ],
            [
                "do not invent customer commitments, budgets, or timelines",
                "do not turn weak signals into confident pipeline claims",
            ],
        )
    if normalized == "ops_recovery":
        return (
            "Inspect runtime state, isolate the failure, and propose or execute the safest recovery path allowed by the task.",
            "Treat this as an operations incident: evidence first, reversible actions, and explicit verification.",
            [
                "start from observed symptoms, logs, process state, and health output",
                "prefer minimal recovery steps that can be verified immediately",
                "record exactly what changed and what the verification result was",
                "call out permission, environment, or dependency blockers precisely",
            ],
            [
                "do not claim recovery without verification evidence",
                "do not hide risky or destructive actions inside a vague summary",
            ],
        )
    return (
        "Handle delegated exploratory work for Hermes with strong evidence and crisp reporting.",
        "Use broad but disciplined retrieval, then compress the result into a useful handoff.",
        [
            "prefer concrete sources, URLs, file paths, and inspected artifacts over abstract summaries",
            "make the smallest useful assumptions and keep moving when the task is slightly ambiguous",
            "surface blockers early if the task depends on unavailable sources or permissions",
        ],
        [
            "do not fabricate evidence, files, or claims",
            "do not ask Hermes to narrow the task unless a real blocker exists",
        ],
    )


def _build_runner_prompt(job: dict) -> str:
    return (
        "You are running a Hermes background job. Work from the persisted job prompt, "
        "do not ask the user non-blocking questions, and return a concise final result.\n\n"
        "You must keep the work self-contained and summarize deliverables, evidence, blockers, and next steps.\n\n"
        f"Trace ID: {job.get('trace_id')}\n"
        f"Job ID: {job.get('job_id')}\n"
        f"Title: {job.get('title')}\n\n"
        "Job prompt:\n"
        f"{job.get('prompt') or ''}"
    )


def _build_openclaw_runner_prompt(job: dict) -> str:
    capability = _capability_from_job(job)
    worker_kind = _worker_kind_from_job(job) or "research"
    selected_agent = _resolve_openclaw_agent(job)
    mission, posture, required_skills, guardrails = _openclaw_capability_brief(capability)
    skill_lines = "\n".join(f"- {item}" for item in required_skills)
    guardrail_lines = "\n".join(f"- {item}" for item in guardrails)
    return (
        "You are OpenClaw acting as an exploratory worker under Hermes orchestration.\n"
        "Hermes is delegating a bounded task to you because flexible retrieval and evidence gathering matter here.\n"
        "Think first, then act. Use search, browsing, and local inspection proactively when useful.\n"
        "If the requested contract, file, or evidence does not exist in reachable sources, say so clearly.\n"
        "Prefer concrete sources, URLs, file paths, and explicit uncertainty over broad summaries.\n\n"
        f"Capability: {capability or 'research'}\n"
        f"Worker kind: {worker_kind}\n"
        f"Target OpenClaw agent: {selected_agent}\n"
        f"Trace ID: {job.get('trace_id')}\n"
        f"Mission: {mission}\n"
        f"Working posture: {posture}\n"
        f"Job ID: {job.get('job_id')}\n"
        f"Title: {job.get('title')}\n\n"
        "Required skills for this run:\n"
        f"{skill_lines}\n\n"
        "Guardrails:\n"
        f"{guardrail_lines}\n\n"
        "Return a concise final answer with these sections when relevant:\n"
        "1. Outcome\n"
        "2. Evidence\n"
        "3. Gaps / blockers\n"
        "4. Best next move\n\n"
        "Delegated job prompt:\n"
        f"{job.get('prompt') or ''}"
    )


def _run_hermes_for_job(job: dict, timeout: int) -> tuple[int, str, str]:
    prompt = _build_runner_prompt(job)
    cmd = [
        "hermes",
        "chat",
        "-Q",
        "--source",
        "background-job",
        "--continue",
        f"background-{job.get('job_id')}",
        "-q",
        prompt,
    ]
    if os.getenv("HERMES_BACKGROUND_JOB_YOLO", "true").strip().lower() in {"1", "true", "yes", "on"}:
        cmd.insert(3, "--yolo")

    completed = subprocess.run(
        cmd,
        cwd=str(REPO_ROOT),
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=timeout,
        check=False,
    )
    return completed.returncode, completed.stdout or "", completed.stderr or ""


def _strip_ansi(text: str) -> str:
    return _ANSI_RE.sub("", str(text or ""))


def _extract_openclaw_payload(stdout: str) -> tuple[str, dict]:
    cleaned = _strip_ansi(stdout).strip()
    if not cleaned:
        return "", {}
    marker = cleaned.rfind('"payloads"')
    if marker >= 0:
        start = cleaned.rfind("{", 0, marker)
        if start >= 0:
            candidate = cleaned[start:].strip()
            try:
                payload = json.loads(candidate)
            except Exception:
                payload = None
            if isinstance(payload, dict):
                texts = []
                for item in payload.get("payloads") or []:
                    if isinstance(item, dict) and str(item.get("text") or "").strip():
                        texts.append(str(item.get("text") or "").strip())
                return "\n\n".join(texts).strip(), payload
    lines = cleaned.splitlines()
    for idx in range(len(lines) - 1, -1, -1):
        candidate = "\n".join(lines[idx:]).strip()
        if not candidate.startswith("{"):
            continue
        try:
            payload = json.loads(candidate)
        except Exception:
            continue
        if isinstance(payload, dict):
            texts: list[str] = []
            for item in payload.get("payloads") or []:
                if isinstance(item, dict) and str(item.get("text") or "").strip():
                    texts.append(str(item.get("text") or "").strip())
            return "\n\n".join(texts).strip(), payload
    return cleaned, {}


def _run_openclaw_for_job(job: dict, timeout: int) -> tuple[int, str, str]:
    prompt = _build_openclaw_runner_prompt(job)
    selected_agent = _resolve_openclaw_agent(job)
    cmd = [
        "openclaw",
        "--no-color",
        "agent",
        "--local",
        "--agent",
        selected_agent,
        "--json",
        "--thinking",
        OPENCLAW_THINKING,
        "--timeout",
        str(timeout),
        "--message",
        prompt,
    ]
    process = subprocess.Popen(
        cmd,
        cwd=str(Path.home()),
        start_new_session=True,
        stdout=subprocess.PIPE,
        stderr=subprocess.PIPE,
        text=True,
        encoding="utf-8",
        errors="replace",
    )
    update_job(
        str(job.get("job_id") or ""),
        runner_pid=process.pid,
        runner_runtime="openclaw",
    )
    try:
        try:
            stdout_raw, stderr_raw = process.communicate(timeout=timeout + 30)
        except subprocess.TimeoutExpired:
            try:
                os.killpg(process.pid, signal.SIGTERM)
            except Exception:
                process.terminate()
            raise
        raw_stream = stdout_raw or stderr_raw or ""
        text_result, payload = _extract_openclaw_payload(raw_stream)
        if text_result:
            stdout = text_result
        else:
            stdout = _strip_ansi(raw_stream)
        stderr = _strip_ansi(stderr_raw or "")
        if payload:
            meta = payload.get("meta") if isinstance(payload.get("meta"), dict) else {}
            agent_meta = meta.get("agentMeta") if isinstance(meta.get("agentMeta"), dict) else {}
            model = str(agent_meta.get("model") or "").strip()
            provider = str(agent_meta.get("provider") or "").strip()
            usage = agent_meta.get("usage") if isinstance(agent_meta.get("usage"), dict) else {}
            usage_total = usage.get("total")
            trailer = []
            trailer.append(f"OpenClaw agent: {selected_agent}")
            if provider or model:
                trailer.append(f"OpenClaw worker: {provider or '-'} / {model or '-'}")
            if usage_total:
                trailer.append(f"OpenClaw tokens(total): {usage_total}")
            if trailer:
                stdout = (stdout.rstrip() + "\n\n" + "\n".join(trailer)).strip()
        return process.returncode, stdout, stderr
    finally:
        update_job(
            str(job.get("job_id") or ""),
            runner_pid=None,
            runner_runtime="",
        )


def _delivery_target(job: dict) -> str:
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
    target = DeliveryTarget.from_origin(origin)
    if not target.is_valid():
        return ""
    return target.to_target_ref()


def _trim_for_delivery(text: str) -> tuple[str, bool]:
    cleaned = str(text or "").strip()
    if len(cleaned) <= MAX_DELIVERY_CHARS:
        return cleaned, False
    return cleaned[:MAX_DELIVERY_CHARS].rstrip() + "\n\n...（内容较长，完整结果已存入后台任务记录）", True


def _format_delivery_message(job: dict, *, failed: bool = False) -> str:
    job_id = str(job.get("job_id") or "")
    title = str(job.get("title") or job_id)
    result = str(job.get("result") or "").strip()
    blocker = str(job.get("blocker") or "").strip()
    body = blocker if failed and blocker else result
    body, truncated = _trim_for_delivery(body or "没有可展示的结果。")
    status_text = "失败" if failed else "完成"
    lines = [
        f"后台任务{status_text}：{title}",
        f"Job: {job_id}",
        "",
        body,
    ]
    if truncated:
        lines.append("")
        lines.append("可发“后台任务状态”查看任务 ID，再按 ID 查完整记录。")
    return "\n".join(lines).strip()


def _deliver_job_result(job: dict, *, failed: bool = False) -> None:
    if os.getenv("HERMES_BACKGROUND_JOB_AUTO_DELIVER", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return
    if job.get("delivered_at_unix"):
        return

    job_id = str(job.get("job_id") or "")
    target = _delivery_target(job)
    if not target:
        update_job(
            job_id,
            delivery_status="skipped",
            delivery_error="No origin platform/chat_id recorded for this job.",
        )
        append_job_event(job_id, kind="delivery", message="Skipped delivery: no origin target.")
        return

    try:
        result = send_text_to_target(
            DeliveryTarget.from_origin(job.get("origin") if isinstance(job.get("origin"), dict) else {}),
            _format_delivery_message(job, failed=failed),
        )
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result.get("error")))
        update_job(
            job_id,
            delivery_status="delivered",
            delivery_error="",
            delivery_target=target,
            delivered_at_unix=int(time.time()),
        )
        append_job_event(job_id, kind="delivery", message=f"Delivered result to {target}.")
    except Exception as exc:
        update_job(
            job_id,
            delivery_status="failed",
            delivery_error=f"{type(exc).__name__}: {exc}",
            delivery_target=target,
        )
        append_job_event(job_id, kind="delivery_failed", message=f"{type(exc).__name__}: {exc}")


def run_one(timeout: int, executor: str) -> bool:
    job = claim_next_job(executor=executor)
    if not job:
        return False

    job_id = str(job.get("job_id"))
    runtime = _requested_runtime(job)
    runtime_executor = "openclaw-worker" if runtime == "openclaw" else executor
    worker_kind = _worker_kind_from_job(job) or "research"
    selected_agent = _resolve_openclaw_agent(job)
    runtime_focus = (
        f"Running OpenClaw {worker_kind} worker via agent '{selected_agent}'."
        if runtime == "openclaw"
        else "Running Hermes background job."
    )
    runtime_step = (
        f"Wait for OpenClaw {worker_kind} worker '{selected_agent}' to finish and capture the result."
        if runtime == "openclaw"
        else "Wait for Hermes to finish and capture the result."
    )
    update_job(
        job_id,
        executor=runtime_executor,
        current_focus=runtime_focus,
        next_step=runtime_step,
    )
    append_job_event(
        job_id,
        kind="executor",
        message=(
            f"Worker started OpenClaw {worker_kind} execution with agent '{selected_agent}'."
            if runtime == "openclaw"
            else "Worker started Hermes execution."
        ),
        current_focus=runtime_focus,
        next_step=runtime_step,
    )
    try:
        current = get_job(job_id) or {}
        if str(current.get("status") or "").strip().lower() != "running":
            append_job_event(
                job_id,
                kind="skipped",
                message="Job no longer running when executor was about to start; skipping execution.",
                status=str(current.get("status") or ""),
            )
            return True
        if runtime == "openclaw":
            returncode, stdout, stderr = _run_openclaw_for_job(job, timeout=timeout)
        else:
            returncode, stdout, stderr = _run_hermes_for_job(job, timeout=timeout)
        output = "\n".join(
            line.strip()
            for line in (stdout or "").splitlines()
            if line.strip() and not line.strip().startswith("session_id:")
        ).strip()
        error = (stderr or "").strip()
        if returncode == 0 and output:
            completed_job = update_job(
                job_id,
                status="completed",
                current_focus="Background job completed.",
                next_step="Review the stored result; delivery to the originating chat has been attempted when available.",
                result=output,
            )
            _sync_capability_run_status(
                job,
                status="completed",
                current_focus="Background job completed.",
                next_step="Review the stored result delivered from the background worker.",
                result=output,
            )
            append_job_event(
                job_id,
                kind="completed",
                message="OpenClaw execution completed." if runtime == "openclaw" else "Hermes execution completed.",
            )
            if completed_job:
                _deliver_job_result(completed_job)
        else:
            failure_stage = "OpenClaw execution" if runtime == "openclaw" else "Hermes execution"
            exit_label = "openclaw" if runtime == "openclaw" else "hermes"
            failed_job = update_job(
                job_id,
                status="failed",
                current_focus=f"Background job failed during {failure_stage}.",
                blocker=error or output or f"{exit_label} exited with code {returncode}",
                result=output,
            )
            _sync_capability_run_status(
                job,
                status="failed",
                current_focus=f"Background job failed during {failure_stage}.",
                next_step="Inspect the worker error and retry the capability run if needed.",
                result=output,
                blocker=error or output or f"{exit_label} exited with code {returncode}",
            )
            append_job_event(job_id, kind="failed", message=error or output or f"exit code {returncode}")
            if failed_job:
                _deliver_job_result(failed_job, failed=True)
    except subprocess.TimeoutExpired:
        failed_job = update_job(
            job_id,
            status="failed",
            current_focus="Background job timed out.",
            blocker=f"Execution exceeded {timeout} seconds.",
        )
        _sync_capability_run_status(
            job,
            status="failed",
            current_focus="Background job timed out.",
            next_step="Inspect why the worker stalled and retry if appropriate.",
            blocker=f"Execution exceeded {timeout} seconds.",
        )
        append_job_event(job_id, kind="timeout", message=f"Execution exceeded {timeout} seconds.")
        if failed_job:
            _deliver_job_result(failed_job, failed=True)
    except Exception as exc:
        failed_job = update_job(
            job_id,
            status="failed",
            current_focus="Background job crashed.",
            blocker=f"{type(exc).__name__}: {exc}",
        )
        _sync_capability_run_status(
            job,
            status="failed",
            current_focus="Background job crashed.",
            next_step="Inspect the crash trace and retry the capability run if needed.",
            blocker=f"{type(exc).__name__}: {exc}",
        )
        append_job_event(job_id, kind="error", message=f"{type(exc).__name__}: {exc}")
        if failed_job:
            _deliver_job_result(failed_job, failed=True)
    finally:
        release_job_lock(job_id)
    return True


def main() -> int:
    parser = argparse.ArgumentParser(description="Run queued Hermes background jobs.")
    parser.add_argument("--once", action="store_true", help="Run at most one queued job and exit.")
    parser.add_argument("--interval", type=int, default=15, help="Loop sleep seconds.")
    parser.add_argument("--timeout", type=int, default=int(os.getenv("HERMES_BACKGROUND_JOB_TIMEOUT", "3600")), help="Per-job timeout seconds.")
    parser.add_argument("--executor", default=os.getenv("HERMES_BACKGROUND_JOB_EXECUTOR", "background-job-worker"), help="Executor name.")
    args = parser.parse_args()
    distill_enabled = str(os.getenv("HERMES_USER_PROFILE_DISTILL_ENABLED", "true") or "true").strip().lower() in {"1", "true", "yes", "on"}
    distill_interval = max(300, int(os.getenv("HERMES_USER_PROFILE_DISTILL_INTERVAL_SECONDS", "21600") or "21600"))
    distill_platform = str(os.getenv("HERMES_USER_PROFILE_DISTILL_PLATFORM", "dingtalk") or "dingtalk").strip().lower()
    distill_since_days = max(1, int(os.getenv("HERMES_USER_PROFILE_DISTILL_SINCE_DAYS", "30") or "30"))
    distill_user_limit = max(1, int(os.getenv("HERMES_USER_PROFILE_DISTILL_USER_LIMIT", "20") or "20"))
    distill_activity_limit = max(1, int(os.getenv("HERMES_USER_PROFILE_DISTILL_ACTIVITY_LIMIT", "20") or "20"))
    last_distill_at = 0.0

    if args.once:
        return 0 if run_one(timeout=args.timeout, executor=args.executor) else 2

    while True:
        ran = run_one(timeout=args.timeout, executor=args.executor)
        now = time.time()
        if distill_enabled and now - last_distill_at >= distill_interval:
            try:
                distill_recent_users(
                    platform=distill_platform,
                    since_days=distill_since_days,
                    user_limit=distill_user_limit,
                    activity_limit=distill_activity_limit,
                )
            except Exception:
                pass
            last_distill_at = now
        if not ran:
            time.sleep(max(1, args.interval))


if __name__ == "__main__":
    raise SystemExit(main())
