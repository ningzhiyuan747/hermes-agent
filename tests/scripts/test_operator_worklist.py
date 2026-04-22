from __future__ import annotations

import importlib.util
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "operator_worklist.py"


def load_module():
    spec = importlib.util.spec_from_file_location("operator_worklist_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_operator_worklist_report_renders_items():
    mod = load_module()
    report = mod.build_report(
        {
            "all_item_count": 1,
            "items": [
                {
                    "queue_item_id": "switch_executor:task-1:1",
                    "kind": "switch_executor",
                    "requested_executor": "codex",
                    "task_id": "task-1",
                    "task_title": "Task One",
                    "summary": "Switch task to executor 'codex'.",
                }
            ],
        }
    )

    assert "Operator Worklist" in report
    assert "待处理总数: 1" in report
    assert "switch_executor | codex | task-1 | Task One" in report

