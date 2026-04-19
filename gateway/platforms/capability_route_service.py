from __future__ import annotations

from dataclasses import dataclass
from typing import Any, Callable, Dict, Optional


@dataclass(frozen=True)
class CapabilityRouteServiceDeps:
    detect_capability_route: Callable[[str], Dict[str, str] | None]
    route_prefers_openclaw: Callable[[Dict[str, str] | None], bool]
    create_capability_run: Callable[[Any, Dict[str, str]], Dict[str, Any] | None]
    create_background_job: Callable[[Any, Dict[str, str], Dict[str, Any] | None], Dict[str, Any] | None]


@dataclass(frozen=True)
class CapabilityRouteOutcome:
    status: str
    route: Dict[str, str] | None = None
    run_record: Dict[str, Any] | None = None
    job_record: Dict[str, Any] | None = None
    user_reply: str = ""
    error_detail: str = ""

    def handled(self) -> bool:
        return self.status not in {"no_route", "route_not_offloaded"}


class CapabilityRouteService:
    def __init__(self, deps: CapabilityRouteServiceDeps) -> None:
        self.deps = deps

    def maybe_offload_openclaw(self, event: Any) -> CapabilityRouteOutcome:
        route = self.deps.detect_capability_route(getattr(event, "text", "") or "")
        if not route:
            return CapabilityRouteOutcome(status="no_route")
        if not self.deps.route_prefers_openclaw(route):
            return CapabilityRouteOutcome(status="route_not_offloaded", route=route)

        run_record = self.deps.create_capability_run(event, route) or None
        if isinstance(run_record, dict) and run_record.get("error"):
            detail = str(run_record.get("error") or "").strip()
            return CapabilityRouteOutcome(
                status="route_error",
                route=route,
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
                run_record=run_record,
                user_reply=(
                    f"已识别为 {capability} 任务，但当前需要审批后才能执行。\n审批ID：{approval_id or '-'}"
                ),
            )

        job_record = self.deps.create_background_job(event, route, run_record if isinstance(run_record, dict) else None) or None
        if not isinstance(job_record, dict):
            return CapabilityRouteOutcome(
                status="job_create_failed",
                route=route,
                run_record=run_record if isinstance(run_record, dict) else None,
                user_reply="后台研究任务创建失败，暂时没有转给 OpenClaw。",
            )

        capability = str(route.get("capability") or "").strip()
        return CapabilityRouteOutcome(
            status="offloaded_openclaw",
            route=route,
            run_record=run_record if isinstance(run_record, dict) else None,
            job_record=job_record,
            user_reply=(
                "已转给 OpenClaw research worker 后台处理。"
                f"\n能力：{capability}"
                f"\n任务ID：{str(job_record.get('job_id') or '').strip()}"
                "\n可随时发“状态”或“停止”查看/终止。"
            ),
        )
