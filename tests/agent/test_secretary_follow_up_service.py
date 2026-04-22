import agent.secretary_follow_up_service as service


def test_execute_secretary_follow_up_dry_run_returns_preview(monkeypatch):
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "wait_approval:task-1",
                    "task_id": "task-1",
                    "task_title": "Need approval",
                    "target_kind": "origin_chat",
                    "target_ref": "feishu:oc_123",
                    "target_platform": "feishu",
                    "target_chat_id": "oc_123",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "summary": "Approval has been pending.",
                    "message_preview": "升级提醒: Need approval",
                }
            ]
        },
    )
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": {"task_id": task_id, "metadata": {}}})

    monkeypatch.setattr(service.time, "time", lambda: 10_000)

    result = service.execute_secretary_follow_up(task_id="task-1", dry_run=True)

    assert result["ok"] is True
    assert result["details"]["dry_run"] is True
    assert result["details"]["target_ref"] == "feishu:oc_123"
    assert result["details"]["cooldown_active"] is False


def test_execute_secretary_follow_up_sends_origin_chat_and_records(monkeypatch):
    task_updates = []
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "wait_approval:task-2",
                    "task_id": "task-2",
                    "task_title": "Need approval",
                    "target_kind": "origin_chat",
                    "target_ref": "dingtalk:cid_123",
                    "target_platform": "dingtalk",
                    "target_chat_id": "cid_123",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "summary": "Approval has been pending.",
                    "message_preview": "升级提醒: Need approval",
                }
            ]
        },
    )
    monkeypatch.setattr(service.time, "time", lambda: 20_000)
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": {"task_id": task_id, "metadata": {}}})
    monkeypatch.setattr(service, "send_text_to_target", lambda target, text: {"ok": True, "target": target.to_target_ref(), "text": text})
    monkeypatch.setattr(
        service,
        "update_task",
        lambda task_id, **fields: task_updates.append({"task_id": task_id, **fields}) or {"task_id": task_id, "metadata": fields.get("metadata", {})},
    )

    result = service.execute_secretary_follow_up(action_id="wait_approval:task-2", dry_run=False)

    assert result["ok"] is True
    assert result["details"]["delivery_response"]["target"] == "dingtalk:cid_123"
    assert task_updates[0]["metadata"]["control_plane"]["last_follow_up_target_ref"] == "dingtalk:cid_123"


def test_execute_secretary_follow_up_rejects_cooldown_duplicate(monkeypatch):
    monkeypatch.setattr(service.time, "time", lambda: 30_000)
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "wait_approval:task-2",
                    "task_id": "task-2",
                    "task_title": "Need approval",
                    "target_kind": "origin_chat",
                    "target_ref": "dingtalk:cid_123",
                    "target_platform": "dingtalk",
                    "target_chat_id": "cid_123",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "summary": "Approval has been pending.",
                    "message_preview": "升级提醒: Need approval",
                }
            ]
        },
    )
    monkeypatch.setattr(
        service,
        "sync_task_control_state",
        lambda task_id: {
            "task": {
                "task_id": task_id,
                "metadata": {
                    "secretary_follow_up": {
                        "last_item": {
                            "action_id": "wait_approval:task-2",
                            "target_ref": "dingtalk:cid_123",
                            "recorded_at_unix": 29_000,
                        }
                    }
                },
            }
        },
    )

    result = service.execute_secretary_follow_up(action_id="wait_approval:task-2", dry_run=False)

    assert result["ok"] is False
    assert "cooling down" in result["message"]
    assert result["details"]["cooldown_active"] is True
    assert result["details"]["remaining_cooldown_seconds"] > 0


def test_execute_secretary_follow_up_rejects_non_origin_chat_targets(monkeypatch):
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "switch_executor:task-3",
                    "task_id": "task-3",
                    "target_kind": "operator_queue",
                    "target_ref": "operator_queue",
                }
            ]
        },
    )
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": {"task_id": task_id, "metadata": {}}})

    result = service.execute_secretary_follow_up(task_id="task-3", dry_run=False)

    assert result["ok"] is False
    assert "not yet sendable" in result["message"]


