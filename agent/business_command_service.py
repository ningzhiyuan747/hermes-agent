from __future__ import annotations

import re
from typing import Any

from agent.business_command_ops import (
    execute_approval_command,
    execute_channel_command,
    execute_distilled_profile_command,
    execute_task_command,
)
from agent.incoming_message import IncomingMessage


CURRENT_TASK_COMMANDS = {"当前任务", "本群任务", "task", "currenttask"}
TASK_BIND_PREFIXES = ("绑定任务", "关联任务", "挂任务", "bindtask")
TASK_UNBIND_COMMANDS = {"解绑任务", "取消绑定任务", "unbindtask"}
TASK_CREATE_PREFIXES = ("建任务", "创建任务", "新建任务", "新任务", "createtask")
TASK_LINK_COMMANDS = {"任务频道", "关联频道", "任务关联频道", "看同步", "tasklinks"}
TASK_BROADCAST_PREFIXES = ("任务广播", "同步到任务频道", "同步播报", "发同步", "tasksync")
APPROVAL_LIST_COMMANDS = {"审批列表", "待审批", "approvals", "pendingapprovals", "approvalstatus"}
APPROVAL_LIST_ALL_COMMANDS = {"审批列表全部", "待审批全部", "allapprovals", "approvalsall"}
PROFILE_SHOW_COMMANDS = {"画像", "看画像", "我的画像", "我的蒸馏", "我的风格", "profile", "myprofile"}
PROFILE_EVIDENCE_COMMANDS = {"画像证据", "蒸馏证据", "profileevidence"}
PROFILE_HISTORY_COMMANDS = {"画像历史", "蒸馏历史", "profilehistory"}
PROFILE_DRAFT_COMMANDS = {"画像草稿", "蒸馏草稿", "profiledraft"}
PROFILE_DRAFT_EVIDENCE_COMMANDS = {"画像草稿证据", "蒸馏草稿证据", "profiledraftevidence"}
PROFILE_DIFF_COMMANDS = {"画像差异", "画像草稿差异", "profilediff"}


def normalize_command_text(text: str) -> str:
    return re.sub(r"\s+", "", str(text or "").strip()).lower()


def extract_prefixed_text(text: str, prefixes: tuple[str, ...]) -> str:
    raw = str(text or "").strip()
    if not raw:
        return ""
    lowered = raw.lower()
    for prefix in prefixes:
        if lowered.startswith(prefix.lower()):
            return raw[len(prefix):].strip(" ：:")
    return ""


def parse_business_approval_text(text: str) -> dict[str, str] | None:
    stripped = str(text or "").strip()
    compact = normalize_command_text(stripped)
    if compact in {normalize_command_text(item) for item in APPROVAL_LIST_COMMANDS}:
        return {"action": "list", "scope": "chat"}
    if compact in {normalize_command_text(item) for item in APPROVAL_LIST_ALL_COMMANDS}:
        return {"action": "list", "scope": "all"}
    approved = re.match(r"^(批准|通过|approve)\s*([A-Za-z0-9_.:-]+)\s*$", stripped, re.IGNORECASE)
    if approved:
        return {"action": "approve", "approval_id": approved.group(2)}
    denied = re.match(r"^(拒绝|驳回|deny)\s*([A-Za-z0-9_.:-]+)\s*$", stripped, re.IGNORECASE)
    if denied:
        return {"action": "deny", "approval_id": denied.group(2)}
    return None


def parse_business_task_text(text: str) -> dict[str, str] | None:
    compact = normalize_command_text(text)
    if compact in {normalize_command_text(item) for item in CURRENT_TASK_COMMANDS}:
        return {"action": "current"}
    if compact in {normalize_command_text(item) for item in TASK_LINK_COMMANDS}:
        return {"action": "links"}
    if compact in {normalize_command_text(item) for item in TASK_UNBIND_COMMANDS}:
        return {"action": "unbind"}
    title = extract_prefixed_text(text, TASK_CREATE_PREFIXES)
    if title:
        return {"action": "create", "title": title}
    task_id = extract_prefixed_text(text, TASK_BIND_PREFIXES)
    if task_id:
        return {"action": "bind", "task_id": task_id}
    message = extract_prefixed_text(text, TASK_BROADCAST_PREFIXES)
    if message:
        return {"action": "broadcast", "message": message}
    return None


