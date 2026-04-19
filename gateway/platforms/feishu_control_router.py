from __future__ import annotations

import re
from dataclasses import dataclass
from typing import Any, Awaitable, Callable, Dict, Optional

from gateway.platforms.base import MessageEvent


@dataclass(frozen=True)
class FeishuControlRouterDeps:
    send_plain: Callable[[MessageEvent, str], Awaitable[Any]]
    is_owner_or_admin: Callable[[MessageEvent], bool]
    get_dingtalk_bridge_status: Callable[[], Awaitable[str]]
    relay_text_to_dingtalk: Callable[[str], Awaitable[Dict[str, Any]]]
    cancel_background_job_for_session: Callable[..., Dict[str, Any]]
    build_adapter_context: Callable[..., Any]
    normalize_text: Callable[[str], str]
    extract_prefixed_text: Callable[[str, tuple[str, ...]], str]
    is_dingtalk_bridge_status_text: Callable[[str], bool]
    extract_dingtalk_relay_text: Callable[[str], str]
    looks_like_dingtalk_test_text: Callable[[str], bool]
    list_approval_rows_for_event: Callable[..., tuple[list[Dict[str, Any]], str, str]]
    format_approval_list_text: Callable[..., str]
    get_event_task_panel_snapshot: Callable[..., Optional[Dict[str, Any]]]
    format_task_panel_snapshot: Callable[[Dict[str, Any]], str]
    get_background_job_snapshot: Callable[..., Optional[Dict[str, Any]]]
    get_capability_run_snapshot: Callable[..., Optional[Dict[str, Any]]]
    format_activity_snapshot: Callable[..., str]
    dispatch_business_text_command_func: Callable[..., Optional[str]] | None = None
    create_task_func: Callable[..., Dict[str, Any]] | None = None
    bind_channel_task_func: Callable[..., Any] | None = None
    get_channel_task_func: Callable[..., Dict[str, Any] | None] | None = None
    get_task_func: Callable[[str], Dict[str, Any] | None] | None = None
    unbind_channel_task_func: Callable[..., Any] | None = None
    decide_approval_func: Callable[..., Dict[str, Any] | None] | None = None
    parse_task_sync_control_text_func: Callable[..., Dict[str, Any] | None] | None = None
    build_channel_sync_payload_func: Callable[..., Dict[str, Any]] | None = None
    run_channel_command_func: Callable[[Dict[str, Any]], str] | None = None
    task_create_prefixes: tuple[str, ...] = ()
    current_task_commands_norm: frozenset[str] = frozenset()
    task_bind_prefixes: tuple[str, ...] = ()
    task_unbind_commands_norm: frozenset[str] = frozenset()
    task_link_text_commands: tuple[str, ...] = ()
    task_broadcast_prefixes: tuple[str, ...] = ()
    status_text_commands_norm: frozenset[str] = frozenset()
    status_active_only_commands_norm: frozenset[str] = frozenset()
    stop_text_commands_norm: frozenset[str] = frozenset()
    approval_list_commands_norm: frozenset[str] = frozenset()


