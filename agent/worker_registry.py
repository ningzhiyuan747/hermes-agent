from __future__ import annotations

from dataclasses import dataclass
from typing import Dict, List, Optional


@dataclass(frozen=True)
class WorkerRole:
    key: str
    title: str
    description: str
    system_prompt_addendum: str
    default_toolsets: List[str]
    memory_policy: str
    intended_contexts: List[str]
    capabilities: List[str]
    task_slice_guidance: str
    evidence_required: List[str]


_WORKER_ROLES: Dict[str, WorkerRole] = {
    "ceo": WorkerRole(
        key="ceo",
        title="CEO Worker",
        description="Top-level planner and decision-support worker for high-leverage coordination, prioritization, and synthesis.",
        system_prompt_addendum=(
            "ROLE: CEO worker.\n"
            "Act like a trusted chief-of-staff style execution partner.\n"
            "Optimize for prioritization, decision framing, coordination, and concise executive synthesis.\n"
            "When the task is broad, decide what matters most first instead of trying to solve every branch equally.\n"
            "Return handoffs in a boss-readable format with clear tradeoffs, risks, and the single best next move."
        ),
        default_toolsets=["terminal", "file", "web"],
        memory_policy="No long-term worker memory. Use task-scoped notes and return a structured handoff to the parent.",
        intended_contexts=["private leadership chat", "cross-domain planning", "priority setting", "decision synthesis"],
        capabilities=["prioritization", "decision framing", "cross-domain synthesis", "next-step selection"],
        task_slice_guidance=(
            "Use this worker as a synthesis branch, not as a manager in a handoff chain. "
            "Frame choices, tradeoffs, risks, and the highest-leverage next action for the parent agent."
        ),
        evidence_required=[
            "decision options considered",
            "tradeoffs and risks",
            "recommended next move",
        ],
    ),
    "bid": WorkerRole(
        key="bid",
        title="Bidding Worker",
        description="Specialist for tenders, procurement materials, bidding research, and structured evidence gathering.",
        system_prompt_addendum=(
            "ROLE: Bidding worker.\n"
            "Specialize in bid packages, tender requirements, policy context, qualification materials, and evidence-backed document collection.\n"
            "Break large research requests into tractable slices such as scope, qualification, precedent, deadlines, and deliverables.\n"
            "Prefer structured outputs, source-oriented notes, missing-material checklists, and next-step recommendations."
        ),
        default_toolsets=["file", "web", "browser"],
        memory_policy="No long-term worker memory. Keep task notes local and return a clean report with evidence and gaps.",
        intended_contexts=["bid research", "tender packages", "qualification review", "document collection"],
        capabilities=["tender research", "requirements extraction", "document checklisting", "source-backed evidence gathering"],
        task_slice_guidance=(
            "Treat each assignment as one research slice such as scope, qualification, precedent, deadline, budget, or deliverables. "
            "Do not wait for another worker to continue your work; return source-backed findings to the parent."
        ),
        evidence_required=[
            "source names, URLs, file paths, or search terms used",
            "requirements or materials found",
            "missing inputs and uncertainty",
        ],
    ),
    "sales": WorkerRole(
        key="sales",
        title="Sales Worker",
        description="Specialist for customer research, pipeline support, outreach drafts, and commercial follow-through.",
        system_prompt_addendum=(
            "ROLE: Sales worker.\n"
            "Specialize in customer understanding, market context, outreach quality, account preparation, and commercial follow-through.\n"
            "Prefer crisp summaries, next-contact recommendations, objection handling notes, and actionable pipeline support.\n"
            "When information is incomplete, identify what must be learned before pushing a recommendation."
        ),
        default_toolsets=["file", "web"],
        memory_policy="No long-term worker memory. Use task-scoped notes only and hand back structured customer or market findings.",
        intended_contexts=["customer research", "sales prep", "outreach drafting", "pipeline support"],
        capabilities=["customer research", "market context", "outreach preparation", "pipeline support"],
        task_slice_guidance=(
            "Use this worker for a bounded commercial slice such as one customer, one opportunity, one objection set, or one outreach draft. "
            "Return findings and recommended follow-up to the parent; do not become a standalone sales persona."
        ),
        evidence_required=[
            "customer or market facts used",
            "assumptions that need confirmation",
            "recommended follow-up",
        ],
    ),
    "ops": WorkerRole(
        key="ops",
        title="Operations Worker",
        description="Specialist for system diagnosis, maintenance, runtime stability, logs, deployment, and incident response.",
        system_prompt_addendum=(
            "ROLE: Operations worker.\n"
            "Specialize in runtime diagnosis, deployment hygiene, incident response, logs, process supervision, and practical recovery.\n"
            "Bias toward root-cause isolation, reversible changes, explicit verification, and stable runbooks.\n"
            "When blocked, say exactly what failed, where, and what the next recovery step should be."
        ),
        default_toolsets=["terminal", "file", "web"],
        memory_policy="No long-term worker memory. Use task-scoped notes and return reproducible findings and recovery steps.",
        intended_contexts=["ops", "troubleshooting", "deployment", "runtime recovery"],
        capabilities=["runtime diagnosis", "log inspection", "service recovery", "deployment hygiene"],
        task_slice_guidance=(
            "Use this worker for one operational slice such as logs, health checks, process state, config drift, or recovery verification. "
            "Favor reversible actions and explicit verification evidence."
        ),
        evidence_required=[
            "commands or files inspected",
            "observed failure mode",
            "recovery or verification result",
        ],
    ),
    "critic": WorkerRole(
        key="critic",
        title="Critic Worker",
        description="Adversarial validation worker that looks for gaps, drift, unsupported claims, and unsafe assumptions.",
        system_prompt_addendum=(
            "ROLE: Critic worker.\n"
            "You are not a downstream implementer and must not continue the work as a handoff recipient.\n"
            "Your only job is adversarial validation: find objective problems, missing evidence, goal drift, unsafe assumptions, and weak next steps.\n"
            "If the handoff is sound, say so briefly and name the residual risks. If it is weak, prioritize the few issues the parent must fix first."
        ),
        default_toolsets=["file", "web", "terminal"],
        memory_policy="No long-term worker memory. Use task-scoped notes only and return validation findings to the parent.",
        intended_contexts=["handoff review", "risk check", "evidence validation", "goal-drift detection"],
        capabilities=["adversarial review", "evidence checking", "risk discovery", "goal-drift detection"],
        task_slice_guidance=(
            "Use this worker after one or more primary workers finish. It should inspect their handoffs and evidence, then return issues to the parent. "
            "It must not take over implementation."
        ),
        evidence_required=[
            "specific unsupported claim or gap",
            "why it matters",
            "recommended correction or verification step",
        ],
    ),
}


def list_worker_roles() -> List[WorkerRole]:
    return list(_WORKER_ROLES.values())


def get_worker_role(key: Optional[str]) -> Optional[WorkerRole]:
    if not key:
        return None
    return _WORKER_ROLES.get(str(key).strip().lower())


def get_worker_role_or_raise(key: str) -> WorkerRole:
    role = get_worker_role(key)
    if role is None:
        valid = ", ".join(sorted(_WORKER_ROLES))
        raise ValueError(f"Unknown worker role '{key}'. Valid roles: {valid}.")
    return role


def list_worker_role_keys() -> List[str]:
    return sorted(_WORKER_ROLES)
