from __future__ import annotations

import base64
import importlib.util
import json
import sys
from pathlib import Path


SCRIPT_PATH = Path(__file__).resolve().parents[2] / "scripts" / "platform_memory_writeback.py"


def load_module():
    sys.modules.pop("platform_memory_writeback_test", None)
    spec = importlib.util.spec_from_file_location("platform_memory_writeback_test", SCRIPT_PATH)
    module = importlib.util.module_from_spec(spec)
    assert spec.loader is not None
    sys.modules[spec.name] = module
    spec.loader.exec_module(module)
    return module


def test_load_payload_decodes_base64_json():
    mod = load_module()
    payload = {"platform": "dingtalk", "user_id": "u-1", "entry": "用户偏好：简洁"}
    encoded = base64.b64encode(json.dumps(payload, ensure_ascii=False).encode("utf-8")).decode("ascii")

    assert mod._load_payload(encoded) == payload


def test_run_private_preference_writeback_uses_memory_tool_and_distiller(monkeypatch):
    mod = load_module()
    captured: dict[str, object] = {}

    monkeypatch.setattr(
        mod,
        "memory_tool",
        lambda **kwargs: captured.update({"memory_tool": kwargs}) or json.dumps({"success": True}, ensure_ascii=False),
    )
    monkeypatch.setattr(
        mod,
        "distill_user_profile",
        lambda **kwargs: captured.update({"distill": kwargs}) or {"summary": "偏好已蒸馏"},
    )

    result = mod.run_private_preference_writeback(
        {"platform": "dingtalk", "user_id": "u-1", "entry": "用户偏好：简洁"}
    )

    assert result["ok"] is True
    assert result["write"]["success"] is True
    assert result["distilled_summary"] == "偏好已蒸馏"
    assert captured["memory_tool"]["action"] == "add"
    assert captured["memory_tool"]["target"] == "user"
    assert captured["memory_tool"]["content"] == "用户偏好：简洁"
    assert captured["distill"] == {"platform": "dingtalk", "user_id": "u-1", "since_days": 30, "limit": 20}
