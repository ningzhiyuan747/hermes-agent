from __future__ import annotations

import os
import json
import re
import subprocess
import time
from pathlib import Path
from typing import Any, Dict

from agent.background_jobs import append_job_event, get_job, update_job
from agent.outbound_delivery import DeliveryTarget, send_text_to_target


_WINDOWS_DINGTALK_RELAY = Path("/mnt/f/hermes-control-plane/adapters/dingtalk/relay.py")
_WINDOWS_DINGTALK_PYTHON = Path("/mnt/f/hermes-control-plane/.venv/Scripts/python.exe")
_WINDOWS_DINGTALK_RELAY_ARG = r"F:\hermes-control-plane\adapters\dingtalk\relay.py"
_DINGTALK_ATTACHMENT_MAX_COUNT = 2
_DINGTALK_ATTACHMENT_PREFERRED_SUFFIXES = (".xlsx", ".xls", ".docx", ".doc", ".pdf")
_ABSOLUTE_ATTACHMENT_RE = re.compile(
    r"(?P<path>[A-Za-z]:\\[^\n\r`]+?\.(?:md|docx?|xlsx?|pdf|csv|txt)|/mnt/[^\n\r`]+?\.(?:md|docx?|xlsx?|pdf|csv|txt)|/[^\n\r`]+?\.(?:md|docx?|xlsx?|pdf|csv|txt))",
    re.IGNORECASE,
)
_ATTACHMENT_CONTEXT_RE = re.compile(r"(本轮实际生成的文件|文件在这里|写入回执|Successfully wrote|已保存文件|已导出文件|本次生成文件)", re.IGNORECASE)
_FORMAL_DOCUMENT_RE = re.compile(
    r"报价单|报价表|招标文件|投标文件|标书|承诺书|授权书|授权证明|声明函|合同|方案|申购|论证|参数表|汇总表|需求表|报告|docx|xlsx|pdf",
    re.IGNORECASE,
)


def _max_delivery_chars() -> int:
    try:
        return max(200, int(os.getenv("HERMES_BACKGROUND_JOB_DELIVERY_CHARS", "3200") or "3200"))
    except Exception:
        return 3200


def delivery_target_for_job(job: Dict[str, Any]) -> str:
    origin = job.get("origin") if isinstance(job.get("origin"), dict) else {}
    target = DeliveryTarget.from_origin(origin)
    if not target.is_valid():
        return ""
    return target.to_target_ref()


def trim_for_delivery(text: str) -> tuple[str, bool]:
    cleaned = str(text or "").strip()
    max_chars = _max_delivery_chars()
    if len(cleaned) <= max_chars:
        return cleaned, False
    return cleaned[:max_chars].rstrip() + "\n\n...（内容较长，完整结果已存入后台任务记录）", True


def _job_tags(job: Dict[str, Any]) -> set[str]:
    return {
        str(item).strip().lower()
        for item in (job.get("tags") or [])
        if str(item).strip()
    }


def _is_openclaw_job(job: Dict[str, Any]) -> bool:
    tags = _job_tags(job)
    if "executor:openclaw" in tags or "runtime:openclaw" in tags:
        return True
    executor = str(job.get("executor") or "").strip().lower()
    runner_runtime = str(job.get("runner_runtime") or "").strip().lower()
    return executor in {"openclaw", "openclaw-worker"} or runner_runtime == "openclaw"


