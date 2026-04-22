from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "secretary_action.py"


def load_module():
    spec = importlib.util.spec_from_file_location("secretary_action_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_secretary_action_script_uses_service(monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(
        mod,
        "execute_secretary_action",
        lambda task_id, action, auto_safe_only=False: {
            "ok": True,
            "message": f"ran {action} for {task_id}",
            "details": {"task_id": task_id, "action": action, "auto_safe_only": auto_safe_only},
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        ["secretary_action.py", "--task-id", "task-1", "--action", "retry_delivery", "--auto-safe-only", "--json"],
    )

    mod.main()
    out = capsys.readouterr().out

    assert '"ok": true' in out.lower()
    assert '"task_id": "task-1"' in out
    assert '"action": "retry_delivery"' in out


def test_secretary_action_script_allow_unsafe_disables_auto_safe_guard(monkeypatch):
    mod = load_module()
    captured = {}

    def fake_execute(task_id, action, auto_safe_only=False):
        captured["task_id"] = task_id
        captured["action"] = action
        captured["auto_safe_only"] = auto_safe_only
        return {"ok": True, "message": "ok", "details": {}}

    monkeypatch.setattr(mod, "execute_secretary_action", fake_execute)
    monkeypatch.setattr(
        sys,
        "argv",
        ["secretary_action.py", "--task-id", "task-2", "--action", "switch_executor", "--auto-safe-only", "--allow-unsafe"],
    )

    mod.main()

    assert captured == {
        "task_id": "task-2",
        "action": "switch_executor",
        "auto_safe_only": False,
    }
