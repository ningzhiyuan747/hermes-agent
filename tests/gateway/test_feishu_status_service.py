from types import SimpleNamespace
from unittest.mock import Mock

from gateway.platforms.feishu_status_service import FeishuStatusService, FeishuStatusServiceDeps


def _make_event() -> SimpleNamespace:
    return SimpleNamespace(
        source=SimpleNamespace(
            chat_id="oc_task_group",
            thread_id="",
        )
    )


def test_format_approval_list_text_uses_task_context():
    formatter = Mock(return_value="审批面板")
    service = FeishuStatusService(
        FeishuStatusServiceDeps(
            capability_bridge=Mock(),
            get_scope_task_context_func=Mock(return_value=("task-1", "任务一")),
            format_approval_list_text_func=formatter,
        )
    )

    text = service.format_approval_list_text([{"approval_id": "approval-1"}], _make_event(), scope_all=False)

    assert text == "审批面板"
    formatter.assert_called_once_with(
        [{"approval_id": "approval-1"}],
        task_id="task-1",
        task_title="任务一",
        scope_all=False,
    )


def test_get_background_job_snapshot_uses_capability_bridge_context():
    capability_bridge = Mock()
    capability_bridge.get_background_job_snapshot.return_value = {"job_id": "job-1"}
    service = FeishuStatusService(
        FeishuStatusServiceDeps(
            capability_bridge=capability_bridge,
            list_jobs_func=Mock(),
        )
    )

    snapshot = service.get_background_job_snapshot(
        "session-1",
        event=_make_event(),
        active_only=False,
        global_fallback=True,
    )

    assert snapshot == {"job_id": "job-1"}
    capability_bridge.get_background_job_snapshot.assert_called_once_with(
        "session-1",
        list_jobs_func=service.deps.list_jobs_func,
        active_only=False,
        global_fallback=True,
        platform="feishu",
        chat_id="oc_task_group",
        thread_id="",
    )
