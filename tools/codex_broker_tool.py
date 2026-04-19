"""Managed Codex CLI broker for Hermes."""

from __future__ import annotations

import json
import os
import re
import shlex
import signal
import subprocess
import time
import uuid
from pathlib import Path, PureWindowsPath
from typing import Any, Dict

from hermes_cli.config import get_hermes_home
from tools.registry import registry, tool_error, tool_result

CODEX_BIN = Path(os.path.expanduser("~/.hermes/node/bin/codex"))
BROKER_DIR = get_hermes_home() / "codex_broker"
INDEX_PATH = BROKER_DIR / "sessions.json"
CANONICAL_WSL_CWD = "/mnt/f/hermes-dingtalk-bridge"
ALIAS_WSL_CWD = "/mnt/f/Desktop/爱马仕"
CANONICAL_WINDOWS_CWD = r"F:\hermes-dingtalk-bridge"
ALIAS_WINDOWS_CWD = r"F:\Desktop\爱马仕"
CODEX_WORKSPACE_LINK = Path("/home/user/.hermes/workspaces/hermes-dingtalk-bridge")
THREAD_STARTED_RE = re.compile(r'"type"\s*:\s*"thread\.started".*?"thread_id"\s*:\s*"([^"]+)"')


def _ensure_store() -> None:
    BROKER_DIR.mkdir(parents=True, exist_ok=True)


def _ensure_workspace_link() -> None:
    CODEX_WORKSPACE_LINK.parent.mkdir(parents=True, exist_ok=True)
    target = Path(ALIAS_WSL_CWD)
    try:
        if CODEX_WORKSPACE_LINK.is_symlink() or CODEX_WORKSPACE_LINK.exists():
            current = CODEX_WORKSPACE_LINK.resolve()
            if current == target.resolve():
                return
            CODEX_WORKSPACE_LINK.unlink()
        CODEX_WORKSPACE_LINK.symlink_to(target)
    except FileExistsError:
        pass


