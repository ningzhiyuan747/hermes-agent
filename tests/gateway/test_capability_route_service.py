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
            route_prefers_openclaw=Mock(),
            create_capability_run=Mock(),
            create_background_job=Mock(),
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event())

    assert outcome.status == "no_route"
    assert outcome.handled() is False


def test_maybe_offload_openclaw_handles_pending_approval():
    route = {"capability": "bid_research"}
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            route_prefers_openclaw=Mock(return_value=True),
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
    assert outcome.handled() is True
    assert "approval-1" in outcome.user_reply


def test_maybe_offload_openclaw_returns_job_reply():
    route = {"capability": "ops_recovery"}
    create_background_job = Mock(return_value={"job_id": "job-1"})
    service = CapabilityRouteService(
        CapabilityRouteServiceDeps(
            detect_capability_route=Mock(return_value=route),
            route_prefers_openclaw=Mock(return_value=True),
            create_capability_run=Mock(return_value={"run_id": "run-1", "status": "queued"}),
            create_background_job=create_background_job,
        )
    )

    outcome = service.maybe_offload_openclaw(_make_event("帮我恢复 gateway"))

    assert outcome.status == "offloaded_openclaw"
    assert outcome.job_record == {"job_id": "job-1"}
    assert "job-1" in outcome.user_reply
    create_background_job.assert_called_once()
