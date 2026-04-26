from __future__ import annotations

import json
import os
from functools import lru_cache
from pathlib import Path
from typing import Any


_DEFAULT_POLICY_PATH = Path(
    os.getenv("HERMES_CAPABILITY_POLICY_FILE", "/mnt/f/hermes-control-plane/capability_execution_policy.json")
)
_DEFAULT_POLICY = {"executor": "hermes", "route_mode": "guided"}


@lru_cache(maxsize=4)
def load_capability_execution_policy(policy_path: str | None = None) -> dict[str, Any]:
    path = Path(policy_path) if policy_path else _DEFAULT_POLICY_PATH
    try:
        raw = json.loads(path.read_text(encoding="utf-8"))
    except Exception:
        return {"default": dict(_DEFAULT_POLICY), "capabilities": {}}
    if not isinstance(raw, dict):
        return {"default": dict(_DEFAULT_POLICY), "capabilities": {}}
    default = raw.get("default") if isinstance(raw.get("default"), dict) else {}
    capabilities = raw.get("capabilities") if isinstance(raw.get("capabilities"), dict) else {}
    return {
        "default": {**_DEFAULT_POLICY, **default},
        "capabilities": capabilities,
    }


def get_capability_execution_policy(capability: str, policy_path: str | None = None) -> dict[str, Any]:
    data = load_capability_execution_policy(policy_path)
    default = dict(data.get("default") or _DEFAULT_POLICY)
    item = (data.get("capabilities") or {}).get(str(capability or "").strip(), {})
    if not isinstance(item, dict):
        item = {}
    merged = {**default, **item}
    merged["executor"] = str(merged.get("executor") or default.get("executor") or "hermes").strip().lower()
    merged["route_mode"] = str(merged.get("route_mode") or default.get("route_mode") or "guided").strip().lower()
    return merged