def _load_index() -> Dict[str, Dict[str, Any]]:
    _ensure_store()
    if not INDEX_PATH.exists():
        return {}
    try:
        data = json.loads(INDEX_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {}
    if not isinstance(data, dict):
        return {}
    return {str(k): dict(v) for k, v in data.items() if isinstance(v, dict)}


def _save_index(records: Dict[str, Dict[str, Any]]) -> None:
    _ensure_store()
    tmp = INDEX_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(records, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(INDEX_PATH)


def _session_env(name: str, default: str = "") -> str:
    try:
        from gateway.session_context import get_session_env

        return get_session_env(name, default) or default
    except Exception:
        return os.getenv(name, default)


def _current_session_key() -> str:
    return _session_env("HERMES_SESSION_KEY", "")


def _windows_to_wsl_path(path: str) -> str:
    p = PureWindowsPath(path)
    drive = str(p.drive).rstrip(":").lower()
    tail = "/".join(p.parts[1:])
    return f"/mnt/{drive}/{tail}" if tail else f"/mnt/{drive}"


def _normalize_cwd(raw_cwd: str) -> str:
    cwd = (raw_cwd or "").strip()
    if not cwd:
        _ensure_workspace_link()
        return str(CODEX_WORKSPACE_LINK)

    lower = cwd.lower()
    if lower.startswith(ALIAS_WINDOWS_CWD.lower()):
        suffix = cwd[len(ALIAS_WINDOWS_CWD) :]
        _ensure_workspace_link()
        return str(CODEX_WORKSPACE_LINK) + suffix.replace("\\", "/")
    if lower.startswith(CANONICAL_WINDOWS_CWD.lower()):
        suffix = cwd[len(CANONICAL_WINDOWS_CWD) :]
        _ensure_workspace_link()
        return str(CODEX_WORKSPACE_LINK) + suffix.replace("\\", "/")
    if cwd.startswith(ALIAS_WSL_CWD):
        suffix = cwd[len(ALIAS_WSL_CWD) :]
        _ensure_workspace_link()
        return str(CODEX_WORKSPACE_LINK) + suffix
    if cwd.startswith(CANONICAL_WSL_CWD):
        suffix = cwd[len(CANONICAL_WSL_CWD) :]
        _ensure_workspace_link()
        return str(CODEX_WORKSPACE_LINK) + suffix

    lower = cwd.lower()
    if re.match(r"^[a-z]:\\", lower):
        lower = cwd.lower()
        return _windows_to_wsl_path(cwd)
    return cwd


def _display_cwd(cwd: str) -> str:
    if cwd.startswith(str(CODEX_WORKSPACE_LINK)):
        suffix = cwd[len(str(CODEX_WORKSPACE_LINK)) :]
        return CANONICAL_WINDOWS_CWD + suffix.replace("/", "\\")
    if cwd.startswith(CANONICAL_WSL_CWD):
        suffix = cwd[len(CANONICAL_WSL_CWD) :]
        return CANONICAL_WINDOWS_CWD + suffix.replace("/", "\\")
    return cwd


def _read_text_file(path_str: str) -> str:
    if not path_str:
        return ""
    try:
        path = Path(path_str)
        if path.exists():
            return path.read_text(encoding="utf-8", errors="replace")
    except Exception:
        return ""
    return ""


def _write_text_file(path: Path, content: str) -> None:
    path.write_text(content, encoding="utf-8")


def _pid_alive(pid: int) -> bool:
    try:
        os.kill(pid, 0)
        return True
    except (ProcessLookupError, PermissionError):
        return False


def _parse_thread_id(*texts: str) -> str:
    for text in texts:
        if not text:
            continue
        match = THREAD_STARTED_RE.search(text)
        if match:
            return match.group(1)
        for line in text.splitlines():
            line = line.strip()
            if not line.startswith("{"):
                continue
            try:
                payload = json.loads(line)
            except Exception:
                continue
            if payload.get("type") == "thread.started" and payload.get("thread_id"):
                return str(payload["thread_id"])
    return ""


def _compact_record(record: Dict[str, Any]) -> Dict[str, Any]:
    return {
        "broker_id": record.get("broker_id"),
        "status": record.get("status"),
        "cwd": record.get("cwd_display"),
        "model": record.get("model"),
        "thread_id": record.get("thread_id") or "",
        "pid": record.get("pid"),
        "created_at": record.get("created_at"),
        "updated_at": record.get("updated_at"),
        "exit_code": record.get("exit_code"),
        "final_output": record.get("final_output", ""),
        "transcript_tail": record.get("transcript_tail", ""),
    }


def _resolve_broker_id(args: Dict[str, Any], records: Dict[str, Dict[str, Any]]) -> str:
    requested = str(args.get("broker_id") or args.get("session_id") or "").strip()
    if requested:
        return requested

    session_key = _current_session_key()
    candidates = []
    for broker_id, record in records.items():
        if session_key and record.get("session_key") != session_key:
            continue
        candidates.append((float(record.get("created_at_unix") or 0), broker_id))

    if not candidates:
        for broker_id, record in records.items():
            candidates.append((float(record.get("created_at_unix") or 0), broker_id))

    candidates.sort(reverse=True)
    return candidates[0][1] if candidates else ""


def _refresh_record(record: Dict[str, Any]) -> Dict[str, Any]:
    record = dict(record)
    pid = int(record.get("pid") or 0)
    exit_code = None
    exit_file = Path(str(record.get("exit_file") or ""))
    if exit_file.exists():
        try:
            exit_code = int(exit_file.read_text(encoding="utf-8", errors="replace").strip() or "0")
        except Exception:
            exit_code = None

    transcript = _read_text_file(str(record.get("transcript_file") or ""))
    final_output = _read_text_file(str(record.get("output_file") or "")).strip()

    if exit_code is not None:
        status = "exited"
    elif pid and _pid_alive(pid):
        status = "running"
    else:
        status = "unknown"

    record["status"] = status
    record["exit_code"] = exit_code
    record["thread_id"] = record.get("thread_id") or _parse_thread_id(transcript)
    record["final_output"] = final_output
    record["transcript_tail"] = "\n".join(transcript.splitlines()[-40:]) if transcript else ""
    now = time.time()
    record["updated_at_unix"] = now
    record["updated_at"] = time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now))
    return record


