from __future__ import annotations

from typing import Any, Dict


_DEFAULT_EXECUTOR = "hermes"
_EXECUTOR_ALIASES = {
    "openclaw-worker": "openclaw",
    "hermes-background-job-worker": "hermes",
    "background-job-worker": "hermes",
}

_EXECUTOR_REGISTRY: dict[str, dict[str, Any]] = {
    "hermes": {
        "display_name": "Hermes",
        "route_behavior": "inline",
        "supports_background_job": False,
        "fallback_executor_key": "",
        "specialization": "general",
    },
    "openclaw": {
        "display_name": "OpenClaw",
        "route_behavior": "background_job",
        "supports_background_job": True,
        "fallback_executor_key": "hermes",
        "specialization": "background_research",
    },
    "codex": {
        "display_name": "Codex",
        "route_behavior": "external",
        "supports_background_job": False,
        "fallback_executor_key": "hermes",
        "specialization": "code_execution",
    },
    "omx": {
        "display_name": "OMX",
        "route_behavior": "external",
        "supports_background_job": False,
        "fallback_executor_key": "hermes",
        "specialization": "external_execution",
    },
}


def normalize_executor_key(value: str) -> str:
    normalized = str(value or "").strip().lower()
    normalized = _EXECUTOR_ALIASES.get(normalized, normalized)
    return normalized if normalized in _EXECUTOR_REGISTRY else _DEFAULT_EXECUTOR


def get_executor_spec(executor_key: str) -> Dict[str, Any]:
    normalized = normalize_executor_key(executor_key)
    return {
        "key": normalized,
        **dict(_EXECUTOR_REGISTRY.get(normalized) or _EXECUTOR_REGISTRY[_DEFAULT_EXECUTOR]),
    }


def resolve_route_executor(route: Dict[str, Any] | None) -> Dict[str, Any]:
    route = route if isinstance(route, dict) else {}
    executor_key = normalize_executor_key(str(route.get("executor") or "").strip())
    return get_executor_spec(executor_key)
