from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from gateway.platforms.base import MessageEvent


@dataclass(frozen=True)
class FeishuStatusServiceDeps:
    capability_bridge: Any
    get_scope_task_context_func: Callable[..., tuple[str, str]] | None = None
    list_scoped_approvals_func: Callable[..., tuple[list[Dict[str, Any]], str, str]] | None = None
    format_approval_list_text_func: Callable[..., str] | None = None
    get_task_panel_snapshot_func: Callable[..., Dict[str, Any]] | None = None
    format_task_panel_snapshot_func: Callable[..., str] | None = None
    list_jobs_func: Callable[..., list[Dict[str, Any]]] | None = None
    build_activity_snapshot_text_func: Callable[..., str] | None = None
    get_operational_task_board_text_func: Callable[[], str] | None = None


class FeishuStatusService:
    def __init__(self, deps: FeishuStatusServiceDeps) -> None:
        self.deps = deps

    def get_event_task_context(self, event: MessageEvent) -> tuple[str, str]:
        func = self.deps.get_scope_task_context_func
        if func is None:
            return "", ""
        return func(
            platform="feishu",
            chat_id=str(event.source.chat_id or "").strip(),
            thread_id=str(event.source.thread_id or "").strip(),
        )

    def list_approval_rows_for_event(
        self,
        event: MessageEvent,
        *,
        scope_all: bool,
        status: str,
        limit: int = 10,
    ) -> tuple[list[Dict[str, Any]], str, str]:
        func = self.deps.list_scoped_approvals_func
        if func is None:
            return [], "", ""
        return func(
            platform="feishu",
            chat_id=str(event.source.chat_id or "").strip(),
            thread_id=str(event.source.thread_id or "").strip(),
            scope_all=scope_all,
            status=status,
            limit=limit,
        )

    def format_approval_list_text(
        self,
        rows: list[Dict[str, Any]],
        event: MessageEvent,
        *,
        scope_all: bool,
    ) -> str:
        func = self.deps.format_approval_list_text_func
        if func is None:
            return "业务审批列表暂不可用。"
        task_id, task_title = self.get_event_task_context(event)
        return func(rows, task_id=task_id, task_title=task_title, scope_all=scope_all)

    def get_event_task_panel_snapshot(
        self,
        event: MessageEvent,
        *,
        active_only: bool = False,
    ) -> Optional[Dict[str, Any]]:
        task_id, _task_title = self.get_event_task_context(event)
        func = self.deps.get_task_panel_snapshot_func
        if not task_id or func is None:
            return None
        try:
            return func(task_id, active_only=active_only)
        except Exception:
            return None

    def format_task_panel_snapshot(self, snapshot: Dict[str, Any]) -> str:
        func = self.deps.format_task_panel_snapshot_func
        if func is None:
            return "当前任务面板暂不可用。"
        return func(snapshot, fullwidth_colon=True)

    def get_background_job_snapshot(
        self,
        session_key: str,
        *,
        event: Optional[MessageEvent] = None,
        active_only: bool = True,
        global_fallback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        source = event.source if event is not None else None
        return self.deps.capability_bridge.get_background_job_snapshot(
            session_key,
            list_jobs_func=self.deps.list_jobs_func,
            active_only=active_only,
            global_fallback=global_fallback,
            platform="feishu",
            chat_id=str(getattr(source, "chat_id", "") or ""),
            thread_id=str(getattr(source, "thread_id", "") or ""),
        )

    def get_capability_run_snapshot(
        self,
        session_key: str,
        *,
        event: Optional[MessageEvent] = None,
        active_only: bool = True,
        global_fallback: bool = False,
    ) -> Optional[Dict[str, Any]]:
        source = event.source if event is not None else None
        return self.deps.capability_bridge.get_capability_run_snapshot(
            session_key,
            active_only=active_only,
            global_fallback=global_fallback,
            platform="feishu",
            chat_id=str(getattr(source, "chat_id", "") or ""),
            thread_id=str(getattr(source, "thread_id", "") or ""),
        )

    def format_activity_snapshot(
        self,
        *,
        job: Optional[Dict[str, Any]] = None,
        run: Optional[Dict[str, Any]] = None,
    ) -> str:
        func = self.deps.build_activity_snapshot_text_func
        if func is None:
            if job:
                return self.deps.capability_bridge.format_background_job_snapshot(job)
            if run:
                return self.deps.capability_bridge.format_capability_run_snapshot(run)
            task_board_func = self.deps.get_operational_task_board_text_func
            if task_board_func is not None:
                try:
                    return task_board_func()
                except Exception:
                    return ""
            return ""
        text = func(job, run, fullwidth_colon=True)
        if text:
            return text
        task_board_func = self.deps.get_operational_task_board_text_func
        if task_board_func is not None:
            try:
                return task_board_func()
            except Exception:
                return ""
        return ""
