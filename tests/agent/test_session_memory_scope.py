from agent import session_memory_scope as scope


def test_build_session_memory_scope_prompt_for_dm_uses_user_memory(monkeypatch):
    values = {
        "HERMES_SESSION_PLATFORM": "feishu",
        "HERMES_SESSION_CHAT_TYPE": "dm",
        "HERMES_SESSION_USER_ID": "ou_user_1",
        "HERMES_SESSION_CHAT_ID": "oc_dm_1",
        "HERMES_SESSION_THREAD_ID": "",
    }

    monkeypatch.setattr(scope, "_session_value", lambda name: values.get(name, ""))
    monkeypatch.setattr(
        scope,
        "get_user_distilled_profile",
        lambda **_kwargs: {
            "summary": "偏好先给结论、输出尽量短、默认中文",
            "memory": {
                "profile": {
                    "core_principles": "先给结论；默认中文沟通",
                    "working_style": "结果导向",
                    "preferred_output": "先结论后细节",
                    "domain_focus": "商务跟进",
                    "decision_heuristics": "先明确目标，再给最短执行路径",
                    "approval_sensitivity": "中",
                    "anti_patterns": "避免冗长空话",
                    "stable_instructions": "默认中文；输出简洁",
                },
            },
        },
    )
    def _fake_user_memory(**kwargs):
        scope_name = kwargs.get("scope")
        if scope_name == "profile":
            return {"summary": "用户偏好中文、喜欢简洁回答"}
        if scope_name == "notes":
            return {"summary": "最近确认：商务沟通优先直接给结论"}
        return None

    monkeypatch.setattr(scope, "get_user_memory", _fake_user_memory)

    text = scope.build_session_memory_scope_prompt()

    assert "Current external identity in this conversation" in text
    assert "Stable repo/workspace/tool facts belong to system memory" in text
    assert "This is a private user conversation" in text
    assert "Distilled working profile summary: 偏好先给结论、输出尽量短、默认中文" in text
    assert "Distilled core principles: 先给结论；默认中文沟通" in text
    assert "Distilled working style: 结果导向" in text
    assert "Distilled preferred output: 先结论后细节" in text
    assert "Distilled decision heuristics: 先明确目标，再给最短执行路径" in text
    assert "Distilled anti-patterns: 避免冗长空话" in text
    assert "Private user profile summary: 用户偏好中文、喜欢简洁回答" in text
    assert "Private user notes summary: 最近确认：商务沟通优先直接给结论" in text
    assert "Do not pull task-group memory" in text


def test_build_session_memory_scope_prompt_for_task_group_uses_task_memory(monkeypatch):
    values = {
        "HERMES_SESSION_PLATFORM": "dingtalk",
        "HERMES_SESSION_CHAT_TYPE": "group",
        "HERMES_SESSION_USER_ID": "staff_1",
        "HERMES_SESSION_CHAT_ID": "cid_group_1",
        "HERMES_SESSION_THREAD_ID": "",
    }

    monkeypatch.setattr(scope, "_session_value", lambda name: values.get(name, ""))
    monkeypatch.setattr(
        scope,
        "get_channel_task",
        lambda **_kwargs: {"task": {"task_id": "task-123", "title": "中标合同取证"}},
    )
    monkeypatch.setattr(scope, "get_task_memory", lambda **_kwargs: {"summary": "已确认采购单位，下一步追合同附件"})

    text = scope.build_session_memory_scope_prompt()

    assert "This conversation is bound to task task-123 (中标合同取证)." in text
    assert "Use task memory as the shared durable context for this conversation." in text
    assert "There is no separate durable channel memory layer here" in text
    assert "Task memory summary: 已确认采购单位，下一步追合同附件" in text
    assert "Do not expose or rely on any participant's private DM memory here" in text


def test_build_session_memory_scope_prompt_for_unbound_group_blocks_private_memory(monkeypatch):
    values = {
        "HERMES_SESSION_PLATFORM": "dingtalk",
        "HERMES_SESSION_CHAT_TYPE": "group",
        "HERMES_SESSION_USER_ID": "staff_1",
        "HERMES_SESSION_CHAT_ID": "cid_group_2",
        "HERMES_SESSION_THREAD_ID": "",
    }

    monkeypatch.setattr(scope, "_session_value", lambda name: values.get(name, ""))
    monkeypatch.setattr(scope, "get_channel_task", lambda **_kwargs: {"task": None})

    text = scope.build_session_memory_scope_prompt()

    assert "This is a shared chat without a bound task." in text
    assert "There is no durable shared channel memory here until a task is bound." in text
    assert "Do not use any participant's private memory as shared context here." in text