def _spawn_codex_process(
    *,
    action: str,
    prompt: str,
    cwd: str,
    model: str,
    sandbox: str,
    dangerous: bool,
    output_file: Path,
    transcript_file: Path,
    exit_file: Path,
    thread_id: str = "",
) -> int:
    codex_bin = shlex.quote(str(CODEX_BIN))
    args = [codex_bin, "exec"]
    if action == "resume":
        args.extend(["resume", "--json"])
        if thread_id:
            args.append(shlex.quote(thread_id))
        if prompt:
            args.append(shlex.quote(prompt))
    else:
        args.extend(
            [
                "--json",
                "--skip-git-repo-check",
                "-o",
                shlex.quote(str(output_file)),
            ]
        )
    if dangerous:
        args.append("--dangerously-bypass-approvals-and-sandbox")
    elif action != "resume":
        args.extend(["--sandbox", shlex.quote(sandbox)])
    if model:
        args.extend(["-m", shlex.quote(model)])
    if action == "resume":
        args.extend(["-o", shlex.quote(str(output_file))])
        args.append("--skip-git-repo-check")
    else:
        args.append(shlex.quote(prompt))
    command = " ".join(args)
    wrapper = (
        f"rm -f {shlex.quote(str(exit_file))}; "
        f"{command}; "
        f"rc=$?; printf '%s' \"$rc\" > {shlex.quote(str(exit_file))}"
    )

    with transcript_file.open("w", encoding="utf-8") as transcript_handle:
        process = subprocess.Popen(
            ["bash", "-lc", wrapper],
            cwd=cwd,
            stdout=transcript_handle,
            stderr=subprocess.STDOUT,
            stdin=subprocess.DEVNULL,
            start_new_session=True,
            text=True,
        )
    return int(process.pid)


def codex_broker_tool(args: Dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "list").strip().lower()
    records = _load_index()

    if action in {"start", "resume"}:
        prompt = str(args.get("prompt") or "").strip()
        if action == "start" and not prompt:
            return tool_error("codex_broker.start requires prompt")

        requested_cwd = str(args.get("cwd") or "").strip()
        cwd = _normalize_cwd(requested_cwd)
        if not Path(cwd).exists():
            return tool_error(f"codex_broker cwd not found: {cwd}")
        if not CODEX_BIN.exists():
            return tool_error(f"codex binary not found: {CODEX_BIN}")

        resumed_from = ""
        thread_id = str(args.get("thread_id") or "").strip()
        if action == "resume":
            previous_id = _resolve_broker_id(args, records)
            if previous_id and previous_id in records:
                previous = _refresh_record(records[previous_id])
                records[previous_id] = previous
                resumed_from = previous_id
                if not requested_cwd:
                    cwd = _normalize_cwd(str(previous.get("cwd") or ""))
                if not thread_id:
                    thread_id = str(previous.get("thread_id") or "").strip()
            if not thread_id:
                return tool_error("codex_broker.resume requires a previous broker session with thread_id or an explicit thread_id")
            if not prompt:
                prompt = "Continue from the latest confirmed Codex state and keep moving the task forward."

        broker_id = f"codex_{uuid.uuid4().hex[:10]}"
        output_file = BROKER_DIR / f"{broker_id}.last.txt"
        transcript_file = BROKER_DIR / f"{broker_id}.jsonl"
        exit_file = BROKER_DIR / f"{broker_id}.exit"
        model = str(args.get("model") or "gpt-5.4").strip()
        sandbox = str(args.get("sandbox") or "workspace-write").strip() or "workspace-write"
        dangerous = bool(args.get("dangerous"))
        pid = _spawn_codex_process(
            action=action,
            prompt=prompt,
            cwd=cwd,
            model=model,
            sandbox=sandbox,
            dangerous=dangerous,
            output_file=output_file,
            transcript_file=transcript_file,
            exit_file=exit_file,
            thread_id=thread_id,
        )
        now = time.time()
        record = {
            "broker_id": broker_id,
            "action": action,
            "prompt": prompt,
            "cwd": cwd,
            "cwd_display": _display_cwd(cwd),
            "model": model,
            "sandbox": sandbox,
            "dangerous": dangerous,
            "pid": pid,
            "session_key": _current_session_key(),
            "platform": _session_env("HERMES_SESSION_PLATFORM", ""),
            "chat_id": _session_env("HERMES_SESSION_CHAT_ID", ""),
            "user_id": _session_env("HERMES_SESSION_USER_ID", ""),
            "thread_id": thread_id if action == "resume" else "",
            "resumed_from": resumed_from,
            "output_file": str(output_file),
            "transcript_file": str(transcript_file),
            "exit_file": str(exit_file),
            "created_at_unix": now,
            "updated_at_unix": now,
            "created_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "updated_at": time.strftime("%Y-%m-%dT%H:%M:%S", time.localtime(now)),
            "status": "running",
            "exit_code": None,
            "final_output": "",
            "transcript_tail": "",
        }
        records[broker_id] = record
        _save_index(records)
        return tool_result(ok=True, codex=_compact_record(record))

    if action == "list":
        refreshed = []
        for broker_id, record in records.items():
            updated = _refresh_record(record)
            records[broker_id] = updated
            refreshed.append(_compact_record(updated))
        _save_index(records)
        refreshed.sort(key=lambda item: str(item.get("created_at") or ""), reverse=True)
        return tool_result(ok=True, codex_sessions=refreshed)

    broker_id = _resolve_broker_id(args, records)
    if not broker_id or broker_id not in records:
        return tool_error("No matching codex broker session found")

    record = _refresh_record(records[broker_id])
    records[broker_id] = record

    if action == "status":
        _save_index(records)
        return tool_result(ok=True, codex=_compact_record(record))

    if action == "read":
        transcript = _read_text_file(str(record.get("transcript_file") or ""))
        lines = transcript.splitlines()
        offset = int(args.get("offset") or 0)
        limit = int(args.get("limit") or 120)
        selected = lines[-limit:] if offset == 0 else lines[offset : offset + limit]
        payload = _compact_record(record)
        payload["log"] = {
            "status": record.get("status"),
            "output": "\n".join(selected),
            "total_lines": len(lines),
            "showing": f"{len(selected)} lines",
        }
        _save_index(records)
        return tool_result(ok=True, codex=payload)

    if action == "wait":
        timeout = int(args.get("timeout") or 180)
        deadline = time.time() + timeout
        while time.time() < deadline:
            record = _refresh_record(records[broker_id])
            records[broker_id] = record
            if record.get("status") == "exited":
                break
            time.sleep(1)
        payload = _compact_record(record)
        payload["wait"] = {
            "status": record.get("status"),
            "exit_code": record.get("exit_code"),
            "final_output": record.get("final_output", ""),
        }
        _save_index(records)
        return tool_result(ok=True, codex=payload)

    if action in {"stop", "cancel", "kill"}:
        pid = int(record.get("pid") or 0)
        kill_result: Dict[str, Any]
        if pid and _pid_alive(pid):
            try:
                os.killpg(pid, signal.SIGTERM)
                kill_result = {"status": "killed", "pid": pid}
            except Exception as exc:
                kill_result = {"status": "error", "error": str(exc), "pid": pid}
        else:
            kill_result = {"status": "already_exited", "pid": pid}
        record = _refresh_record(records[broker_id])
        records[broker_id] = record
        payload = _compact_record(record)
        payload["kill"] = kill_result
        _save_index(records)
        return tool_result(ok=True, codex=payload)

    return tool_error(f"unknown codex_broker action: {action}")