class FeishuControlRouter:
    def __init__(self, deps: FeishuControlRouterDeps) -> None:
        self.deps = deps

    def _build_context(self, event: MessageEvent, session_key: str) -> Any:
        return self.deps.build_adapter_context(
            event,
            platform_name="feishu",
            session_key=session_key,
        )

    async def handle_background_control_text(
        self,
        event: MessageEvent,
        *,
        normalized: str,
        session_key: str,
    ) -> bool:
        if not normalized:
            return False
        if await self.handle_dingtalk_bridge_control_text(event):
            return True
        if await self.handle_task_control_text(event, normalized=normalized, session_key=session_key):
            return True
        if await self.handle_status_control_text(event, normalized=normalized, session_key=session_key):
            return True
        if normalized in self.deps.approval_list_commands_norm:
            return await self.send_approval_list_for_event(event, normalized)
        if normalized.startswith("批准") or normalized.startswith("同意approval-"):
            return await self.decide_approval_from_event_text(event, decision="approved")
        if normalized.startswith("拒绝") or normalized.startswith("驳回approval-"):
            return await self.decide_approval_from_event_text(event, decision="denied")
        return False

    async def send_approval_list_for_event(self, event: MessageEvent, normalized: str) -> bool:
        if not self.deps.is_owner_or_admin(event):
            await self.deps.send_plain(event, "审批列表只允许 Owner/Admin 查看。")
            return True
        rows_loader = self.deps.list_approval_rows_for_event
        scope_all = "全部" in normalized or normalized == "approvals"
        status = "" if scope_all else "pending"
        rows, task_id, task_title = rows_loader(
            event,
            scope_all=scope_all,
            status=status,
            limit=10,
        )
        if not rows and not task_id and not task_title:
            await self.deps.send_plain(event, "业务审批模块当前不可用。")
            return True
        await self.deps.send_plain(
            event,
            self.deps.format_approval_list_text(rows, event, scope_all=scope_all),
        )
        return True

    async def decide_approval_from_event_text(self, event: MessageEvent, *, decision: str) -> bool:
        action_label = "批准" if decision == "approved" else "拒绝"
        if not self.deps.is_owner_or_admin(event):
            await self.deps.send_plain(event, f"{action_label}只允许 Owner/Admin 执行。")
            return True
        if self.deps.decide_approval_func is None:
            await self.deps.send_plain(event, "业务审批模块当前不可用。")
            return True
        approval_match = re.search(r"(approval-[A-Za-z0-9-]+)", event.text or "", re.IGNORECASE)
        if not approval_match:
            await self.deps.send_plain(event, f"请带上 approval id，例如：{action_label} approval-xxxx")
            return True
        record = self.deps.decide_approval_func(
            approval_match.group(1),
            status=decision,
            approved_by=str(event.source.user_id or ""),
        )
        await self.deps.send_plain(event, f"已{action_label}。" if record else "没找到对应审批。")
        return True

    async def handle_task_control_text(
        self,
        event: MessageEvent,
        *,
        normalized: str,
        session_key: str,
    ) -> bool:
        context = self._build_context(event, session_key)
        can_manage_bindings = self.deps.is_owner_or_admin(event)

        if self.deps.dispatch_business_text_command_func is not None:
            shared_response = self.deps.dispatch_business_text_command_func(
                context.incoming_message,
                can_manage_bindings=can_manage_bindings,
            )
            if shared_response:
                await self.deps.send_plain(event, shared_response)
                return True

        task_title = self.deps.extract_prefixed_text(event.text, self.deps.task_create_prefixes)
        if task_title:
            if self.deps.create_task_func is None:
                await self.deps.send_plain(event, "任务模块当前不可用。")
                return True
            try:
                task = self.deps.create_task_func(
                    title=task_title,
                    goal=task_title,
                    owner_user_id=context.user_id or str(getattr(event.source, "user_id_alt", "") or "").strip(),
                    source_platform="feishu",
                    source_chat_id=context.chat_id,
                    source_thread_id=context.thread_id,
                    source_session_id=session_key,
                )
                bound = False
                if not context.is_private_chat() and self.deps.bind_channel_task_func is not None:
                    self.deps.bind_channel_task_func(
                        platform="feishu",
                        chat_id=context.chat_id,
                        thread_id=context.thread_id,
                        task_id=str(task.get("task_id") or "").strip(),
                    )
                    bound = True
                await self.deps.send_plain(
                    event,
                    "任务已创建。"
                    + f"\nTask ID: {str(task.get('task_id') or '').strip()}"
                    + (f"\n标题: {str(task.get('title') or '').strip()}" if str(task.get("title") or "").strip() else "")
                    + ("\n当前群已绑定到该任务。" if bound else "\n当前是私聊，未绑定群；后续可在任务群里发：绑定任务 <task-id>"),
                )
            except Exception as exc:
                await self.deps.send_plain(event, f"建任务失败：{exc}")
            return True

        if normalized in self.deps.current_task_commands_norm:
            if self.deps.get_channel_task_func is None:
                await self.deps.send_plain(event, "任务模块当前不可用。")
                return True
            record = self.deps.get_channel_task_func(
                platform="feishu",
                chat_id=context.chat_id,
                thread_id=context.thread_id,
            )
            if context.is_private_chat():
                await self.deps.send_plain(
                    event,
                    "当前是私聊，默认走个人上下文。\n如需协作，请先在任务群里“建任务 <标题>”或“绑定任务 <task-id>”。",
                )
                return True
            task = (record or {}).get("task") if isinstance(record, dict) else None
            if not isinstance(task, dict):
                await self.deps.send_plain(event, "当前群还没有绑定任务。\n可用：建任务 <标题> 或 绑定任务 <task-id>")
                return True
            snapshot = self.deps.get_event_task_panel_snapshot(event, active_only=False)
            await self.deps.send_plain(event, self.deps.format_task_panel_snapshot(snapshot or {"task": task}))
            return True

        bind_task_id = self.deps.extract_prefixed_text(event.text, self.deps.task_bind_prefixes)
        if bind_task_id:
            if not can_manage_bindings:
                await self.deps.send_plain(event, "绑定任务只允许 Owner/Admin 使用。")
                return True
            if context.is_private_chat():
                await self.deps.send_plain(event, "私聊不需要绑定任务群；请在目标任务群里执行这个命令。")
                return True
            if self.deps.bind_channel_task_func is None or self.deps.get_task_func is None:
                await self.deps.send_plain(event, "任务模块当前不可用。")
                return True
            task = self.deps.get_task_func(bind_task_id)
            if not task:
                await self.deps.send_plain(event, f"没找到任务：{bind_task_id}")
                return True
            try:
                self.deps.bind_channel_task_func(
                    platform="feishu",
                    chat_id=context.chat_id,
                    thread_id=context.thread_id,
                    task_id=bind_task_id,
                )
                await self.deps.send_plain(
                    event,
                    "当前群已绑定任务。"
                    + f"\nTask ID: {bind_task_id}"
                    + (f"\n标题: {str(task.get('title') or '').strip()}" if str(task.get("title") or "").strip() else ""),
                )
            except Exception as exc:
                await self.deps.send_plain(event, f"绑定任务失败：{exc}")
            return True

        if normalized in self.deps.task_unbind_commands_norm:
            if not can_manage_bindings:
                await self.deps.send_plain(event, "解绑任务只允许 Owner/Admin 使用。")
                return True
            if context.is_private_chat():
                await self.deps.send_plain(event, "私聊没有任务群绑定，不需要解绑。")
                return True
            if self.deps.unbind_channel_task_func is None:
                await self.deps.send_plain(event, "任务模块当前不可用。")
                return True
            try:
                self.deps.unbind_channel_task_func(
                    platform="feishu",
                    chat_id=context.chat_id,
                    thread_id=context.thread_id,
                )
                await self.deps.send_plain(event, "当前群的任务绑定已解除。")
            except Exception as exc:
                await self.deps.send_plain(event, f"解绑任务失败：{exc}")
            return True

        sync_command = (
            self.deps.parse_task_sync_control_text_func(
                event.text,
                normalize=self.deps.normalize_text,
                extract_prefixed_text=self.deps.extract_prefixed_text,
                link_commands=self.deps.task_link_text_commands,
                broadcast_prefixes=self.deps.task_broadcast_prefixes,
            )
            if self.deps.parse_task_sync_control_text_func is not None
            else None
        )
        if sync_command is None:
            return False

        action = str(sync_command.get("action") or "").strip()
        if action == "links":
            if self.deps.run_channel_command_func is None or self.deps.build_channel_sync_payload_func is None:
                await self.deps.send_plain(event, "任务同步模块当前不可用。")
                return True
            payload = self.deps.build_channel_sync_payload_func(
                action="current-links",
                platform="feishu",
                chat_id=context.chat_id,
                thread_id=context.thread_id,
                chat_type=context.chat_type,
            )
            await self.deps.send_plain(event, self.deps.run_channel_command_func(payload))
            return True

        if action == "broadcast":
            if self.deps.run_channel_command_func is None or self.deps.build_channel_sync_payload_func is None:
                await self.deps.send_plain(event, "任务同步模块当前不可用。")
                return True
            payload = self.deps.build_channel_sync_payload_func(
                action="broadcast-current",
                platform="feishu",
                chat_id=context.chat_id,
                thread_id=context.thread_id,
                chat_type=context.chat_type,
                message=str(sync_command.get("message") or "").strip(),
                include_source=False,
            )
            await self.deps.send_plain(event, self.deps.run_channel_command_func(payload))
            return True
        return False

    async def handle_status_control_text(
        self,
        event: MessageEvent,
        *,
        normalized: str,
        session_key: str,
    ) -> bool:
        if normalized in self.deps.status_text_commands_norm:
            active_only = normalized in self.deps.status_active_only_commands_norm
            if str(event.source.chat_type or "").strip() != "dm":
                task_snapshot = self.deps.get_event_task_panel_snapshot(event, active_only=active_only)
                if task_snapshot:
                    await self.deps.send_plain(event, self.deps.format_task_panel_snapshot(task_snapshot))
                    return True
            owner_or_admin = self.deps.is_owner_or_admin(event)
            job = self.deps.get_background_job_snapshot(
                session_key,
                event=event,
                active_only=active_only,
                global_fallback=owner_or_admin,
            )
            run = None if job else self.deps.get_capability_run_snapshot(
                session_key,
                event=event,
                active_only=active_only,
                global_fallback=owner_or_admin,
            )
            activity_text = self.deps.format_activity_snapshot(job=job, run=run)
            await self.deps.send_plain(event, activity_text or "当前没有正在执行的后台研究任务。")
            return True

        if normalized in self.deps.stop_text_commands_norm:
            if not self.deps.is_owner_or_admin(event):
                await self.deps.send_plain(
                    event,
                    "这个控制命令只允许 Owner/Admin 使用。\n如果你就是 Owner，但仍被拦住，我可以继续修 owner 判定链路。",
                )
                return True
            outcome = self.deps.cancel_background_job_for_session(
                session_key,
                event=event,
                global_fallback=True,
            )
            cancelled = int(outcome.get("cancelled") or 0)
            await self.deps.send_plain(
                event,
                f"已停止后台研究任务，共 {cancelled} 个。" if cancelled else "当前没有正在执行的后台研究任务。",
            )
            return True
        return False

    async def handle_dingtalk_bridge_control_text(self, event: MessageEvent) -> bool:
        if self.deps.is_dingtalk_bridge_status_text(event.text):
            if not self.deps.is_owner_or_admin(event):
                await self.deps.send_plain(event, "钉钉桥状态只允许 Owner/Admin 查询。")
                return True
            await self.deps.send_plain(event, await self.deps.get_dingtalk_bridge_status())
            return True

        relay_text = self.deps.extract_dingtalk_relay_text(event.text)
        if not relay_text:
            return False
        if not self.deps.is_owner_or_admin(event):
            await self.deps.send_plain(event, "给钉钉子智能体派任务只允许 Owner/Admin 使用。")
            return True
        if self.deps.looks_like_dingtalk_test_text(relay_text):
            status_text = await self.deps.get_dingtalk_bridge_status()
            await self.deps.send_plain(
                event,
                "已拦截这次桥测试任务，改为只返回桥状态，避免把派任务误判成当前钉钉会话回复。\n\n"
                + status_text,
            )
            return True
        outcome = await self.deps.relay_text_to_dingtalk(relay_text)
        if outcome.get("ok"):
            target_name = str(outcome.get("target_name") or outcome.get("chat_id") or "").strip()
            await self.deps.send_plain(
                event,
                "已把任务派给钉钉子智能体。"
                + (f"\n目标：{target_name}" if target_name else "")
                + "\n说明：底层仍走钉钉主动发送链路，但这里的语义是“派任务”，不是“测回复壳”。",
            )
        else:
            detail = str(outcome.get("error") or outcome.get("detail") or outcome.get("raw") or "未知错误").strip()
            await self.deps.send_plain(event, f"派给钉钉子智能体失败：{detail}")
        return True