def parse_business_profile_text(text: str) -> dict[str, str] | None:
    stripped = str(text or "").strip()
    compact = normalize_command_text(stripped)
    simple_profile_commands = (
        (PROFILE_SHOW_COMMANDS, {"action": "show", "user_id": ""}),
        (PROFILE_EVIDENCE_COMMANDS, {"action": "evidence", "user_id": "", "field": ""}),
        (PROFILE_HISTORY_COMMANDS, {"action": "history", "user_id": "", "field": ""}),
        (PROFILE_DRAFT_COMMANDS, {"action": "draft", "user_id": ""}),
        (PROFILE_DRAFT_EVIDENCE_COMMANDS, {"action": "draft-evidence", "user_id": "", "field": ""}),
        (PROFILE_DIFF_COMMANDS, {"action": "diff", "user_id": "", "field": ""}),
    )
    for commands, payload in simple_profile_commands:
        if compact in {normalize_command_text(item) for item in commands}:
            return dict(payload)
    if compact in {"发布画像", "发布画像草稿", "publishprofile"}:
        return {"action": "publish", "user_id": ""}
    if compact in {"丢弃画像草稿", "废弃画像草稿", "discardprofiledraft"}:
        return {"action": "discard-draft", "user_id": ""}

    evidence_show_match = re.match(r"^(画像证据|蒸馏证据|profileevidence)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if evidence_show_match:
        return {"action": "evidence", "user_id": evidence_show_match.group(2), "field": ""}
    evidence_targeted = re.match(
        r"^(画像证据|蒸馏证据|profileevidence)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if evidence_targeted:
        return {"action": "evidence", "user_id": evidence_targeted.group(2), "field": evidence_targeted.group(3)}
    evidence_direct = re.match(r"^(画像证据|蒸馏证据|profileevidence)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$", stripped, re.IGNORECASE)
    if evidence_direct:
        return {"action": "evidence", "user_id": "", "field": evidence_direct.group(2)}
    history_show_match = re.match(r"^(画像历史|蒸馏历史|profilehistory)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if history_show_match:
        return {"action": "history", "user_id": history_show_match.group(2), "field": ""}
    history_direct = re.match(r"^(画像历史|蒸馏历史|profilehistory)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$", stripped, re.IGNORECASE)
    if history_direct:
        return {"action": "history", "user_id": "", "field": history_direct.group(2)}
    history_targeted = re.match(
        r"^(画像历史|蒸馏历史|profilehistory)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if history_targeted:
        return {"action": "history", "user_id": history_targeted.group(2), "field": history_targeted.group(3)}
    draft_evidence_show_match = re.match(r"^(画像草稿证据|蒸馏草稿证据|profiledraftevidence)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if draft_evidence_show_match:
        return {"action": "draft-evidence", "user_id": draft_evidence_show_match.group(2), "field": ""}
    draft_evidence_targeted = re.match(
        r"^(画像草稿证据|蒸馏草稿证据|profiledraftevidence)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if draft_evidence_targeted:
        return {"action": "draft-evidence", "user_id": draft_evidence_targeted.group(2), "field": draft_evidence_targeted.group(3)}
    draft_evidence_direct = re.match(r"^(画像草稿证据|蒸馏草稿证据|profiledraftevidence)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$", stripped, re.IGNORECASE)
    if draft_evidence_direct:
        return {"action": "draft-evidence", "user_id": "", "field": draft_evidence_direct.group(2)}
    diff_show_match = re.match(r"^(画像差异|画像草稿差异|profilediff)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if diff_show_match:
        return {"action": "diff", "user_id": diff_show_match.group(2), "field": ""}
    diff_direct = re.match(r"^(画像差异|画像草稿差异|profilediff)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$", stripped, re.IGNORECASE)
    if diff_direct:
        return {"action": "diff", "user_id": "", "field": diff_direct.group(2)}
    diff_targeted = re.match(
        r"^(画像差异|画像草稿差异|profilediff)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$",
        stripped,
        re.IGNORECASE,
    )
    if diff_targeted:
        return {"action": "diff", "user_id": diff_targeted.group(2), "field": diff_targeted.group(3)}
    draft_show_match = re.match(r"^(画像草稿|蒸馏草稿|profiledraft)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if draft_show_match:
        return {"action": "draft", "user_id": draft_show_match.group(2)}
    publish_match = re.match(r"^(发布画像|发布画像草稿|publishprofile)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if publish_match:
        return {"action": "publish", "user_id": publish_match.group(2)}
    discard_match = re.match(r"^(丢弃画像草稿|废弃画像草稿|discardprofiledraft)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if discard_match:
        return {"action": "discard-draft", "user_id": discard_match.group(2)}

    show_match = re.match(r"^(画像|看画像|profile)\s+([A-Za-z0-9_.:@-]+)\s*$", stripped, re.IGNORECASE)
    if show_match:
        return {"action": "show", "user_id": show_match.group(2)}

    direct_patterns = (
        ("set", r"^(设画像|改画像|setprofile)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*=\s*(.+)\s*$"),
        ("lock", r"^(锁画像|锁定画像|lockprofile)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*=\s*(.+)\s*$"),
        ("unlock", r"^(解锁画像|unlockprofile)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$"),
    )
    targeted_patterns = (
        ("set", r"^(设画像|改画像|setprofile)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*=\s*(.+)\s*$"),
        ("lock", r"^(锁画像|锁定画像|lockprofile)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*=\s*(.+)\s*$"),
        ("unlock", r"^(解锁画像|unlockprofile)\s+([A-Za-z0-9_.:@-]+)\s+([\u4e00-\u9fffA-Za-z_ -]+)\s*$"),
    )

    for action, pattern in targeted_patterns:
        matched = re.match(pattern, stripped, re.IGNORECASE)
        if not matched:
            continue
        if action == "unlock":
            return {"action": action, "user_id": matched.group(2), "field": matched.group(3)}
        return {"action": action, "user_id": matched.group(2), "field": matched.group(3), "value": matched.group(4)}

    for action, pattern in direct_patterns:
        matched = re.match(pattern, stripped, re.IGNORECASE)
        if not matched:
            continue
        if action == "unlock":
            return {"action": action, "user_id": "", "field": matched.group(2)}
        return {"action": action, "user_id": "", "field": matched.group(2), "value": matched.group(3)}

    return None


def dispatch_business_text_command(
    message: IncomingMessage,
    *,
    can_manage_bindings: bool = True,
    can_manage_profiles: bool | None = None,
) -> str | None:
    approval_command = parse_business_approval_text(message.text)
    if approval_command:
        return execute_approval_command(message, approval_command)

    profile_command = parse_business_profile_text(message.text)
    if profile_command:
        return execute_distilled_profile_command(
            message,
            profile_command,
            can_manage_profiles=can_manage_bindings if can_manage_profiles is None else bool(can_manage_profiles),
        )

    task_command = parse_business_task_text(message.text)
    if not task_command:
        return None

    action = str(task_command.get("action") or "").strip()
    if action == "links":
        return execute_channel_command(message, action="current-links")
    if action == "broadcast":
        return execute_channel_command(
            message,
            action="broadcast-current",
            text=str(task_command.get("message") or "").strip(),
            include_source=False,
        )
    return execute_task_command(message, task_command, can_manage_bindings=can_manage_bindings)


def dispatch_tasksync_command(message: IncomingMessage, raw_args: str, *, usage_command: str = "/tasksync") -> str:
    args = str(raw_args or "").strip()
    if not args:
        return execute_channel_command(message, action="current-links")

    if not args.startswith(("links", "status", "broadcast", "sync", "send")):
        return execute_channel_command(message, action="broadcast-current", text=args, include_source=False)

    subcommand, _, remainder = args.partition(" ")
    subcommand = subcommand.strip().lower()
    remainder = remainder.strip()

    if subcommand in {"links", "status"}:
        if remainder:
            return execute_channel_command(message, action="task-links", task_id=remainder)
        return execute_channel_command(message, action="current-links")

    if subcommand in {"broadcast", "sync", "send"}:
        if not remainder:
            return (
                "用法：\n"
                f"{usage_command} links [task-id]\n"
                f"{usage_command} broadcast <消息>\n"
                f"{usage_command} broadcast <task-id> <消息>"
            )
        task_id = ""
        content = remainder
        first_token, _, rest = remainder.partition(" ")
        if re.fullmatch(r"task-[A-Za-z0-9_.:-]+", first_token, re.IGNORECASE) and rest.strip():
            task_id = first_token
            content = rest.strip()
        return execute_channel_command(
            message,
            action="broadcast" if task_id else "broadcast-current",
            task_id=task_id,
            text=content,
            include_source=False,
        )

    return (
        "用法：\n"
        f"{usage_command} links [task-id]\n"
        f"{usage_command} broadcast <消息>\n"
        f"{usage_command} broadcast <task-id> <消息>"
    )
