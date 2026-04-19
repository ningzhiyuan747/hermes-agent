from __future__ import annotations

from datetime import datetime
from typing import Any

from agent.approval_scope_service import list_scoped_approvals
from agent.business_db import (
    DISTILLED_PROFILE_FIELDS,
    bind_channel_task,
    clear_user_distilled_profile_override,
    create_task,
    decide_approval,
    discard_user_distilled_profile_draft,
    get_channel_task,
    get_task,
    get_user,
    get_user_distilled_profile,
    get_user_distilled_profile_draft,
    list_user_distilled_profile_history,
    list_channels,
    list_task_channels,
    publish_user_distilled_profile_draft,
    set_user_distilled_profile_overrides,
    unbind_channel_task,
)
from agent.incoming_message import IncomingMessage
from agent.outbound_delivery import DeliveryTarget, send_text_to_target
from agent.task_panel_service import get_task_panel_snapshot
from agent.task_panels import format_approval_list_text, format_task_panel_snapshot


PROFILE_FIELD_LABELS = {
    "core_principles": "核心原则",
    "working_style": "工作风格",
    "preferred_output": "输出偏好",
    "domain_focus": "领域重点",
    "decision_heuristics": "决策启发",
    "approval_sensitivity": "审批敏感度",
    "anti_patterns": "避免事项",
    "stable_instructions": "稳定指令",
}
PROFILE_FIELD_ALIASES = {
    "核心原则": "core_principles",
    "原则": "core_principles",
    "工作风格": "working_style",
    "风格": "working_style",
    "输出偏好": "preferred_output",
    "输出": "preferred_output",
    "回复偏好": "preferred_output",
    "领域重点": "domain_focus",
    "领域": "domain_focus",
    "重点领域": "domain_focus",
    "决策启发": "decision_heuristics",
    "决策": "decision_heuristics",
    "启发": "decision_heuristics",
    "审批敏感度": "approval_sensitivity",
    "审批": "approval_sensitivity",
    "避免事项": "anti_patterns",
    "禁忌": "anti_patterns",
    "反模式": "anti_patterns",
    "稳定指令": "stable_instructions",
    "指令": "stable_instructions",
    "coreprinciples": "core_principles",
    "workingstyle": "working_style",
    "preferredoutput": "preferred_output",
    "domainfocus": "domain_focus",
    "decisionheuristics": "decision_heuristics",
    "approvalsensitivity": "approval_sensitivity",
    "antipatterns": "anti_patterns",
    "stableinstructions": "stable_instructions",
}
PROFILE_HISTORY_ACTION_LABELS = {
    "draft_saved": "保存草稿",
    "published": "发布画像",
    "draft_discarded": "丢弃草稿",
    "override_set": "手工覆盖",
    "override_cleared": "解除覆盖",
}


def _normalize_profile_field(field: str) -> str:
    raw = str(field or "").strip()
    if not raw:
        return ""
    compact = "".join(ch for ch in raw.lower() if ch not in {" ", "_", "-", "\t"})
    if compact in PROFILE_FIELD_ALIASES:
        return PROFILE_FIELD_ALIASES[compact]
    for key, value in PROFILE_FIELD_ALIASES.items():
        normalized_key = "".join(ch for ch in key.lower() if ch not in {" ", "_", "-", "\t"})
        if compact == normalized_key:
            return value
    if raw in DISTILLED_PROFILE_FIELDS:
        return raw
    return ""


def _distilled_profile_payload(record: dict[str, Any] | None) -> tuple[dict[str, Any], dict[str, Any], dict[str, Any], list[str]]:
    if not isinstance(record, dict):
        return {}, {}, {}, []
    memory = record.get("memory")
    if not isinstance(memory, dict):
        return {}, {}, {}, []
    profile = memory.get("profile") if isinstance(memory.get("profile"), dict) else {}
    if not profile:
        profile = {field: memory.get(field) for field in DISTILLED_PROFILE_FIELDS if memory.get(field) not in (None, "")}
    evidence = memory.get("evidence") if isinstance(memory.get("evidence"), dict) else {}
    sources = memory.get("sources") if isinstance(memory.get("sources"), dict) else {}
    locked_fields = memory.get("locked_fields") if isinstance(memory.get("locked_fields"), list) else []
    return profile, evidence, sources, [str(item).strip() for item in locked_fields if str(item).strip()]