def _collect_document_artifact_paths(text: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    for candidate in _extract_declared_attachment_paths(text) + _extract_evidence_attachment_paths(text):
        key = str(candidate)
        if key in seen:
            continue
        seen.add(key)
        paths.append(candidate)
    return paths


def _looks_like_formal_document_job(job: Dict[str, Any]) -> bool:
    title = str(job.get("title") or "").strip()
    prompt = str(job.get("prompt") or "").strip()
    result = str(job.get("result") or "").strip()
    if _collect_document_artifact_paths(result):
        return True
    corpus = "\n".join([title, prompt, result])
    return bool(_FORMAL_DOCUMENT_RE.search(corpus) or "模板来源：" in result or "本次生成文件：" in result)


def _format_formal_document_delivery_message(job: Dict[str, Any]) -> str:
    title = str(job.get("title") or job.get("job_id") or "文档").strip()
    result = str(job.get("result") or "").strip()
    lines = [f"已生成：{title}"]
    artifacts = _collect_document_artifact_paths(result)
    if artifacts:
        lines.extend(["", "本次生成文件："])
        for path in artifacts[:3]:
            lines.append(f"- {path}")
    else:
        lines.append("")
        lines.append("成品文件已生成。")
    return "\n".join(lines).strip()


def format_delivery_message(job: Dict[str, Any], *, failed: bool = False) -> str:
    job_id = str(job.get("job_id") or "")
    title = str(job.get("title") or job_id)
    result = str(job.get("result") or "").strip()
    blocker = str(job.get("blocker") or "").strip()
    if not failed and _looks_like_formal_document_job(job):
        return _format_formal_document_delivery_message(job)
    body = blocker if failed and blocker else result
    body, truncated = trim_for_delivery(body or "没有可展示的结果。")
    status_text = "失败" if failed else "完成"
    subject = "后台任务"
    if _is_openclaw_job(job):
        subject = "OpenClaw research 交付"
    lines = [
        f"{subject}{status_text}：{title}",
        f"Job: {job_id}",
        "",
        body,
    ]
    if truncated:
        lines.append("")
        lines.append("可发“后台任务状态”查看任务 ID，再按 ID 查完整记录。")
    return "\n".join(lines).strip()


def _deliver_to_dingtalk_via_relay(chat_id: str, text: str) -> Dict[str, Any]:
    if not chat_id:
        raise RuntimeError("Missing DingTalk chat_id for relay delivery.")
    if not _WINDOWS_DINGTALK_PYTHON.exists() or not _WINDOWS_DINGTALK_RELAY.exists():
        raise RuntimeError("DingTalk relay runtime is missing on the shared Windows workspace.")
    completed = subprocess.run(
        [
            str(_WINDOWS_DINGTALK_PYTHON),
            _WINDOWS_DINGTALK_RELAY_ARG,
            "--chat-id",
            str(chat_id),
            "--message",
            str(text),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=60,
        check=False,
    )
    raw = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        raise RuntimeError(raw or f"DingTalk relay exited with code {completed.returncode}")
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {"raw": raw}
    if isinstance(payload, dict) and payload.get("ok") is False:
        raise RuntimeError(str(payload.get("detail") or payload.get("error") or raw or "DingTalk relay failed"))
    return payload if isinstance(payload, dict) else {"raw": raw}


def _wsl_path_to_windows(path_value: str) -> str | None:
    raw = str(path_value or "").strip()
    match = re.match(r"^/mnt/(?P<drive>[a-zA-Z])/(?P<rest>.+)$", raw)
    if not match:
        return None
    drive = match.group("drive").upper()
    rest = match.group("rest").replace("/", "\\")
    return f"{drive}:\\{rest}"


def _normalize_attachment_candidate(value: str) -> Path | None:
    raw = str(value or "").strip().strip("`").strip()
    if not raw:
        return None
    raw = re.sub(r":\d+$", "", raw)
    mapped = _wsl_path_to_windows(raw)
    if mapped:
        candidate = Path(mapped)
        if candidate.exists():
            return candidate
    candidate = Path(raw)
    return candidate if candidate.exists() else None


def _extract_declared_attachment_paths(text: str) -> list[Path]:
    patterns = (
        re.compile(r"(?:已保存文件|已导出文件|本次生成文件)\s*[:：]\s*(?P<path>[A-Za-z]:[^\n\r]+|/mnt/[^\n\r]+|/[^\n\r]+)"),
    )
    paths: list[Path] = []
    seen: set[str] = set()
    raw_text = str(text or "")
    for pattern in patterns:
        for match in pattern.finditer(raw_text):
            candidate = _normalize_attachment_candidate(match.group("path"))
            if candidate is None:
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
    return paths


def _preferred_attachment_suffixes(text: str) -> tuple[str, ...]:
    corpus = str(text or "")
    if re.search(r"招标文件|投标文件|标书|合同|方案|报告|承诺书|说明书", corpus, flags=re.IGNORECASE):
        return (".docx", ".doc", ".pdf", ".xlsx", ".xls")
    if re.search(r"excel|xlsx|报价单|报价表|清单|明细|表格|价格表|汇总表", corpus, flags=re.IGNORECASE):
        return (".xlsx", ".xls", ".pdf", ".docx", ".doc")
    return (".docx", ".doc", ".pdf", ".xlsx", ".xls")


def _extract_evidence_attachment_paths(text: str) -> list[Path]:
    paths: list[Path] = []
    seen: set[str] = set()
    previous_meaningful = ""
    for raw_line in str(text or "").splitlines():
        line = raw_line.strip()
        if not line:
            continue
        for match in _ABSOLUTE_ATTACHMENT_RE.finditer(line):
            if not (_ATTACHMENT_CONTEXT_RE.search(line) or _ATTACHMENT_CONTEXT_RE.search(previous_meaningful)):
                continue
            candidate = _normalize_attachment_candidate(match.group("path"))
            if candidate is None:
                continue
            key = str(candidate)
            if key in seen:
                continue
            seen.add(key)
            paths.append(candidate)
        previous_meaningful = line
    return paths


def _expand_attachment_variants(path: Path, preferred_suffixes: tuple[str, ...] | None = None) -> list[Path]:
    if path.suffix.lower() != ".md":
        return [path]
    variants: list[Path] = []
    for suffix in preferred_suffixes or _DINGTALK_ATTACHMENT_PREFERRED_SUFFIXES:
        sibling = path.with_suffix(suffix)
        if sibling.exists() and sibling.is_file():
            variants.append(sibling)
    return variants or [path]


def _materialize_attachment_variants(path: Path, prompt: str) -> list[Path]:
    candidate = Path(path)
    if candidate.suffix.lower() != ".md":
        return [candidate]
    preferred_suffixes = _preferred_attachment_suffixes(prompt)
    existing = _expand_attachment_variants(candidate, preferred_suffixes)
    if existing and existing != [candidate]:
        return existing

    relay_path = _wsl_path_to_windows(str(candidate)) or str(candidate)
    completed = subprocess.run(
        [
            str(_WINDOWS_DINGTALK_PYTHON),
            _WINDOWS_DINGTALK_RELAY_ARG,
            "--export-structured-document",
            relay_path,
            "--question",
            str(prompt or candidate.stem),
        ],
        capture_output=True,
        text=True,
        encoding="utf-8",
        errors="replace",
        timeout=180,
        check=False,
    )
    raw = (completed.stdout or completed.stderr or "").strip()
    if completed.returncode != 0:
        return [candidate]
    try:
        payload = json.loads(raw) if raw else {}
    except json.JSONDecodeError:
        payload = {}
    exported: list[Path] = []
    for item in payload.get("exported") or []:
        normalized = _normalize_attachment_candidate(str(item))
        if normalized is not None:
            exported.append(normalized)
    ordered: list[Path] = []
    seen: set[str] = set()
    for suffix in preferred_suffixes:
        sibling = candidate.with_suffix(suffix)
        if sibling.exists() and sibling.is_file():
            key = str(sibling)
            if key not in seen:
                seen.add(key)
                ordered.append(sibling)
    for item in exported:
        key = str(item)
        if key not in seen and item.exists() and item.is_file():
            seen.add(key)
            ordered.append(item)
    return ordered or [candidate]


def _extract_sendable_dingtalk_attachments(text: str) -> list[Path]:
    sendable: list[Path] = []
    seen: set[str] = set()
    candidates = _extract_declared_attachment_paths(text)
    if not candidates:
        candidates = _extract_evidence_attachment_paths(text)
    for candidate in candidates:
        for expanded in _materialize_attachment_variants(candidate, text):
            key = str(expanded)
            if key in seen or not expanded.exists() or not expanded.is_file():
                continue
            seen.add(key)
            sendable.append(expanded)
            if len(sendable) >= _DINGTALK_ATTACHMENT_MAX_COUNT:
                return sendable
    return sendable


def _deliver_dingtalk_files_via_relay(chat_id: str, files: list[Path]) -> list[Dict[str, Any]]:
    payloads: list[Dict[str, Any]] = []
    for path in files:
        relay_path = _wsl_path_to_windows(str(path)) or str(path)
        completed = subprocess.run(
            [
                str(_WINDOWS_DINGTALK_PYTHON),
                _WINDOWS_DINGTALK_RELAY_ARG,
                "--chat-id",
                str(chat_id),
                "--file",
                relay_path,
            ],
            capture_output=True,
            text=True,
            encoding="utf-8",
            errors="replace",
            timeout=120,
            check=False,
        )
        raw = (completed.stdout or completed.stderr or "").strip()
        if completed.returncode != 0:
            raise RuntimeError(raw or f"DingTalk relay file send failed for {path} with code {completed.returncode}")
        try:
            payload = json.loads(raw) if raw else {}
        except json.JSONDecodeError:
            payload = {"raw": raw}
        payloads.append(payload if isinstance(payload, dict) else {"raw": raw})
    return payloads


def deliver_job_result(job: Dict[str, Any], *, failed: bool = False, force: bool = False) -> Dict[str, Any] | None:
    if os.getenv("HERMES_BACKGROUND_JOB_AUTO_DELIVER", "true").strip().lower() not in {"1", "true", "yes", "on"}:
        return job

    current = get_job(str(job.get("job_id") or "")) or dict(job)
    if current.get("delivered_at_unix") and not force:
        return current

    job_id = str(current.get("job_id") or "")
    target = delivery_target_for_job(current)
    if not target:
        updated = update_job(
            job_id,
            delivery_status="skipped",
            delivery_error="No origin platform/chat_id recorded for this job.",
            delivered_at_unix=0,
        )
        append_job_event(job_id, kind="delivery", message="Skipped delivery: no origin target.")
        return updated

    try:
        origin = current.get("origin") if isinstance(current.get("origin"), dict) else {}
        target_info = DeliveryTarget.from_origin(origin)
        message = format_delivery_message(current, failed=failed)
        if str(target_info.platform or "").strip().lower() == "dingtalk":
            result = _deliver_to_dingtalk_via_relay(str(target_info.chat_id or "").strip(), message)
            attachments = _extract_sendable_dingtalk_attachments(str(current.get("result") or ""))
            if attachments:
                file_payloads = _deliver_dingtalk_files_via_relay(str(target_info.chat_id or "").strip(), attachments)
                if isinstance(result, dict):
                    result["files"] = [str(path) for path in attachments]
                    result["file_results"] = file_payloads
        else:
            result = send_text_to_target(target_info, message)
        if isinstance(result, dict) and result.get("error"):
            raise RuntimeError(str(result.get("error")))
        updated = update_job(
            job_id,
            delivery_status="delivered",
            delivery_error="",
            delivery_target=target,
            delivered_at_unix=int(time.time()),
        )
        append_job_event(job_id, kind="delivery", message=f"Delivered result to {target}.")
        return updated
    except Exception as exc:
        updated = update_job(
            job_id,
            delivery_status="failed",
            delivery_error=f"{type(exc).__name__}: {exc}",
            delivery_target=target,
            delivered_at_unix=0,
        )
        append_job_event(job_id, kind="delivery_failed", message=f"{type(exc).__name__}: {exc}")
        return updated
