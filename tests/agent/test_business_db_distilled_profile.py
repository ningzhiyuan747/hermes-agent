from agent import business_db


def test_upsert_user_distilled_profile_preserves_locked_fields(tmp_path, monkeypatch):
    db_file = tmp_path / "distilled.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="dingtalk", user_id="u-1", display_name="User 1")
    business_db.upsert_user_distilled_profile(
        platform="dingtalk",
        user_id="u-1",
        profile={
            "working_style": "结果导向",
            "preferred_output": "先结论后细节",
            "domain_focus": "招投标机会筛选",
            "approval_sensitivity": "中",
        },
        evidence={"working_style": [{"kind": "notes_summary", "text": "先说结果"}]},
        sources={"source_window_days": 30},
        updated_by="distiller",
    )
    business_db.set_user_distilled_profile_overrides(
        platform="dingtalk",
        user_id="u-1",
        overrides={"preferred_output": "先给一句话结论，再给清单"},
        locked_fields=["preferred_output"],
    )

    record = business_db.upsert_user_distilled_profile(
        platform="dingtalk",
        user_id="u-1",
        profile={
            "working_style": "过程导向",
            "preferred_output": "长文细讲",
            "domain_focus": "合同与证据检索",
            "approval_sensitivity": "低",
        },
        evidence={"preferred_output": [{"kind": "profile_summary", "text": "喜欢长文"}]},
        sources={"source_window_days": 15},
        updated_by="distiller",
    )

    memory = record["memory"]
    assert memory["profile"]["working_style"] == "过程导向"
    assert memory["profile"]["preferred_output"] == "先给一句话结论，再给清单"
    assert memory["manual_overrides"]["preferred_output"] == "先给一句话结论，再给清单"
    assert memory["locked_fields"] == ["preferred_output"]
    assert memory["sources"]["source_window_days"] == 15


def test_publish_user_distilled_profile_draft_promotes_draft_without_overwriting_locked_override(tmp_path, monkeypatch):
    db_file = tmp_path / "distilled-draft.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="dingtalk", user_id="u-2", display_name="User 2")
    business_db.upsert_user_distilled_profile(
        platform="dingtalk",
        user_id="u-2",
        profile={"preferred_output": "先一句话再清单", "working_style": "结果导向"},
        evidence={},
        sources={"source_window_days": 30},
        manual_overrides={"preferred_output": "先一句话再清单"},
        locked_fields=["preferred_output"],
        updated_by="manual_override",
    )
    business_db.save_user_distilled_profile_draft(
        platform="dingtalk",
        user_id="u-2",
        summary="工作风格：过程导向。",
        profile={"preferred_output": "长文详述", "working_style": "过程导向"},
        evidence={"working_style": [{"kind": "notes_summary", "text": "偏好边做边看"}]},
        sources={"source_window_days": 7},
        updated_by="distiller",
    )

    record = business_db.publish_user_distilled_profile_draft(
        platform="dingtalk",
        user_id="u-2",
        published_by="owner-1",
    )

    memory = record["memory"]
    assert memory["profile"]["working_style"] == "过程导向"
    assert memory["profile"]["preferred_output"] == "先一句话再清单"
    assert memory["draft"]["profile"] == {}
    assert record["summary"] == "工作风格：过程导向。"


def test_distilled_profile_history_tracks_draft_publish_and_override(tmp_path, monkeypatch):
    db_file = tmp_path / "distilled-history.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="dingtalk", user_id="u-3", display_name="User 3")
    business_db.save_user_distilled_profile_draft(
        platform="dingtalk",
        user_id="u-3",
        summary="输出偏好：先结论。",
        profile={"preferred_output": "先结论"},
        updated_by="distiller",
    )
    business_db.publish_user_distilled_profile_draft(
        platform="dingtalk",
        user_id="u-3",
        published_by="owner-1",
    )
    business_db.set_user_distilled_profile_overrides(
        platform="dingtalk",
        user_id="u-3",
        overrides={"preferred_output": "先一句话后清单"},
        locked_fields=["preferred_output"],
    )

    history = business_db.list_user_distilled_profile_history(platform="dingtalk", user_id="u-3", limit=5)

    assert len(history) >= 3
    assert history[0]["action"] == "override_set"
    assert history[1]["action"] == "draft_discarded"
    assert any(item["action"] == "published" for item in history)
    assert any(item["action"] == "draft_saved" for item in history)