CODEX_BROKER_SCHEMA = {
    "name": "codex_broker",
    "description": (
        "Control the local Codex CLI through a managed broker. "
        "Use this when Hermes should delegate a coding task to Codex without relying on desktop UI automation. "
        "Preferred actions are start, status, read, wait, and stop."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": ["start", "resume", "list", "status", "read", "wait", "stop", "cancel", "kill"],
                "description": "Broker action.",
            },
            "broker_id": {
                "type": "string",
                "description": "Codex broker id. If omitted for status/read/wait/stop, the latest session for the current chat is used.",
            },
            "prompt": {"type": "string", "description": "Prompt to send to Codex when action=start or resume."},
            "thread_id": {
                "type": "string",
                "description": "Explicit Codex thread/session id to resume. Usually omitted and inferred from the latest broker session.",
            },
            "cwd": {"type": "string", "description": "Working directory for Codex. Defaults to the canonical Hermes workspace."},
            "model": {"type": "string", "description": "Codex model to use. Defaults to gpt-5.4."},
            "sandbox": {
                "type": "string",
                "enum": ["read-only", "workspace-write", "danger-full-access"],
                "description": "Sandbox mode for Codex exec. Defaults to workspace-write.",
            },
            "dangerous": {"type": "boolean", "description": "If true, run Codex with bypassed approvals and sandbox."},
            "timeout": {"type": "integer", "description": "Wait timeout in seconds for action=wait."},
            "offset": {"type": "integer", "description": "Line offset for action=read."},
            "limit": {"type": "integer", "description": "Line limit for action=read."},
        },
        "required": ["action"],
    },
}


registry.register(
    name="codex_broker",
    toolset="coding-agent",
    schema=CODEX_BROKER_SCHEMA,
    handler=codex_broker_tool,
    emoji="🧠",
)
