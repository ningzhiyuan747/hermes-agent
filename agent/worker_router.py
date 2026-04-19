from __future__ import annotations

from dataclasses import dataclass
from typing import Iterable, List, Optional, Tuple


@dataclass(frozen=True)
class WorkerRoutingDecision:
    role: str
    reason: str
    matched_terms: List[str]


_ROLE_PATTERNS: dict[str, Tuple[str, ...]] = {
    "bid": (
        "bid", "bidding", "tender", "rfp", "rfq", "procurement",
        "招标", "投标", "标书", "采购", "资格", "招投标", "投标资料", "资格预审",
    ),
    "sales": (
        "sales", "customer", "client", "account", "lead", "pipeline", "outreach",
        "crm", "follow-up", "prospect", "客户", "销售", "商机", "询盘", "跟进", "成交",
    ),
    "ops": (
        "ops", "operation", "runtime", "deploy", "deployment", "incident", "server",
        "watchdog", "restart", "log", "logs", "service", "docker", "wsl", "healthcheck",
        "运维", "部署", "日志", "报错", "故障", "服务", "重启", "恢复", "监控",
    ),
    "critic": (
        "critic", "critique", "review", "validate", "validation", "risk", "risks",
        "adversarial", "check", "audit", "gap", "drift", "unsupported",
        "验证", "审查", "复核", "找问题", "风险", "漏洞", "偏移", "证据不足",
    ),
    "ceo": (
        "ceo", "strategy", "plan", "planning", "priority", "prioritize", "decision",
        "roadmap", "summary", "synthesis", "chief of staff",
        "规划", "策略", "优先级", "决策", "路线图", "总结", "汇总", "取舍",
    ),
}


def _normalize_text(parts: Iterable[Optional[str]]) -> str:
    merged = " ".join(part.strip().lower() for part in parts if isinstance(part, str) and part.strip())
    return merged


def route_worker_role(
    goal: str,
    context: Optional[str] = None,
    channel_hint: Optional[str] = None,
    chat_scope: Optional[str] = None,
    preferred_role: Optional[str] = None,
) -> WorkerRoutingDecision:
    normalized_scope = (chat_scope or "").strip().lower()
    if normalized_scope == "private":
        return WorkerRoutingDecision(
            role="ceo",
            reason="Auto-routed to ceo because the current session is a private chat.",
            matched_terms=[],
        )

    text = _normalize_text([goal, context, channel_hint])
    if not text:
        return WorkerRoutingDecision(
            role="ceo",
            reason="Fallback routing: empty task text defaults to the CEO worker.",
            matched_terms=[],
        )

    best_role = "ceo"
    best_terms: List[str] = []

    for role, terms in _ROLE_PATTERNS.items():
        matched = sorted({term for term in terms if term in text})
        if len(matched) > len(best_terms):
            best_role = role
            best_terms = matched

    if best_terms:
        return WorkerRoutingDecision(
            role=best_role,
            reason=f"Auto-routed to {best_role} based on matched terms: {', '.join(best_terms[:6])}.",
            matched_terms=best_terms,
        )

    normalized_preferred = (preferred_role or "").strip().lower()
    if normalized_scope == "group" and normalized_preferred in _ROLE_PATTERNS:
        return WorkerRoutingDecision(
            role=normalized_preferred,
            reason=f"Auto-routed to {normalized_preferred} based on the chat's preferred worker role.",
            matched_terms=[],
        )

    return WorkerRoutingDecision(
        role="ceo",
        reason="Fallback routing: no specialist keyword match, so the task stays with the CEO worker.",
        matched_terms=[],
    )
