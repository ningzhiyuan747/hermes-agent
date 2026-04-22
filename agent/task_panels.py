"""Shared task panel formatting helpers."""

from __future__ import annotations

from typing import Any, Dict, List

from agent.capability_snapshots import (
    extract_background_job_person_memory_key,
    extract_background_job_task_scope_key,
    extract_capability_run_person_memory_key,
    extract_capability_run_task_scope_key,
)


def _artifact_priority(item: Dict[str, Any]) -> tuple[int, int, str]:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    label = str(item.get("label") or "").strip().lower()
    summary = str(item.get("summary") or "").strip().lower()
    kind = str(item.get("kind") or "").strip().lower()
    score = 0
    if bool(metadata.get("final")) or bool(metadata.get("deliverable")):
        score = 100
    elif kind in {"deliverable"}:
        score = 90
    elif kind in {"link", "evidence", "screenshot", "image", "url"}:
        score = 60
    elif "合同" in label or "contract" in label:
        score = 50
    elif "链接" in label or "url" in label:
        score = 40
    elif "摘要" in summary or "report" in summary:
        score = 15
    created = int(item.get("created_at_unix") or 0)
    return (-score, -created, label)


def sorted_artifact_items(items: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    normalized = [item for item in items if isinstance(item, dict)]
    return sorted(normalized, key=_artifact_priority)


def format_artifact_line(item: Dict[str, Any], *, fullwidth_colon: bool = False) -> str:
    metadata = item.get("metadata") if isinstance(item.get("metadata"), dict) else {}
    label = str(item.get("label") or "").strip() or str(item.get("kind") or "artifact").strip() or "artifact"
    ref = str(item.get("path_or_ref") or "").strip()
    summary = str(item.get("summary") or "").strip()
    if bool(metadata.get("final")) or bool(metadata.get("deliverable")):
        prefix = "Deliverable"
    elif str(item.get("kind") or "").strip().lower() in {"link", "evidence", "screenshot", "image", "url"}:
        prefix = "Evidence"
    else:
        prefix = "Artifact"
    colon = "：" if fullwidth_colon else ":"
    detail = summary or ref or "-"
    return f"{prefix}{colon} {label[:40]} -> {detail[:120]}"


def format_task_panel_snapshot(snapshot: Dict[str, Any], *, header: str = "当前群任务：", fullwidth_colon: bool = False) -> str:
    task = snapshot.get("task") if isinstance(snapshot.get("task"), dict) else {}
    current_run = snapshot.get("current_run") if isinstance(snapshot.get("current_run"), dict) else {}
    current_job = snapshot.get("current_job") if isinstance(snapshot.get("current_job"), dict) else {}
    current_delegation = snapshot.get("current_delegation") if isinstance(snapshot.get("current_delegation"), dict) else {}
    approvals = snapshot.get("approvals") if isinstance(snapshot.get("approvals"), list) else []
    artifact_items = snapshot.get("artifact_items") if isinstance(snapshot.get("artifact_items"), list) else []
    control_summary = snapshot.get("control_summary") if isinstance(snapshot.get("control_summary"), dict) else {}
    lines = [header, f"Task ID: {str(task.get('task_id') or '').strip()}"]
    if str(task.get("title") or "").strip():
        lines.append(f"标题: {str(task.get('title') or '').strip()}")
    summary_status = str(control_summary.get("status") or task.get("status") or "").strip()
    if summary_status:
        lines.append(f"状态: {summary_status}")
    if str(task.get("goal") or "").strip():
        lines.append(f"目标: {str(task.get('goal') or '').strip()}")
    current_executor = str(control_summary.get("current_executor") or "").strip()
    if current_executor:
        lines.append(f"当前执行器: {current_executor}")
    if current_run:
        lines.append(
            f"当前 Run: {str(current_run.get('capability_name') or '-').strip()} / {str(current_run.get('status') or '-').strip()}"
        )
    if current_job:
        lines.append(
            f"当前后台任务: {str(current_job.get('job_id') or '-').strip()} / {str(current_job.get('status') or '-').strip()}"
        )
    if current_delegation:
        lines.append(
            f"当前子代理: {str(current_delegation.get('worker_role') or current_delegation.get('role_title') or '-').strip()} / {str(current_delegation.get('status') or '-').strip()}"
        )
    if approvals:
        lines.append(f"待审批: {len(approvals)}")
    current_focus = str(control_summary.get("current_focus") or "").strip()
    next_step = str(control_summary.get("next_step") or "").strip()
    blocker = str(control_summary.get("blocker") or "").strip()
    failure_kind = str(control_summary.get("failure_kind") or "").strip()
    execution_status = str(control_summary.get("execution_status") or "").strip()
    delivery_status = str(control_summary.get("delivery_status") or "").strip()
    recovery_hint = str(control_summary.get("recovery_hint") or "").strip()
    dispatch_action = str(control_summary.get("dispatch_action") or "").strip()
    suggested_executor = str(control_summary.get("suggested_executor") or "").strip()
    last_secretary_action = str(control_summary.get("last_secretary_action") or "").strip()
    last_secretary_action_summary = str(control_summary.get("last_secretary_action_summary") or "").strip()
    requested_executor_override = str(control_summary.get("requested_executor_override") or "").strip()
    last_follow_up_summary = str(control_summary.get("last_follow_up_summary") or "").strip()
    last_follow_up_target_ref = str(control_summary.get("last_follow_up_target_ref") or "").strip()
    operator_queue_count = int(control_summary.get("operator_queue_count") or 0)
    operator_queue_next = str(control_summary.get("operator_queue_next") or "").strip()
    last_operator_resolution = str(control_summary.get("last_operator_resolution") or "").strip()
    last_operator_resolution_summary = str(control_summary.get("last_operator_resolution_summary") or "").strip()
    if current_focus:
        lines.append(f"当前: {current_focus[:120]}")
    if next_step:
        lines.append(f"下一步: {next_step[:120]}")
    if blocker:
        lines.append(f"阻塞: {blocker[:120]}")
    if execution_status:
        lines.append(f"执行状态: {execution_status}")
    if delivery_status:
        lines.append(f"投递状态: {delivery_status}")
    if failure_kind:
        lines.append(f"失败分类: {failure_kind}")
    if recovery_hint:
        lines.append(f"恢复建议: {recovery_hint[:120]}")
    if dispatch_action:
        lines.append(f"调度动作: {dispatch_action}")
    if suggested_executor:
        lines.append(f"建议执行器: {suggested_executor}")
    if last_secretary_action:
        lines.append(f"最近秘书动作: {last_secretary_action}")
    if requested_executor_override:
        lines.append(f"秘书改派请求: {requested_executor_override}")
    if last_secretary_action_summary:
        lines.append(f"秘书动作结果: {last_secretary_action_summary[:120]}")
    if last_follow_up_target_ref:
        lines.append(f"最近秘书提醒目标: {last_follow_up_target_ref}")
    if last_follow_up_summary:
        lines.append(f"最近秘书提醒: {last_follow_up_summary[:120]}")
    if operator_queue_count > 0:
        lines.append(f"待处理操作: {operator_queue_count}")
    if operator_queue_next:
        lines.append(f"操作队列下一项: {operator_queue_next[:120]}")
    if last_operator_resolution:
        lines.append(f"最近操作结论: {last_operator_resolution}")
    if last_operator_resolution_summary:
        lines.append(f"操作结论摘要: {last_operator_resolution_summary[:120]}")
    for scope_payload in (current_delegation, current_run, current_job):
        scope_lines = _scope_lines(scope_payload, fullwidth_colon=fullwidth_colon)
        if scope_lines:
            lines.extend(scope_lines)
            break
    if not current_run and not current_job:
        scope_lines = []
        colon = "：" if fullwidth_colon else ":"
        task_scope_key = str(control_summary.get("task_scope_key") or "").strip()
        person_memory_key = str(control_summary.get("person_memory_key") or "").strip()
        if task_scope_key:
            scope_lines.append(f"Task scope{colon} {task_scope_key[:120]}")
        if person_memory_key:
            scope_lines.append(f"Person memory{colon} {person_memory_key[:120]}")
        if scope_lines:
            lines.extend(scope_lines)
    if artifact_items:
        lines.append("最近产物:")
        for artifact in sorted_artifact_items(artifact_items)[:3]:
            lines.append(format_artifact_line(artifact, fullwidth_colon=fullwidth_colon))
    return "\n".join(lines)


def format_approval_list_text(
    approvals: List[Dict[str, Any]],
    *,
    task_id: str = "",
    task_title: str = "",
    scope_all: bool = False,
) -> str:
    if not approvals:
        return "当前没有待处理审批。"
    lines = ["审批列表（全部）：" if scope_all else "当前任务审批列表：" if task_id else "当前会话审批列表："]
    if task_id:
        lines.append(f"任务：{task_title or task_id}")
    for item in approvals[:8]:
        payload = item.get("payload") if isinstance(item.get("payload"), dict) else {}
        capability = str(payload.get("capability") or item.get("kind") or "-").strip()
        title = str(payload.get("title") or item.get("target_id") or "-").strip()
        requested_by = str(item.get("requested_by") or "").strip()
        status = str(item.get("status") or "").strip()
        detail = f"- {item.get('approval_id')} | {status} | {capability} | {title[:48]}"
        if requested_by:
            detail += f" | requested_by={requested_by}"
        lines.append(detail)
    return "\n".join(lines)


def _origin_line(payload: Dict[str, Any], *, fullwidth_colon: bool = False) -> str:
    origin = payload.get("origin") if isinstance(payload.get("origin"), dict) else {}
    origin_platform = str(origin.get("platform") or "").strip()
    origin_chat_name = str(origin.get("chat_name") or "").strip()
    origin_chat_id = str(origin.get("chat_id") or "").strip()
    if not (origin_platform or origin_chat_name or origin_chat_id):
        return ""
    colon = "：" if fullwidth_colon else ":"
    return f"来源{colon} {origin_platform or '-'} / {origin_chat_name or origin_chat_id or '-'}"


def _scope_lines(payload: Dict[str, Any], *, fullwidth_colon: bool = False) -> List[str]:
    if not isinstance(payload, dict) or not payload:
        return []
    colon = "：" if fullwidth_colon else ":"
    lines: List[str] = []
    if str(payload.get("worker_role") or payload.get("role_title") or "").strip():
        task_scope_key = str(payload.get("task_scope_key") or "").strip()
        person_memory_key = str(payload.get("person_memory_key") or "").strip()
    elif str(payload.get("job_id") or "").strip():
        task_scope_key = extract_background_job_task_scope_key(payload)
        person_memory_key = extract_background_job_person_memory_key(payload)
    else:
        task_scope_key = extract_capability_run_task_scope_key(payload)
        person_memory_key = extract_capability_run_person_memory_key(payload)
    if task_scope_key:
        lines.append(f"Task scope{colon} {task_scope_key[:120]}")
    if person_memory_key:
        lines.append(f"Person memory{colon} {person_memory_key[:120]}")
    return lines


def format_capability_run_snapshot_lines(
    run: Dict[str, Any],
    *,
    fullwidth_colon: bool = False,
) -> List[str]:
    colon = "：" if fullwidth_colon else ":"
    lines = [
        f"Capability{colon} {str(run.get('capability_name') or '-').strip() or '-'}",
        f"Run{colon} {str(run.get('run_id') or '-').strip() or '-'}",
        f"Trace{colon} {str(run.get('trace_id') or '-').strip() or '-'}",
        f"状态{colon} {str(run.get('status') or '-').strip() or '-'}",
    ]
    title = str(run.get("title") or "").strip()
    if title:
        lines.append(f"标题{colon} {title[:80]}")
    task_id = str(run.get("task_id") or "").strip()
    task_title = str(run.get("task_title") or "").strip()
    if task_id:
        lines.append(f"任务{colon} {task_title or task_id}")
    approval_id = str(run.get("approval_id") or "").strip()
    if approval_id:
        lines.append(f"审批ID{colon} {approval_id}")
    focus = str(run.get("current_focus") or "").strip()
    next_step = str(run.get("next_step") or "").strip()
    blocker = str(run.get("blocker") or "").strip()
    result = str(run.get("result") or "").strip()
    if focus:
        lines.append(f"当前{colon} {focus[:120]}")
    if next_step:
        lines.append(f"下一步{colon} {next_step[:120]}")
    if blocker:
        lines.append(f"阻塞{colon} {blocker[:120]}")
    if result:
        lines.append(f"结果摘要{colon} {result[:120]}")
    lines.extend(_scope_lines(run, fullwidth_colon=fullwidth_colon))
    artifact_items = run.get("artifact_items") if isinstance(run.get("artifact_items"), list) else []
    for artifact in sorted_artifact_items(artifact_items)[:3]:
        lines.append(format_artifact_line(artifact, fullwidth_colon=fullwidth_colon))
    origin_line = _origin_line(run, fullwidth_colon=fullwidth_colon)
    if origin_line:
        lines.append(origin_line)
    if str(run.get("shared_scope") or "").strip().lower() == "global":
        lines.append("说明：这是跨端共享 run 视图。" if fullwidth_colon else "说明: 这是跨端共享 run 视图。")
    return lines


def format_capability_run_snapshot(
    run: Dict[str, Any],
    *,
    fullwidth_colon: bool = False,
) -> str:
    return "\n".join(format_capability_run_snapshot_lines(run, fullwidth_colon=fullwidth_colon))


def format_background_job_snapshot_lines(
    job: Dict[str, Any],
    *,
    fullwidth_colon: bool = False,
) -> List[str]:
    colon = "：" if fullwidth_colon else ":"
    tags = [str(item).strip().lower() for item in (job.get("tags") or []) if str(item).strip()]
    worker_kind = ""
    for tag in tags:
        if tag.startswith("worker_kind:"):
            worker_kind = tag.split(":", 1)[1].strip()
            break
    lines = [
        f"后台研究任务{colon} {str(job.get('status') or '-').strip() or '-'}",
        f"任务ID{colon} {str(job.get('job_id') or '-').strip() or '-'}",
        f"Trace{colon} {str(job.get('trace_id') or '-').strip() or '-'}",
        f"后台执行器{colon} {str(job.get('executor') or '-').strip() or '-'}",
    ]
    if worker_kind:
        lines.append(f"Worker kind{colon} {worker_kind}")
    title = str(job.get("title") or "").strip()
    if title:
        lines.append(f"标题{colon} {title[:80]}")
    focus = str(job.get("current_focus") or "").strip()
    next_step = str(job.get("next_step") or "").strip()
    blocker = str(job.get("blocker") or "").strip()
    if focus:
        lines.append(f"当前{colon} {focus[:120]}")
    if next_step:
        lines.append(f"下一步{colon} {next_step[:120]}")
    if blocker:
        lines.append(f"阻塞{colon} {blocker[:120]}")
    capability_name = str(job.get("capability_name") or "").strip()
    capability_status = str(job.get("capability_status") or "").strip()
    capability_run_id = str(job.get("capability_run_id") or "").strip()
    task_id = str(job.get("task_id") or "").strip()
    task_title = str(job.get("task_title") or "").strip()
    approval_id = str(job.get("approval_id") or "").strip()
    capability_result = str(job.get("capability_result") or "").strip()
    capability_focus = str(job.get("capability_focus") or "").strip()
    capability_next_step = str(job.get("capability_next_step") or "").strip()
    capability_blocker = str(job.get("capability_blocker") or "").strip()
    if capability_name or capability_status:
        lines.append(f"Capability{colon} {capability_name or '-'} / {capability_status or '-'}")
    if capability_run_id:
        lines.append(f"Run{colon} {capability_run_id}")
    if task_id:
        lines.append(f"任务{colon} {task_title or task_id}")
    if approval_id:
        lines.append(f"Approval{colon} {approval_id}")
    if capability_focus:
        lines.append(f"Run focus{colon} {capability_focus[:120]}")
    if capability_next_step:
        lines.append(f"Run next{colon} {capability_next_step[:120]}")
    if capability_blocker:
        lines.append(f"Run blocker{colon} {capability_blocker[:120]}")
    if capability_result:
        lines.append(f"Result{colon} {capability_result[:120]}")
    lines.extend(_scope_lines(job, fullwidth_colon=fullwidth_colon))
    artifact_items = job.get("artifact_items") if isinstance(job.get("artifact_items"), list) else []
    for artifact in sorted_artifact_items(artifact_items)[:3]:
        lines.append(format_artifact_line(artifact, fullwidth_colon=fullwidth_colon))
    origin_line = _origin_line(job, fullwidth_colon=fullwidth_colon)
    if origin_line:
        lines.append(origin_line)
    if str(job.get("shared_scope") or "").strip().lower() == "global":
        lines.append("说明：这是跨端共享任务视图。" if fullwidth_colon else "说明: 这是跨端共享任务视图。")
    return lines


def format_background_job_snapshot(
    job: Dict[str, Any],
    *,
    fullwidth_colon: bool = False,
) -> str:
    return "\n".join(format_background_job_snapshot_lines(job, fullwidth_colon=fullwidth_colon))


def build_activity_snapshot_lines(
    background_job: Dict[str, Any] | None = None,
    capability_run: Dict[str, Any] | None = None,
    *,
    fullwidth_colon: bool = False,
) -> List[str]:
    if isinstance(background_job, dict) and background_job:
        return format_background_job_snapshot_lines(background_job, fullwidth_colon=fullwidth_colon)
    if isinstance(capability_run, dict) and capability_run:
        return format_capability_run_snapshot_lines(capability_run, fullwidth_colon=fullwidth_colon)
    return []


def build_activity_snapshot_text(
    background_job: Dict[str, Any] | None = None,
    capability_run: Dict[str, Any] | None = None,
    *,
    fullwidth_colon: bool = False,
) -> str:
    return "\n".join(
        build_activity_snapshot_lines(
            background_job,
            capability_run,
            fullwidth_colon=fullwidth_colon,
        )
    )
