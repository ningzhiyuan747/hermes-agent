from agent import business_db


def test_admin_in_feishu_private_chat_bypasses_business_approval(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "owner_control_surface")
    monkeypatch.delenv("HERMES_FEISHU_DISABLE_APPROVALS", raising=False)

    business_db.upsert_user(platform="feishu", user_id="ou_admin_1", role="admin")
    business_db.upsert_channel(
        platform="feishu",
        chat_id="oc_private_1",
        thread_id="",
        chat_name="owner-dm",
        chat_type="dm",
    )

    decision = business_db.evaluate_capability_access(
        capability="customer_followup",
        actor_user_id="ou_admin_1",
        platform="feishu",
        chat_id="oc_private_1",
        thread_id="",
    )

    assert decision["allowed"] is True
    assert decision["approval_required"] is False
    assert "control surface" in str(decision["reason"] or "").lower()


def test_admin_in_feishu_group_still_requires_business_approval(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "owner_control_surface")
    monkeypatch.delenv("HERMES_FEISHU_DISABLE_APPROVALS", raising=False)

    business_db.upsert_user(platform="feishu", user_id="ou_admin_1", role="admin")
    business_db.upsert_channel(
        platform="feishu",
        chat_id="oc_group_1",
        thread_id="",
        chat_name="biz-group",
        chat_type="group",
    )

    decision = business_db.evaluate_capability_access(
        capability="customer_followup",
        actor_user_id="ou_admin_1",
        platform="feishu",
        chat_id="oc_group_1",
        thread_id="",
    )

    assert decision["allowed"] is True
    assert decision["approval_required"] is True


def test_feishu_disable_approvals_bypasses_group_business_approval(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "enforce")
    monkeypatch.setenv("HERMES_FEISHU_DISABLE_APPROVALS", "1")

    business_db.upsert_user(platform="feishu", user_id="ou_staff_1", role="user")
    business_db.upsert_channel(
        platform="feishu",
        chat_id="oc_group_1",
        thread_id="",
        chat_name="biz-group",
        chat_type="group",
    )

    decision = business_db.evaluate_capability_access(
        capability="customer_followup",
        actor_user_id="ou_staff_1",
        platform="feishu",
        chat_id="oc_group_1",
        thread_id="",
    )

    assert decision["allowed"] is True
    assert decision["approval_required"] is False
    assert "feishu" in str(decision["reason"] or "").lower()


def test_admin_in_weixin_private_chat_bypasses_business_approval(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "owner_control_surface")

    business_db.upsert_user(platform="weixin", user_id="wx_admin_1", role="admin")
    business_db.upsert_channel(
        platform="weixin",
        chat_id="wx_dm_1",
        thread_id="",
        chat_name="boss-dm",
        chat_type="dm",
    )

    decision = business_db.evaluate_capability_access(
        capability="customer_followup",
        actor_user_id="wx_admin_1",
        platform="weixin",
        chat_id="wx_dm_1",
        thread_id="",
    )

    assert decision["allowed"] is True
    assert decision["approval_required"] is False


def test_business_approval_mode_off_disables_business_approval_globally(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "off")

    business_db.upsert_user(platform="dingtalk", user_id="staff_1", role="user")
    business_db.upsert_channel(
        platform="dingtalk",
        chat_id="cid_1",
        thread_id="",
        chat_name="biz-group",
        chat_type="group",
    )

    decision = business_db.evaluate_capability_access(
        capability="customer_followup",
        actor_user_id="staff_1",
        platform="dingtalk",
        chat_id="cid_1",
        thread_id="",
    )

    assert decision["allowed"] is True
    assert decision["approval_required"] is False
    assert "rollout is off" in str(decision["reason"] or "").lower()


def test_cancelled_approval_transitions_capability_run_out_of_pending(tmp_path, monkeypatch):
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)
    monkeypatch.setenv("HERMES_BUSINESS_APPROVAL_MODE", "enforce")

    run = business_db.create_capability_run(
        capability="customer_followup",
        title="Pending approval smoke",
        goal="Follow up with the customer",
        origin={
            "platform": "dingtalk",
            "chat_id": "cid_group_1",
            "chat_name": "Sales group",
            "chat_type": "group",
            "thread_id": "",
        },
        actor_user_id="staff_1",
        session_id="agent:main:dingtalk:group:cid_group_1:staff_1",
        priority="normal",
        input_data={},
    )

    assert run["status"] == "pending_approval"
    approval_id = str(run.get("approval_id") or "").strip()
    assert approval_id

    business_db.decide_approval(approval_id, status="cancelled", approved_by="rollout-cleanup")
    refreshed = business_db.get_capability_run(str(run["run_id"]))

    assert refreshed is not None
    assert refreshed["status"] == "cancelled"
    assert "cancelled before execution" in str(refreshed.get("result") or "").lower()
