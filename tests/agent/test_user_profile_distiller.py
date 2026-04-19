from agent import user_profile_distiller as distiller


def test_distill_user_profile_builds_structured_profile(monkeypatch):
    monkeypatch.setattr(distiller, "get_user", lambda **_kwargs: {"user_id": "u-1"})
    monkeypatch.setattr(distiller, "get_user_memory", lambda **kwargs: {"summary": "默认中文；先给结论；输出简洁"} if kwargs.get("scope") == "profile" else {"summary": "审批前先确认；尽量列表化"})
    monkeypatch.setattr(distiller, "list_tasks_for_user", lambda **_kwargs: [{"title": "跟进重点客户 A"}, {"title": "整理招标线索"}])
    monkeypatch.setattr(distiller, "list_background_jobs_for_user", lambda **_kwargs: [{"title": "OpenClaw 招标研究"}])
    monkeypatch.setattr(
        distiller,
        "list_capability_runs_for_user",
        lambda **_kwargs: [
            {"capability_name": "bid_research", "approval_id": "approval-1", "status": "queued"},
            {"capability_name": "bid_research", "approval_id": "", "status": "completed"},
        ],
    )
    captured = {}

    def _capture(**kwargs):
        captured.update(kwargs)
        return kwargs

    monkeypatch.setattr(distiller, "save_user_distilled_profile_draft", _capture)

    record = distiller.distill_user_profile(platform="dingtalk", user_id="u-1", since_days=30, limit=10)

    assert record["updated_by"] == "distiller"
    profile = captured["profile"]
    assert profile["core_principles"]
    assert profile["decision_heuristics"]
    assert profile["anti_patterns"]
    assert profile["preferred_output"]
    evidence = captured["evidence"]
    assert "domain_focus" in evidence
    assert evidence["approval_sensitivity"][0]["kind"] == "approval_stats"
    sources = captured["sources"]
    assert sources["recent_task_titles"] == ["跟进重点客户 A", "整理招标线索"]
