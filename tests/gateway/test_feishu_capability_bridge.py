from types import SimpleNamespace
from unittest.mock import Mock

from gateway.platforms.feishu_capability_bridge import FeishuCapabilityBridge


def _make_event() -> SimpleNamespace:
    return SimpleNamespace(
        text="请帮我查招标",
        message_id="msg-1",
        source=SimpleNamespace(
            chat_id="group-42",
            chat_name="商务群",
            chat_type="group",
            thread_id="task-9",
            user_id="user-7",
            user_id_alt="",
        ),
    )


def test_create_capability_run_for_route_includes_scope_keys_in_input_data():
    bridge = FeishuCapabilityBridge()
    create_capability_run = Mock(return_value={"run_id": "run-1", "status": "queued"})

    bridge.create_capability_run_for_route(
        _make_event(),
        {"capability": "bid_research", "executor": "openclaw", "worker_kind": "research", "title": "查招标"},
        session_key_builder=lambda event: "sess-1",
        create_capability_run_func=create_capability_run,
    )

    kwargs = create_capability_run.call_args.kwargs
    assert kwargs["input_data"]["task_scope_key"] == "feishu:chat:group-42:thread:task-9"
    assert kwargs["input_data"]["person_memory_key"] == "feishu:user:user-7"
    assert kwargs["input_data"]["conversation_role"] == "chat_surface"


def test_scope_fields_uses_task_unit_role_for_session_style_surface_without_chat():
    scopes = FeishuCapabilityBridge._scope_fields(
        platform="feishu",
        chat_id="",
        thread_id="",
        actor_user_id="user-7",
        session_id="sess-1",
    )

    assert scopes["task_scope_key"] == ""
    assert scopes["person_memory_key"] == "feishu:user:user-7"
    assert scopes["conversation_role"] == "task_unit"


def test_create_background_job_for_route_tags_and_output_include_scope_keys():
    bridge = FeishuCapabilityBridge()
    create_job = Mock(return_value={"job_id": "job-1"})
    update_capability_run = Mock()

    bridge.create_background_job_for_route(
        _make_event(),
        {"capability": "bid_research", "executor": "openclaw", "worker_kind": "research", "mode": "guided", "title": "查招标"},
        run_record={"run_id": "run-1", "trace_id": "trace-1"},
        session_key_builder=lambda event: "sess-1",
        create_job_func=create_job,
        update_capability_run_func=update_capability_run,
    )

    create_job_kwargs = create_job.call_args.kwargs
    assert "task_scope:feishu:chat:group-42:thread:task-9" in create_job_kwargs["tags"]
    assert "person_memory:feishu:user:user-7" in create_job_kwargs["tags"]
    assert "conversation_role:chat_surface" in create_job_kwargs["tags"]

    update_kwargs = update_capability_run.call_args.kwargs
    assert update_kwargs["output"]["task_scope_key"] == "feishu:chat:group-42:thread:task-9"
    assert update_kwargs["output"]["person_memory_key"] == "feishu:user:user-7"
    assert update_kwargs["output"]["conversation_role"] == "chat_surface"
