from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "operator_queue_action.py"


def load_module():
    spec = importlib.util.spec_from_file_location("operator_queue_action_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_operator_queue_action_script_uses_service(monkeypatch, capsys):
    mod = load_module()
    monkeypatch.setattr(
        mod,
        "complete_operator_queue_item",
        lambda task_id, queue_item_id, resolution="completed", note="": {
            "ok": True,
            "message": "resolved",
            "details": {
                "task_id": task_id,
                "queue_item_id": queue_item_id,
                "resolution": resolution,
                "note": note,
            },
        },
    )
    monkeypatch.setattr(
        sys,
        "argv",
        [
            "operator_queue_action.py",
            "--task-id",
            "task-1",
            "--queue-item-id",
            "switch_executor:task-1:1",
            "--resolution",
            "completed",
            "--note",
            "done",
            "--json",
        ],
    )

    mod.main()
    out = capsys.readouterr().out

    assert '"task_id": "task-1"' in out
    assert '"queue_item_id": "switch_executor:task-1:1"' in out