def test_execute_due_secretary_follow_ups_dry_run_filters_levels_and_limits(monkeypatch):
    monkeypatch.setattr(service.time, "time", lambda: 40_000)
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "wait_approval:task-1",
                    "task_id": "task-1",
                    "task_title": "Need approval 1",
                    "target_kind": "origin_chat",
                    "target_ref": "feishu:oc_1",
                    "target_platform": "feishu",
                    "target_chat_id": "oc_1",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "stale_for_seconds": 600,
                    "summary": "Approval has been pending 1.",
                    "message_preview": "升级提醒: Need approval 1",
                },
                {
                    "action_id": "wait_approval:task-2",
                    "task_id": "task-2",
                    "task_title": "Need approval 2",
                    "target_kind": "origin_chat",
                    "target_ref": "dingtalk:cid_2",
                    "target_platform": "dingtalk",
                    "target_chat_id": "cid_2",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "stale_for_seconds": 1200,
                    "summary": "Approval has been pending 2.",
                    "message_preview": "升级提醒: Need approval 2",
                },
                {
                    "action_id": "wait_approval:task-3",
                    "task_id": "task-3",
                    "task_title": "Need approval 3",
                    "target_kind": "origin_chat",
                    "target_ref": "weixin:wx_3",
                    "target_platform": "weixin",
                    "target_chat_id": "wx_3",
                    "target_thread_id": "",
                    "follow_up_level": "nudge",
                    "stale_for_seconds": 1800,
                    "summary": "Approval has been pending 3.",
                    "message_preview": "提醒: Need approval 3",
                },
            ]
        },
    )
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": {"task_id": task_id, "metadata": {}}})

    result = service.execute_due_secretary_follow_ups(limit=1, levels=("escalate",), dry_run=True)

    assert result["ok"] is True
    assert result["details"]["candidate_count"] == 1
    assert result["details"]["ok_count"] == 1
    assert result["details"]["results"][0]["details"]["action_id"] == "wait_approval:task-2"
    assert result["details"]["results"][0]["details"]["dry_run"] is True


def test_execute_due_secretary_follow_ups_sends_multiple_items(monkeypatch):
    sent_targets = []
    monkeypatch.setattr(service.time, "time", lambda: 50_000)
    monkeypatch.setattr(
        service,
        "build_secretary_loop_snapshot",
        lambda limit=0: {
            "follow_up_items": [
                {
                    "action_id": "wait_approval:task-4",
                    "task_id": "task-4",
                    "task_title": "Need approval 4",
                    "target_kind": "origin_chat",
                    "target_ref": "feishu:oc_4",
                    "target_platform": "feishu",
                    "target_chat_id": "oc_4",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "stale_for_seconds": 1400,
                    "summary": "Approval has been pending 4.",
                    "message_preview": "升级提醒: Need approval 4",
                },
                {
                    "action_id": "wait_approval:task-5",
                    "task_id": "task-5",
                    "task_title": "Need approval 5",
                    "target_kind": "origin_chat",
                    "target_ref": "dingtalk:cid_5",
                    "target_platform": "dingtalk",
                    "target_chat_id": "cid_5",
                    "target_thread_id": "",
                    "follow_up_level": "escalate",
                    "stale_for_seconds": 1300,
                    "summary": "Approval has been pending 5.",
                    "message_preview": "升级提醒: Need approval 5",
                },
            ]
        },
    )
    monkeypatch.setattr(service, "sync_task_control_state", lambda task_id: {"task": {"task_id": task_id, "metadata": {}}})
    monkeypatch.setattr(service, "send_text_to_target", lambda target, text: sent_targets.append(target.to_target_ref()) or {"ok": True, "target": target.to_target_ref(), "text": text})
    monkeypatch.setattr(service, "update_task", lambda task_id, **fields: {"task_id": task_id, "metadata": fields.get("metadata", {})})

    result = service.execute_due_secretary_follow_ups(limit=2, levels=("escalate",), dry_run=False)

    assert result["ok"] is True
    assert result["details"]["candidate_count"] == 2
    assert result["details"]["ok_count"] == 2
    assert sent_targets == ["feishu:oc_4", "dingtalk:cid_5"]
