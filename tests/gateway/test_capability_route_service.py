from types import SimpleNamespace
from unittest.mock import Mock

from gateway.platforms.capability_route_service import (
    CapabilityRouteService,
    CapabilityRouteServiceDeps,
)


def _make_event(text: str = "请帮我查招标") -> SimpleNamespace:
    return SimpleNamespace(text=text)


def test_maybe_offload_openclaw_returns_no_route():
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=None),
            resolve_route_executor=Mock(),
            create_capability_run=Mock(),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event())

    assert outcome.status == "no_route"
    assert outcome.handled() is False


def test_maybe_offload_openclaw_handles_pending_approval():
    route = {"capability": "bid_research", "worker_kind": "research"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "openclaw", "display_name": "OpenClaw", "route_behavior": "background_job", "fallback_executor_key": "hermes"}),
            create_capability_run=Mock(
                return_value={
                    "status": "pending_approval",
                    "approval_id": "approval-1",
                    "capability_name": "bid_research",
                }
            ),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event())

    assert outcome.status == "pending_approval"
    assert outcome.executor_key == "openclaw"
    assert outcome.handled() is True
    assert "approval-1" in outcome.user_reply


def test_maybe_offload_openclaw_returns_job_reply():
    route = {"capability": "bid_research", "worker_kind": "research"}
    create_background_job = Mock(return_value={"job_id": "job-1"})
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "openclaw", "display_name": "OpenClaw", "route_behavior": "background_job", "fallback_executor_key": "hermes"}),
            create_capability_run=Mock(return_value={"run_id": "run-1", "status": "queued"}),
            create_background_job=create_background_job,
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("帮我查招标"))

    assert outcome.status == "offloaded_executor"
    assert outcome.executor_key == "openclaw"
    assert outcome.job_record == {"job_id": "job-1"}
    assert "job-1" in outcome.user_reply
    assert "research executor" in outcome.user_reply
    create_background_job.assert_called_once()


def test_maybe_offload_openclaw_returns_route_not_offloaded_for_inline_executor():
    route = {"capability": "meeting_minutes", "executor": "hermes"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "hermes", "display_name": "Hermes", "route_behavior": "inline", "fallback_executor_key": ""}),
            create_capability_run=Mock(),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("帮我整理会议纪要"))

    assert outcome.status == "route_not_offloaded"
    assert outcome.executor_key == "hermes"
    assert outcome.handled() is False


def test_maybe_offload_openclaw_falls_back_to_hermes_for_unsupported_executor():
    route = {"capability": "ops_recovery", "executor": "codex"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "codex", "display_name": "Codex", "route_behavior": "external", "fallback_executor_key": "hermes"}),
            create_capability_run=Mock(),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("交给 codex 修这个问题"))

    assert outcome.status == "fallback_inline"
    assert outcome.executor_key == "codex"
    assert outcome.fallback_executor_key == "hermes"
    assert outcome.handled() is False
    assert "not wired" in outcome.error_detail


def test_maybe_offload_openclaw_falls_back_inline_when_job_creation_fails():
    route = {"capability": "contract_retrieval", "executor": "openclaw", "worker_kind": "research"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "openclaw", "display_name": "OpenClaw", "route_behavior": "background_job", "fallback_executor_key": "hermes"}),
            create_capability_run=Mock(return_value={"run_id": "run-1", "status": "queued"}),
            create_background_job=Mock(return_value=None),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("帮我找合同"))

    assert outcome.status == "fallback_inline"
    assert outcome.executor_key == "openclaw"
    assert outcome.fallback_executor_key == "hermes"
    assert outcome.handled() is False
    assert "Background job creation failed" in outcome.error_detail


def test_maybe_offload_openclaw_rejects_non_research_route():
    route = {"capability": "ops_recovery", "executor": "openclaw", "worker_kind": "operations"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            resolve_route_executor=Mock(return_value={"key": "openclaw", "display_name": "OpenClaw", "route_behavior": "background_job", "fallback_executor_key": "hermes"}),
            create_capability_run=Mock(),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("帮我恢复 gateway"))

    assert outcome.status == "fallback_inline"
    assert outcome.executor_key == "openclaw"
    assert outcome.fallback_executor_key == "hermes"
    assert outcome.handled() is False
    assert "detached research work" in outcome.error_detail
