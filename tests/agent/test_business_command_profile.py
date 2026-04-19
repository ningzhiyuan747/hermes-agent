from agent.business_command_ops import execute_distilled_profile_command
from agent.incoming_message import IncomingMessage


def _message(text: str, *, user_id: str = "ou_user_1") -> IncomingMessage:
    return IncomingMessage(
        platform="dingtalk",
        chat_id="cid_1",
        chat_type="dm",
        user_id=user_id,
        user_name="Tester",
        text=text,
        session_key="session-1",
    )


def test_execute_distilled_profile_command_formats_profile(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user_distilled_profile",
        lambda **_kwargs: {
            "summary": "工作风格：结果导向；输出偏好：先结论后细节。",
            "memory": {
                "profile": {
                    "working_style": "结果导向",
                    "preferred_output": "先结论后细节",
                    "stable_instructions": "默认中文；输出简洁",
                },
                "locked_fields": ["preferred_output"],
                "sources": {"source_window_days": 30},
            },
        },
    )

    text = execute_distilled_profile_command(_message("我的画像"), {"action": "show"}, can_manage_profiles=False)

    assert "画像：张三" in text
    assert "工作风格：结果导向" in text
    assert "已锁字段：输出偏好" in text
    assert "证据窗口：最近 30 天" in text


def test_execute_distilled_profile_command_blocks_other_user_edit_without_permission():
    text = execute_distilled_profile_command(
        _message("设画像 ou_other 输出偏好=先结论"),
        {"action": "set", "user_id": "ou_other", "field": "输出偏好", "value": "先结论"},
        can_manage_profiles=False,
    )

    assert text == "修改他人画像只允许 Owner/Admin 使用。"


def test_execute_distilled_profile_command_formats_evidence(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user_distilled_profile",
        lambda **_kwargs: {
            "summary": "工作风格：结果导向。",
            "memory": {
                "profile": {
                    "preferred_output": "先结论后细节",
                },
                "evidence": {
                    "preferred_output": [
                        {"kind": "profile_summary", "text": "偏好简洁、先说结论"},
                        {"kind": "notes_summary", "text": "最近再次确认：先给结论"},
                    ]
                },
                "sources": {"source_window_days": 30},
            },
        },
    )

    text = execute_distilled_profile_command(
        _message("画像证据 输出偏好"),
        {"action": "evidence", "field": "输出偏好"},
        can_manage_profiles=False,
    )

    assert "画像证据：张三" in text
    assert "字段：输出偏好" in text
    assert "当前值：先结论后细节" in text
    assert "用户资料：偏好简洁、先说结论" in text


def test_execute_distilled_profile_command_blocks_other_user_evidence_without_permission():
    text = execute_distilled_profile_command(
        _message("画像证据 ou_other 输出偏好"),
        {"action": "evidence", "user_id": "ou_other", "field": "输出偏好"},
        can_manage_profiles=False,
    )

    assert text == "查看他人画像证据只允许 Owner/Admin 使用。"


def test_execute_distilled_profile_command_formats_draft(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user_distilled_profile_draft",
        lambda **_kwargs: {
            "summary": "工作风格：结果导向。",
            "profile": {"working_style": "结果导向"},
            "sources": {"source_window_days": 7},
            "updated_by": "distiller",
        },
    )

    text = execute_distilled_profile_command(
        _message("画像草稿"),
        {"action": "draft"},
        can_manage_profiles=False,
    )

    assert "画像草稿：张三" in text
    assert "工作风格：结果导向" in text
    assert "可用命令：发布画像、丢弃画像草稿、画像草稿证据 [字段]" in text


def test_execute_distilled_profile_command_publishes_draft(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.publish_user_distilled_profile_draft",
        lambda **_kwargs: {"summary": "工作风格：结果导向。", "memory": {"profile": {"working_style": "结果导向"}}},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )

    text = execute_distilled_profile_command(
        _message("发布画像"),
        {"action": "publish"},
        can_manage_profiles=False,
    )

    assert text.startswith("已发布画像草稿。")


def test_execute_distilled_profile_command_formats_diff(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user_distilled_profile",
        lambda **_kwargs: {
            "summary": "工作风格：结果导向。",
            "memory": {
                "profile": {"preferred_output": "先结论后细节"},
                "locked_fields": ["preferred_output"],
            },
        },
    )
    monkeypatch.setattr(
        "agent.business_command_ops.get_user_distilled_profile_draft",
        lambda **_kwargs: {
            "summary": "工作风格：结果导向。",
            "profile": {"preferred_output": "先一句话结论，再给清单"},
        },
    )

    text = execute_distilled_profile_command(
        _message("画像差异 输出偏好"),
        {"action": "diff", "field": "输出偏好"},
        can_manage_profiles=False,
    )

    assert "画像差异：张三" in text
    assert "输出偏好 [已锁]：变更" in text
    assert "正式：先结论后细节" in text
    assert "草稿：先一句话结论，再给清单" in text


def test_execute_distilled_profile_command_formats_history(monkeypatch):
    monkeypatch.setattr(
        "agent.business_command_ops.get_user",
        lambda **_kwargs: {"display_name": "张三"},
    )
    monkeypatch.setattr(
        "agent.business_command_ops.list_user_distilled_profile_history",
        lambda **_kwargs: [
            {
                "timestamp_unix": 1713330000,
                "action": "published",
                "actor": "owner-1",
                "summary": "工作风格：结果导向。",
                "field_names": ["preferred_output", "working_style"],
            }
        ],
    )

    text = execute_distilled_profile_command(
        _message("画像历史 输出偏好"),
        {"action": "history", "field": "输出偏好"},
        can_manage_profiles=False,
    )

    assert "画像历史：张三" in text
    assert "字段：输出偏好" in text
    assert "发布画像 by owner-1" in text
    assert "字段：输出偏好、工作风格" in text