def _format_distilled_profile(platform: str, user_id: str, record: dict[str, Any] | None) -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if not isinstance(record, dict):
        return header + "\n还没有蒸馏画像。先让系统积累一些任务和对话，再自动生成。"

    profile, _evidence, sources, locked_fields = _distilled_profile_payload(record)
    lines = [header]
    summary = str(record.get("summary") or "").strip()
    if summary:
        lines.append(f"摘要：{summary}")
    for field in DISTILLED_PROFILE_FIELDS:
        value = str(profile.get(field) or "").strip()
        if value:
            lines.append(f"- {PROFILE_FIELD_LABELS.get(field, field)}：{value}")
    if locked_fields:
        labels = [PROFILE_FIELD_LABELS.get(field, field) for field in locked_fields]
        lines.append(f"- 已锁字段：{'、'.join(labels)}")
    window_days = sources.get("source_window_days")
    if window_days:
        lines.append(f"- 证据窗口：最近 {int(window_days)} 天")
    return "\n".join(lines)


def _format_distilled_profile_draft(platform: str, user_id: str, record: dict[str, Any] | None) -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像草稿：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if not isinstance(record, dict):
        return header + "\n当前没有待发布的画像草稿。"
    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    sources = record.get("sources") if isinstance(record.get("sources"), dict) else {}
    lines = [header]
    summary = str(record.get("summary") or "").strip()
    if summary:
        lines.append(f"摘要：{summary}")
    for field in DISTILLED_PROFILE_FIELDS:
        value = str(profile.get(field) or "").strip()
        if value:
            lines.append(f"- {PROFILE_FIELD_LABELS.get(field, field)}：{value}")
    window_days = sources.get("source_window_days")
    if window_days:
        lines.append(f"- 证据窗口：最近 {int(window_days)} 天")
    updated_by = str(record.get("updated_by") or "").strip()
    if updated_by:
        lines.append(f"- 草稿来源：{updated_by}")
    lines.append("可用命令：发布画像、丢弃画像草稿、画像草稿证据 [字段]")
    return "\n".join(lines)


def _format_evidence_item(item: dict[str, Any]) -> str:
    kind = str(item.get("kind") or "").strip()
    if kind == "capability":
        label = str(item.get("label") or item.get("value") or "-").strip()
        count = int(item.get("count") or 0)
        return f"- 能力轨迹：{label}" + (f" x{count}" if count else "")
    if kind in {"task", "job"}:
        return f"- {kind}：{str(item.get('title') or '-').strip()}"
    if kind in {"profile_summary", "notes_summary"}:
        tag = "用户资料" if kind == "profile_summary" else "用户备注"
        return f"- {tag}：{str(item.get('text') or '-').strip()}"
    if kind == "approval_stats":
        return (
            f"- 审批统计：最近 run {int(item.get('total_runs') or 0)} 个，"
            f"带审批 {int(item.get('approval_runs') or 0)} 个，"
            f"审批取消 {int(item.get('denied_runs') or 0)} 个"
        )
    return f"- {kind or 'evidence'}：{str(item).strip()}"


