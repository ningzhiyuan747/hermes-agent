from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "control_tower.py"


def load_module():
    spec = importlib.util.spec_from_file_location("control_tower_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_control_tower_report_renders_summary():
    mod = load_module()
    report = mod.build_report(
        {
            "summary": {
                "active_task_count": 2,
                "secretary_action_count": 3,
                "auto_safe_action_count": 1,
                "manual_action_count": 2,
                "follow_up_due_count": 1,
                "escalation_due_count": 1,
                "follow_up_item_count": 1,
                "reminded_follow_up_count": 1,
                "cooling_follow_up_count": 1,
                "unsent_follow_up_count": 0,
                "operator_queue_count": 1,
                "task_status_counts": {"pending_approval": 2, "failed": 1},
            },
            "secretary": {
                "actions": [
                    {
                        "dispatch_action": "wait_approval",
                        "suggested_executor": "openclaw",
                        "task_id": "task-1",
                        "task_title": "Need approval",
                        "follow_up_due": True,
                        "follow_up_level": "escalate",
                    }
                ],
                "follow_up_items": [
                    {
                        "follow_up_level": "escalate",
                        "target_ref": "feishu:oc_approval",
                        "target_kind": "origin_chat",
                        "task_id": "task-1",
                        "summary": "Approval has been pending for 180 minutes; escalate owner/admin on feishu:chat:oc_approval.",
                    }
                ],
                "reminded_follow_up_items": [
                    {
                        "follow_up_level": "escalate",
                        "target_ref": "feishu:oc_approval",
                        "task_id": "task-1",
                        "summary": "Approval has been pending for 180 minutes; escalate owner/admin on feishu:chat:oc_approval.",
                    }
                ],
            },
            "operator_worklist": {
                "items": [
                    {
                        "kind": "switch_executor",
                        "requested_executor": "codex",
                        "task_id": "task-2",
                        "summary": "Switch task to executor 'codex'.",
                    }
                ]
            },
            "board": {
                "derived_signals": ["signal-a"],
            },
        },
        limit=5,
    )

    assert "Control Tower" in report
    assert "活跃任务: 2" in report
    assert "秘书动作: 3" in report
    assert "催办到期: 1" in report
    assert "升级到期: 1" in report
    assert "提醒草案: 1" in report
    assert "已提醒: 1" in report
    assert "冷却中: 1" in report
    assert "待首次提醒: 0" in report
    assert "Operator 待办: 1" in report
    assert "wait_approval -> openclaw | task-1 | Need approval | 跟进=escalate" in report
    assert "escalate | feishu:oc_approval | task-1 | Approval has been pending for 180 minutes" in report
    assert "已提醒:" in report
    assert "switch_executor | codex | task-2 | Switch task to executor 'codex'." in report
    assert "signal-a" in report
