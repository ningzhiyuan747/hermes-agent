from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional

from agent.executor_registry import resolve_route_executor

_OPENCLAW_BACKGROUND_RESEARCH_CAPABILITIES = {"bid_research", "contract_retrieval"}


@dataclass(frozen=True)
class CapabilityRouteServiceDeps:
    detect_capability_route: Callable[[str], Dict[str, str] | None]
    resolve_route_executor: Callable[[Dict[str, str] | None], Dict[str, Any]]
    create_capability_run: Callable[[Any, Dict[str, str]], Dict[str, Any] | None]
    create_background_job: Callable[[Any, Dict[str, str], Dict[str, Any] | None], Dict[str, Any] | None]


@dataclass(frozen=True)
class CapabilityRouteOutcome:
    status: str
    route: Dict[str, str] | None = None
    executor_key: str = ""
    fallback_executor_key: str = ""
    run_record: Dict[str, Any] | None = None
    job_record: Dict[str, Any] | None = None
    user_reply: str = ""
    error_detail: str = ""

    def handled(self) -> bool:
        return self.status not in {"no_route", "route_not_offloaded", "unsupported_executor", "fallback_inline"}


class CapabilityRouteService:
    def __init__(self, deps: CapabilityRouteServiceDeps) -> None:
        self.deps = deps

    def _fallback_inline_outcome(
        self,
        *,
        route: Dict[str, str] | None,
        executor_key: str,
        fallback_executor_key: str,
        run_record: Dict[str, Any] | None = None,
        error_detail: str = "",
    ) -> CapabilityRouteOutcome:
        return CapabilityRouteOutcome(
            status="fallback_inline",
            route=route,
            executor_key=executor_key,
            fallback_executor_key=fallback_executor_key,
            run_record=run_record,
            error_detail=error_detail,
        )

    def _openclaw_route_allowed(self, route: Dict[str, str] | None) -> bool:
        route = route if isinstance(route, dict) else {}
        capability = str(route.get("capability") or "").strip().lower()
        worker_kind = str(route.get("worker_kind") or "").strip().lower()
        return worker_kind == "research" and capability in _OPENCLAW_BACKGROUND_RESEARCH_CAPABILITIES

    def maybe_offload_openclaw(self, event: Any) -> CapabilityRouteOutcome:
        route = self.deps.detect_capability_route(getattr(event, "text", "") or "")
        if not route:
            return CapabilityRouteOutcome(status="no_route")
        executor_spec = self.deps.resolve_route_executor(route) if self.deps.resolve_route_executor else resolve_route_executor(route)
        executor_key = str(executor_spec.get("key") or "").strip().lower()
        fallback_executor_key = str(executor_spec.get("fallback_executor_key") or "").strip().lower()
        route_behavior = str(executor_spec.get("route_behavior") or "inline").strip().lower()
        display_name = str(executor_spec.get("display_name") or executor_key or "worker").strip()
        if executor_key == "openclaw" and not self._openclaw_route_allowed(route):
            return self._fallback_inline_outcome(
                route=route,
                executor_key=executor_key,
                fallback_executor_key=fallback_executor_key or "hermes",
                error_detail=(
                    "OpenClaw is reserved for detached research work; "
                    "this route should stay with Hermes inline handling."
                ),
            )
        if route_behavior == "inline":
            return CapabilityRouteOutcome(status="route_not_offloaded", route=route, executor_key=executor_key)
        if route_behavior != "background_job":
            if fallback_executor_key == "hermes":
                return self._fallback_inline_outcome(
                    route=route,
                    executor_key=executor_key,
                    fallback_executor_key=fallback_executor_key,
                    error_detail=f"Executor '{executor_key or 'unknown'}' is not wired into capability routing yet.",
                )
            return CapabilityRouteOutcome(
                status="unsupported_executor",
                route=route,
                executor_key=executor_key,
                fallback_executor_key=fallback_executor_key,
                error_detail=f"Executor '{executor_key or 'unknown'}' is not wired into capability routing yet.",
            )

        run_record = self.deps.create_capability_run(event, route) or None
        if isinstance(run_record, dict) and run_record.get("error"):
            detail = str(run_record.get("error") or "").strip()
            return CapabilityRouteOutcome(
                status="route_error",
                route=route,
                executor_key=executor_key,
                fallback_executor_key=fallback_executor_key,
                run_record=run_record,
                error_detail=detail,
                user_reply=f"这个任务暂时没法建账执行：{detail}",
            )

        if isinstance(run_record, dict) and str(run_record.get("status") or "").strip() == "pending_approval":
            capability = str(run_record.get("capability_name") or route.get("capability") or "").strip()
            approval_id = str(run_record.get("approval_id") or "").strip()
            return CapabilityRouteOutcome(
                status="pending_approval",
                route=route,
                executor_key=executor_key,
                fallback_executor_key=fallback_executor_key,
                run_record=run_record,
                user_reply=(
                    f"已识别为 {capability} 任务，但当前需要审批后才能执行。\n审批ID：{approval_id or '-'}"
                ),
            )

        job_record = self.deps.create_background_job(event, route, run_record if isinstance(run_record, dict) else None) or None
        if not isinstance(job_record, dict):
            if fallback_executor_key == "hermes":
                return self._fallback_inline_outcome(
                    route=route,
                    executor_key=executor_key,
                    fallback_executor_key=fallback_executor_key,
                    run_record=run_record if isinstance(run_record, dict) else None,
                    error_detail=f"Background job creation failed for executor '{executor_key or 'unknown'}'.",
                )
            return CapabilityRouteOutcome(
                status="job_create_failed",
                route=route,
                executor_key=executor_key,
                fallback_executor_key=fallback_executor_key,
                run_record=run_record if isinstance(run_record, dict) else None,
                user_reply=f"后台 {display_name} 任务创建失败，暂时没有转交成功。",
            )

        capability = str(route.get("capability") or "").strip()
        return CapabilityRouteOutcome(
            status="offloaded_executor",
            route=route,
            executor_key=executor_key,
            fallback_executor_key=fallback_executor_key,
            run_record=run_record if isinstance(run_record, dict) else None,
            job_record=job_record,
            user_reply=(
                f"已转给 {display_name} 后台 research executor 处理。"
                f"\n能力：{capability}"
                f"\n任务ID：{str(job_record.get('job_id') or '').strip()}"
                "\n可随时发“状态”或“停止”查看/终止。"
            ),
        )
