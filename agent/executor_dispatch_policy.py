from __future__ import annotations

from typing import Any, Dict

from agent.executor_registry import get_executor_spec, normalize_executor_key


def decide_dispatch_action(
    *,
    status: str,
    current_executor: str,
    failure_kind: str = "",
    delivery_status: str = "",
    pending_approval_count: int = 0,
) -> Dict[str, Any]:
    normalized_status = str(status or "").strip().lower()
    normalized_failure = str(failure_kind or "").strip().lower()
    normalized_delivery_status = str(delivery_status or "").strip().lower()
    executor_key = normalize_executor_key(current_executor)
    executor_spec = get_executor_spec(executor_key)
    fallback_executor_key = str(executor_spec.get("fallback_executor_key") or "").strip().lower()

    if int(pending_approval_count or 0) > 0 or normalized_status == "pending_approval":
        return {
            "dispatch_action": "wait_approval",
            "suggested_executor": executor_key,
        }

    if normalized_status in {"queued", "running", "blocked", "paused"}:
        return {
            "dispatch_action": "continue_current",
            "suggested_executor": executor_key,
        }

    if normalized_failure in {"credential_failed", "routing_failed", "delivery_failed"} or normalized_delivery_status == "failed":
        return {
            "dispatch_action": "retry_delivery",
            "suggested_executor": executor_key,
        }

    if normalized_failure in {"infra_failed", "execution_failed"}:
        if fallback_executor_key and fallback_executor_key != executor_key:
            return {
                "dispatch_action": "switch_executor",
                "suggested_executor": fallback_executor_key,
            }
        return {
            "dispatch_action": "retry_same_executor",
            "suggested_executor": executor_key,
        }

    if normalized_status in {"failed", "cancelled"}:
        if fallback_executor_key and fallback_executor_key != executor_key:
            return {
                "dispatch_action": "switch_executor",
                "suggested_executor": fallback_executor_key,
            }
        return {
            "dispatch_action": "retry_same_executor",
            "suggested_executor": executor_key,
        }

    return {
        "dispatch_action": "noop",
        "suggested_executor": executor_key,
    }