def _format_distilled_profile_evidence(platform: str, user_id: str, record: dict[str, Any] | None, *, field: str = "") -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像证据：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if not isinstance(record, dict):
        return header + "\n还没有蒸馏画像，暂时也没有证据可看。"

    profile, evidence, sources, _locked_fields = _distilled_profile_payload(record)
    if field:
        label = PROFILE_FIELD_LABELS.get(field, field)
        value = str(profile.get(field) or "").strip()
        rows = evidence.get(field) if isinstance(evidence, dict) else None
        if not isinstance(rows, list) or not rows:
            return header + f"\n字段：{label}\n当前没有可展示的证据。"
        lines = [header, f"字段：{label}"]
        if value:
            lines.append(f"当前值：{value}")
        lines.extend(_format_evidence_item(item) for item in rows if isinstance(item, dict))
        return "\n".join(lines)

    lines = [header]
    for item_field in DISTILLED_PROFILE_FIELDS:
        rows = evidence.get(item_field) if isinstance(evidence, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(f"- {PROFILE_FIELD_LABELS.get(item_field, item_field)}：{len(rows)} 条证据")
    window_days = sources.get("source_window_days")
    if window_days:
        lines.append(f"- 证据窗口：最近 {int(window_days)} 天")
    if len(lines) == 1:
        lines.append("当前没有可展示的证据。")
    return "\n".join(lines)


def _format_distilled_profile_draft_evidence(platform: str, user_id: str, record: dict[str, Any] | None, *, field: str = "") -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像草稿证据：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if not isinstance(record, dict):
        return header + "\n当前没有待发布的画像草稿。"

    profile = record.get("profile") if isinstance(record.get("profile"), dict) else {}
    evidence = record.get("evidence") if isinstance(record.get("evidence"), dict) else {}
    sources = record.get("sources") if isinstance(record.get("sources"), dict) else {}
    if field:
        label = PROFILE_FIELD_LABELS.get(field, field)
        value = str(profile.get(field) or "").strip()
        rows = evidence.get(field) if isinstance(evidence, dict) else None
        if not isinstance(rows, list) or not rows:
            return header + f"\n字段：{label}\n当前没有可展示的草稿证据。"
        lines = [header, f"字段：{label}"]
        if value:
            lines.append(f"草稿值：{value}")
        lines.extend(_format_evidence_item(item) for item in rows if isinstance(item, dict))
        return "\n".join(lines)
    lines = [header]
    for item_field in DISTILLED_PROFILE_FIELDS:
        rows = evidence.get(item_field) if isinstance(evidence, dict) else None
        if not isinstance(rows, list) or not rows:
            continue
        lines.append(f"- {PROFILE_FIELD_LABELS.get(item_field, item_field)}：{len(rows)} 条证据")
    window_days = sources.get("source_window_days")
    if window_days:
        lines.append(f"- 证据窗口：最近 {int(window_days)} 天")
    if len(lines) == 1:
        lines.append("当前没有可展示的草稿证据。")
    return "\n".join(lines)


def _format_distilled_profile_diff(
    platform: str,
    user_id: str,
    published_record: dict[str, Any] | None,
    draft_record: dict[str, Any] | None,
    *,
    field: str = "",
) -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像差异：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if not isinstance(draft_record, dict):
        return header + "\n当前没有待比较的画像草稿。"

    published_profile, _published_evidence, _published_sources, locked_fields = _distilled_profile_payload(published_record)
    draft_profile = draft_record.get("profile") if isinstance(draft_record.get("profile"), dict) else {}
    candidate_fields = [field] if field else list(DISTILLED_PROFILE_FIELDS)
    lines = [header]
    changed = 0
    for item_field in candidate_fields:
        label = PROFILE_FIELD_LABELS.get(item_field, item_field)
        before = str(published_profile.get(item_field) or "").strip()
        after = str(draft_profile.get(item_field) or "").strip()
        if not field and before == after:
            continue
        changed += 1
        status = "新增" if not before and after else "删除" if before and not after else "变更" if before != after else "无变化"
        lock_note = " [已锁]" if item_field in locked_fields else ""
        lines.append(f"- {label}{lock_note}：{status}")
        lines.append(f"  正式：{before or '（空）'}")
        lines.append(f"  草稿：{after or '（空）'}")
    if changed == 0:
        lines.append("草稿与正式画像当前没有差异。")
    else:
        lines.append(f"共 {changed} 个字段存在差异。")
        lines.append("可用命令：发布画像、丢弃画像草稿、画像草稿证据 [字段]")
    return "\n".join(lines)


def _format_distilled_profile_history(
    platform: str,
    user_id: str,
    entries: list[dict[str, Any]],
    *,
    field: str = "",
) -> str:
    user = get_user(platform=platform, user_id=user_id) or {}
    display_name = str(user.get("display_name") or "").strip()
    header = f"画像历史：{display_name or user_id}"
    if display_name and display_name != user_id:
        header += f" ({user_id})"
    if field:
        header += f"\n字段：{PROFILE_FIELD_LABELS.get(field, field)}"
    if not entries:
        return header + "\n当前还没有可展示的画像变更记录。"

    lines = [header]
    matched = 0
    for entry in entries:
        fields = [str(item).strip() for item in entry.get("field_names", []) if str(item).strip()]
        if field and field not in fields:
            continue
        matched += 1
        timestamp_unix = int(entry.get("timestamp_unix") or 0)
        timestamp_label = (
            datetime.fromtimestamp(timestamp_unix).strftime("%m-%d %H:%M")
            if timestamp_unix > 0
            else "-"
        )
        action = PROFILE_HISTORY_ACTION_LABELS.get(str(entry.get("action") or "").strip(), str(entry.get("action") or "").strip() or "变更")
        actor = str(entry.get("actor") or "").strip() or "-"
        lines.append(f"- [{timestamp_label}] {action} by {actor}")
        if fields:
            labels = [PROFILE_FIELD_LABELS.get(item, item) for item in fields]
            lines.append(f"  字段：{'、'.join(labels)}")
        summary = str(entry.get("summary") or "").strip()
        if summary:
            lines.append(f"  摘要：{summary}")
    if matched == 0:
        lines.append("当前没有匹配该字段的画像变更记录。")
    return "\n".join(lines)


def execute_distilled_profile_command(
    message: IncomingMessage,
    command: dict[str, Any],
    *,
    can_manage_profiles: bool = True,
) -> str:
    action = str(command.get("action") or "").strip().lower()
    target_user_id = str(command.get("user_id") or message.user_id or "").strip()
    if not target_user_id:
        return "没有可用的用户 ID，无法读取画像。"
    is_self = target_user_id == str(message.user_id or "").strip()

    if action == "show":
        if not is_self and not can_manage_profiles:
            return "查看他人画像只允许 Owner/Admin 使用。"
        published = get_user_distilled_profile(platform=message.platform, user_id=target_user_id)
        draft = get_user_distilled_profile_draft(platform=message.platform, user_id=target_user_id)
        text = _format_distilled_profile(message.platform, target_user_id, published)
        if draft:
            text += "\n\n有新的画像草稿待发布。可用：画像草稿、发布画像、丢弃画像草稿。"
        return text

    if action == "draft":
        if not is_self and not can_manage_profiles:
            return "查看他人画像草稿只允许 Owner/Admin 使用。"
        return _format_distilled_profile_draft(
            message.platform,
            target_user_id,
            get_user_distilled_profile_draft(platform=message.platform, user_id=target_user_id),
        )

    if action == "draft-evidence":
        if not is_self and not can_manage_profiles:
            return "查看他人画像草稿证据只允许 Owner/Admin 使用。"
        field = _normalize_profile_field(str(command.get("field") or ""))
        raw_field = str(command.get("field") or "").strip()
        if raw_field and not field:
            labels = "、".join(PROFILE_FIELD_LABELS.values())
            return f"画像字段不支持。可用字段：{labels}"
        return _format_distilled_profile_draft_evidence(
            message.platform,
            target_user_id,
            get_user_distilled_profile_draft(platform=message.platform, user_id=target_user_id),
            field=field,
        )

    if action == "diff":
        if not is_self and not can_manage_profiles:
            return "查看他人画像差异只允许 Owner/Admin 使用。"
        field = _normalize_profile_field(str(command.get("field") or ""))
        raw_field = str(command.get("field") or "").strip()
        if raw_field and not field:
            labels = "、".join(PROFILE_FIELD_LABELS.values())
            return f"画像字段不支持。可用字段：{labels}"
        return _format_distilled_profile_diff(
            message.platform,
            target_user_id,
            get_user_distilled_profile(platform=message.platform, user_id=target_user_id),
            get_user_distilled_profile_draft(platform=message.platform, user_id=target_user_id),
            field=field,
        )

    if action == "evidence":
        if not is_self and not can_manage_profiles:
            return "查看他人画像证据只允许 Owner/Admin 使用。"
        field = _normalize_profile_field(str(command.get("field") or ""))
        raw_field = str(command.get("field") or "").strip()
        if raw_field and not field:
            labels = "、".join(PROFILE_FIELD_LABELS.values())
            return f"画像字段不支持。可用字段：{labels}"
        return _format_distilled_profile_evidence(
            message.platform,
            target_user_id,
            get_user_distilled_profile(platform=message.platform, user_id=target_user_id),
            field=field,
        )

    if action == "history":
        if not is_self and not can_manage_profiles:
            return "查看他人画像历史只允许 Owner/Admin 使用。"
        field = _normalize_profile_field(str(command.get("field") or ""))
        raw_field = str(command.get("field") or "").strip()
        if raw_field and not field:
            labels = "、".join(PROFILE_FIELD_LABELS.values())
            return f"画像字段不支持。可用字段：{labels}"
        return _format_distilled_profile_history(
            message.platform,
            target_user_id,
            list_user_distilled_profile_history(platform=message.platform, user_id=target_user_id, limit=10),
            field=field,
        )

    if action == "publish":
        if not is_self and not can_manage_profiles:
            return "发布他人画像草稿只允许 Owner/Admin 使用。"
        try:
            record = publish_user_distilled_profile_draft(
                platform=message.platform,
                user_id=target_user_id,
                published_by=message.user_id or "profile_publish",
            )
        except ValueError:
            return "当前没有待发布的画像草稿。"
        return "已发布画像草稿。\n" + _format_distilled_profile(message.platform, target_user_id, record)

    if action == "discard-draft":
        if not is_self and not can_manage_profiles:
            return "丢弃他人画像草稿只允许 Owner/Admin 使用。"
        discard_user_distilled_profile_draft(
            platform=message.platform,
            user_id=target_user_id,
            discarded_by=message.user_id or "profile_discard",
        )
        return "已丢弃画像草稿。"

    field = _normalize_profile_field(str(command.get("field") or ""))
    if not field:
        labels = "、".join(PROFILE_FIELD_LABELS.values())
        return f"画像字段不支持。可用字段：{labels}"

    if not is_self and not can_manage_profiles:
        return "修改他人画像只允许 Owner/Admin 使用。"

    if action in {"set", "lock"}:
        value = str(command.get("value") or "").strip()
        if not value:
            return "画像字段值不能为空。"
        record = set_user_distilled_profile_overrides(
            platform=message.platform,
            user_id=target_user_id,
            overrides={field: value},
            locked_fields=[field] if action == "lock" else None,
        )
        prefix = "已锁定并更新画像字段" if action == "lock" else "已更新画像字段"
        return f"{prefix} {PROFILE_FIELD_LABELS.get(field, field)}。\n" + _format_distilled_profile(message.platform, target_user_id, record)

    if action == "unlock":
        existing = get_user_distilled_profile(platform=message.platform, user_id=target_user_id)
        if not isinstance(existing, dict):
            return "还没有蒸馏画像，无法解锁。"
        record = clear_user_distilled_profile_override(
            platform=message.platform,
            user_id=target_user_id,
            field=field,
            unlock=True,
        )
        return f"已解锁画像字段 {PROFILE_FIELD_LABELS.get(field, field)}。\n" + _format_distilled_profile(message.platform, target_user_id, record)

    return f"未知画像动作：{action or '-'}"


def incoming_message_from_payload(payload: dict[str, Any]) -> IncomingMessage:
    metadata = payload.get("metadata") if isinstance(payload.get("metadata"), dict) else {}
    return IncomingMessage(
        platform=str(payload.get("platform") or payload.get("source_platform") or "").strip().lower(),
        chat_id=str(payload.get("chat_id") or payload.get("source_chat_id") or "").strip(),
        thread_id=str(payload.get("thread_id") or payload.get("source_thread_id") or "").strip(),
        chat_type=str(payload.get("chat_type") or "").strip(),
        chat_name=str(payload.get("chat_name") or "").strip(),
        user_id=str(
            payload.get("user_id")
            or payload.get("owner_user_id")
            or payload.get("approved_by")
            or ""
        ).strip(),
        user_name=str(payload.get("user_name") or "").strip(),
        text=str(payload.get("text") or "").strip(),
        session_key=str(payload.get("session_key") or payload.get("session_id") or "").strip(),
        metadata=dict(metadata),
    )

def _channel_line(channel: dict[str, Any]) -> str:
    chat_name = str(channel.get("chat_name") or "").strip() or "-"
    chat_type = str(channel.get("chat_type") or "").strip() or "-"
    task_id = str(channel.get("task_id") or "").strip() or "-"
    return f"- {DeliveryTarget.from_channel(channel).to_target_ref()} | 名称: {chat_name} | 类型: {chat_type} | task: {task_id}"


def _resolve_bound_task(message: IncomingMessage) -> tuple[str, dict[str, Any] | None]:
    record = get_channel_task(platform=message.platform, chat_id=message.chat_id, thread_id=message.thread_id)
    task = record.get("task") if isinstance(record, dict) else None
    if not isinstance(task, dict):
        return "", None
    task_id = str(task.get("task_id") or "").strip()
    if not task_id:
        return "", None
    return task_id, task


def _build_task_links_text(task_id: str, channels: list[dict[str, Any]], task: dict[str, Any] | None = None) -> str:
    title = str((task or {}).get("title") or "").strip()
    header = f"任务 {task_id} 已绑定频道："
    lines = [header]
    if title:
        lines.append(f"标题: {title}")
    lines.extend(_channel_line(channel) for channel in channels)
    return "\n".join(lines)


def execute_task_command(
    message: IncomingMessage,
    command: dict[str, Any],
    *,
    can_manage_bindings: bool = True,
) -> str:
    action = str(command.get("action") or "").strip()
    title = str(command.get("title") or "").strip()
    task_id = str(command.get("task_id") or "").strip()

    if action == "current":
        if message.is_private_chat():
            return "当前是私聊，默认走个人上下文。\n如需协作，请先在任务群里“建任务 <标题>”或“绑定任务 <task-id>”。"
        record = get_channel_task(platform=message.platform, chat_id=message.chat_id, thread_id=message.thread_id)
        task = record.get("task") if isinstance(record, dict) else None
        if not isinstance(task, dict):
            return "当前群还没有绑定任务。\n可用：建任务 <标题> 或 绑定任务 <task-id>"
        snapshot = get_task_panel_snapshot(str(task.get("task_id") or "").strip(), active_only=False) or {}
        if not isinstance(snapshot.get("task"), dict):
            snapshot["task"] = task
        return format_task_panel_snapshot(snapshot)

    if action == "create":
        task = create_task(
            title=title,
            goal=title,
            owner_user_id=message.user_id,
            source_platform=message.platform,
            source_chat_id=message.chat_id,
            source_thread_id=message.thread_id,
            source_session_id=message.session_key,
        )
        bound = False
        if not message.is_private_chat():
            bind_channel_task(
                platform=message.platform,
                chat_id=message.chat_id,
                thread_id=message.thread_id,
                task_id=str(task.get("task_id") or "").strip(),
            )
            bound = True
        lines = ["任务已创建。", f"Task ID: {str(task.get('task_id') or '').strip()}"]
        if str(task.get("title") or "").strip():
            lines.append(f"标题: {str(task.get('title') or '').strip()}")
        lines.append("当前群已绑定到该任务。" if bound else "当前是私聊，未绑定群；后续可在任务群里发：绑定任务 <task-id>")
        return "\n".join(lines)

    if action == "unbind":
        if not can_manage_bindings:
            return "绑定任务只允许 Owner/Admin 使用。"
        if message.is_private_chat():
            return "私聊没有任务群绑定，不需要解绑。"
        unbind_channel_task(platform=message.platform, chat_id=message.chat_id, thread_id=message.thread_id)
        return "当前群的任务绑定已解除。"

    if action == "bind":
        if not can_manage_bindings:
            return "绑定任务只允许 Owner/Admin 使用。"
        if message.is_private_chat():
            return "私聊不需要绑定任务群；请在目标任务群里执行这个命令。"
        task = get_task(task_id)
        if not task:
            return f"没找到任务：{task_id}"
        bind_channel_task(platform=message.platform, chat_id=message.chat_id, thread_id=message.thread_id, task_id=task_id)
        lines = ["当前群已绑定任务。", f"Task ID: {task_id}"]
        if str(task.get("title") or "").strip():
            lines.append(f"标题: {str(task.get('title') or '').strip()}")
        return "\n".join(lines)

    return f"不支持的任务动作：{action or '-'}"


def execute_approval_command(message: IncomingMessage, command: dict[str, Any]) -> str:
    action = str(command.get("action") or "").strip()
    scope = str(command.get("scope") or "").strip()
    approval_id = str(command.get("approval_id") or "").strip()
    if action == "list":
        approvals, task_id, task_title = list_scoped_approvals(
            platform=message.platform,
            chat_id=message.chat_id,
            thread_id=message.thread_id,
            scope_all=(scope == "all"),
            status="pending",
            limit=50,
        )
        text = format_approval_list_text(approvals, task_id=task_id, task_title=task_title, scope_all=(scope == "all"))
        if approvals:
            text += "\n发送“批准 approval-...”或“拒绝 approval-...”处理。"
        return text

    decision = "approved" if action == "approve" else "denied"
    record = decide_approval(approval_id, status=decision, approved_by=message.user_id)
    if not record:
        return f"未找到审批单：{approval_id}"
    payload_obj = record.get("payload") or {}
    capability = str(payload_obj.get("capability") or record.get("kind") or "-").strip()
    title = str(payload_obj.get("title") or record.get("target_id") or "-").strip()
    result_text = "已批准" if decision == "approved" else "已拒绝"
    return "{} {}。\n能力：{}\n标题：{}\nrun：{}".format(
        result_text,
        record.get("approval_id"),
        capability,
        title,
        record.get("target_id") or "-",
    )


def execute_channel_command(
    message: IncomingMessage,
    *,
    action: str,
    task_id: str = "",
    text: str = "",
    include_source: bool = False,
    limit: int = 50,
) -> str:
    normalized_action = str(action or "").strip().lower()

    if normalized_action == "list":
        channels = list_channels(limit=limit, platform=message.platform)
        if not channels:
            return "没有发现可用频道。"
        header = f"已发现频道（platform={message.platform}）：" if message.platform else "已发现频道："
        return "\n".join([header, *[_channel_line(channel) for channel in channels]])

    if normalized_action == "task-links":
        if not task_id:
            return "task_id 必填。"
        channels = list_task_channels(task_id, platform=message.platform, limit=limit)
        if not channels:
            return f"任务 {task_id} 还没有绑定任何频道。"
        return _build_task_links_text(task_id, channels, get_task(task_id))

    if normalized_action == "current-links":
        if message.is_private_chat():
            return "当前是私聊，默认不绑定任务群。\n如需查看三端绑定，请显式带上 task_id。"
        if not message.platform or not message.chat_id:
            return "platform/chat_id 必填。"
        bound_task_id, task = _resolve_bound_task(message)
        if not bound_task_id:
            return "当前频道还没有绑定任务。\n可用：建任务 <标题> 或 绑定任务 <task-id>"
        channels = list_task_channels(bound_task_id, limit=limit)
        if not channels:
            return f"任务 {bound_task_id} 还没有绑定任何频道。"
        return _build_task_links_text(bound_task_id, channels, task)

    if normalized_action == "bind-many":
        if not task_id:
            return "task_id 必填。"
        if not get_task(task_id):
            return f"没找到任务：{task_id}"
        channel_items = message.metadata.get("channels") if isinstance(message.metadata, dict) else []
        if not isinstance(channel_items, list):
            channel_items = []
        changed = 0
        for item in channel_items:
            if not isinstance(item, dict):
                continue
            bind_channel_task(
                platform=str(item.get("platform") or "").strip().lower(),
                chat_id=str(item.get("chat_id") or "").strip(),
                thread_id=str(item.get("thread_id") or "").strip(),
                task_id=task_id,
            )
            changed += 1
        return f"已绑定 {changed} 个频道到任务 {task_id}。"

    if normalized_action in {"broadcast", "broadcast-current"}:
        target_task_id = task_id
        if normalized_action == "broadcast-current":
            target_task_id, _task = _resolve_bound_task(message)
            if not target_task_id:
                return "当前频道还没有绑定任务。\n可用：建任务 <标题> 或 绑定任务 <task-id>"
        if not target_task_id:
            return "task_id 必填。"
        channels = list_task_channels(target_task_id, limit=limit)
        if not channels:
            return f"任务 {target_task_id} 还没有绑定任何频道。"
        delivered = 0
        skipped = 0
        for channel in channels:
            if not include_source and (
                str(channel.get("platform") or "").strip().lower() == message.platform
                and str(channel.get("chat_id") or "").strip() == message.chat_id
                and str(channel.get("thread_id") or "").strip() == message.thread_id
            ):
                skipped += 1
                continue
            result = send_text_to_target(DeliveryTarget.from_channel(channel), str(text or "").strip())
            if isinstance(result, dict) and not result.get("error"):
                delivered += 1
        return f"任务 {target_task_id} 已同步到 {delivered} 个频道。" + (f"\n跳过来源频道 {skipped} 个。" if skipped else "")

    return f"未知频道操作：{normalized_action}"


def run_task_command(payload: dict[str, Any]) -> str:
    message = incoming_message_from_payload(payload)
    return execute_task_command(
        message,
        {"action": payload.get("action"), "title": payload.get("title"), "task_id": payload.get("task_id")},
        can_manage_bindings=bool(payload.get("can_manage_bindings", True)),
    )


def run_approval_command(payload: dict[str, Any]) -> str:
    message = incoming_message_from_payload(payload)
    return execute_approval_command(
        message,
        {"action": payload.get("action"), "scope": payload.get("scope"), "approval_id": payload.get("approval_id")},
    )


def run_channel_command(payload: dict[str, Any]) -> str:
    message = incoming_message_from_payload(payload)
    if "channels" in payload:
        message_dict = message.to_dict()
        message_dict["metadata"] = {"channels": payload.get("channels") or []}
        message = IncomingMessage.from_dict(message_dict)
    return execute_channel_command(
        message,
        action=str(payload.get("action") or "").strip(),
        task_id=str(payload.get("task_id") or "").strip(),
        text=str(payload.get("message") or "").strip(),
        include_source=bool(payload.get("include_source")),
        limit=int(payload.get("limit") or 50),
    )
