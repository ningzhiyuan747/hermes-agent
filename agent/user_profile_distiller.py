from __future__ import annotations

from collections import Counter
import re
import time
from typing import Any

from agent.business_db import (
    get_user,
    get_user_memory,
    list_background_jobs_for_user,
    list_capability_runs_for_user,
    list_recent_users,
    list_tasks_for_user,
    save_user_distilled_profile_draft,
)


_CAPABILITY_LABELS = {
    "bid_research": "招投标机会筛选",
    "contract_retrieval": "合同与证据检索",
    "customer_followup": "客户跟进与商务推进",
    "ops_recovery": "运维恢复与故障排查",
}

_PRINCIPLE_HINTS = (
    ("先给结论", ("结论", "先说结果", "直接")),
    ("尽量简洁", ("简洁", "简短", "精简")),
    ("默认中文沟通", ("中文", "汉语")),
    ("优先结构化输出", ("表格", "清单", "列表", "分点")),
    ("高风险动作先确认", ("审批", "确认", "风险")),
)


def _split_clauses(text: str) -> list[str]:
    raw = re.split(r"[。\n；;]+", str(text or "").strip())
    return [item.strip(" -•\t") for item in raw if item.strip(" -•\t")]


def _join_top(counter: Counter[str], *, fallback: str, limit: int = 3) -> str:
    items = [label for label, _count in counter.most_common(limit) if label]
    if not items:
        return fallback
    return "、".join(items)


def _infer_preferred_output(profile_summary: str, notes_summary: str) -> str:
    combined = f"{profile_summary} {notes_summary}"
    preferences: list[str] = []
    if any(token in combined for token in ("中文", "汉语")):
        preferences.append("默认中文")
    if any(token in combined for token in ("简洁", "简短", "精简")):
        preferences.append("优先简洁表达")
    if any(token in combined for token in ("结论", "先说结果", "直接")):
        preferences.append("先给结论再补细节")
    if any(token in combined for token in ("表格", "清单", "列表", "分点")):
        preferences.append("偏好清单或结构化输出")
    return "；".join(preferences) or "默认先给结论，再补充必要细节和行动项"


def _infer_core_principles(
    *,
    profile_summary: str,
    notes_summary: str,
    approval_sensitivity: str,
) -> str:
    combined = f"{profile_summary} {notes_summary}"
    principles: list[str] = []
    for label, hints in _PRINCIPLE_HINTS:
        if any(token in combined for token in hints):
            principles.append(label)
    if "高" in approval_sensitivity and "高风险动作先确认" not in principles:
        principles.append("高风险动作先确认")
    return "；".join(principles[:4]) or "先确认目标，再给结论和可执行下一步"


def _infer_working_style(
    *,
    capability_counts: Counter[str],
    task_titles: list[str],
    notes_summary: str,
) -> str:
    dominant = _join_top(
        Counter({_CAPABILITY_LABELS.get(name, name): count for name, count in capability_counts.items()}),
        fallback="通用任务推进",
        limit=2,
    )
    if any(token in notes_summary for token in ("直接", "先结论", "简洁")):
        return f"结果导向，偏好快速推进；近期工作集中在{dominant}"
    if task_titles:
        return f"执行偏持续跟进和落地推进；近期任务主要围绕{dominant}"
    return f"工作风格稳定偏执行型；近期重点在{dominant}"


def _infer_domain_focus(capability_counts: Counter[str], task_titles: list[str]) -> str:
    labeled = Counter({_CAPABILITY_LABELS.get(name, name): count for name, count in capability_counts.items() if name})
    if labeled:
        return _join_top(labeled, fallback="通用业务协作")
    if task_titles:
        keywords = Counter()
        for title in task_titles:
            for token in re.findall(r"[\u4e00-\u9fffA-Za-z]{2,}", title):
                keywords[token] += 1
        return _join_top(keywords, fallback="通用业务协作")
    return "通用业务协作"


def _infer_decision_heuristics(
    *,
    capability_counts: Counter[str],
    preferred_output: str,
    approval_sensitivity: str,
) -> str:
    heuristics: list[str] = []
    dominant_capability = capability_counts.most_common(1)
    if dominant_capability:
        label = _CAPABILITY_LABELS.get(dominant_capability[0][0], dominant_capability[0][0])
        heuristics.append(f"先判断是否属于{label}，再决定执行路径")
    if "结论" in preferred_output:
        heuristics.append("先给结论，再补最少必要细节")
    if "高" in approval_sensitivity or "中" in approval_sensitivity:
        heuristics.append("高风险或越权动作先确认审批边界")
    return "；".join(heuristics[:3]) or "先明确目标、风险和下一步，再展开执行"


def _infer_approval_sensitivity(runs: list[dict[str, Any]]) -> str:
    with_approval = sum(1 for item in runs if str(item.get("approval_id") or "").strip())
    denied = sum(1 for item in runs if str(item.get("status") or "").strip().lower() == "cancelled" and str(item.get("approval_id") or "").strip())
    if with_approval == 0:
        return "低，通常可直接执行常规操作"
    if denied > 0:
        return "高，涉及审批的动作需要显式确认后再推进"
    if with_approval >= max(2, len(runs) // 2):
        return "中，遇到高风险或越权动作时应先确认审批"
    return "中低，常规执行可以直接推进，高风险动作再审批"


def _infer_anti_patterns(profile_summary: str, notes_summary: str, preferred_output: str) -> str:
    combined = f"{profile_summary} {notes_summary} {preferred_output}"
    patterns: list[str] = []
    if any(token in combined for token in ("简洁", "简短", "精简")):
        patterns.append("避免冗长空话")
    if any(token in combined for token in ("结论", "直接")):
        patterns.append("避免先铺垫过多再说结论")
    if any(token in combined for token in ("审批", "确认", "风险")):
        patterns.append("避免未经确认直接推进高风险动作")
    return "；".join(patterns[:3]) or "避免空泛建议、未经验证的结论和脱离任务的泛谈"


def _build_stable_instructions(profile_summary: str, notes_summary: str, preferred_output: str) -> str:
    clauses: list[str] = []
    for item in _split_clauses(profile_summary) + _split_clauses(notes_summary):
        if item and item not in clauses:
            clauses.append(item)
    if preferred_output and preferred_output not in clauses:
        clauses.append(preferred_output)
    return "；".join(clauses[:5]) or "默认中文，先给结论，再给下一步建议"


def _capability_evidence(capability_counts: Counter[str], *, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for name, count in capability_counts.most_common(limit):
        rows.append(
            {
                "kind": "capability",
                "value": name,
                "label": _CAPABILITY_LABELS.get(name, name),
                "count": count,
            }
        )
    return rows


def _title_evidence(kind: str, titles: list[str], *, limit: int = 3) -> list[dict[str, Any]]:
    rows: list[dict[str, Any]] = []
    for title in titles[:limit]:
        rows.append({"kind": kind, "title": title})
    return rows


def _summary_evidence(kind: str, text: str) -> list[dict[str, Any]]:
    value = str(text or "").strip()
    return [{"kind": kind, "text": value}] if value else []


def distill_user_profile(
    *,
    platform: str,
    user_id: str,
    since_days: int = 30,
    limit: int = 20,
) -> dict[str, Any] | None:
    user = get_user(platform=platform, user_id=user_id)
    if not user:
        return None

    since_unix = int(time.time()) - max(1, int(since_days)) * 86400
    profile = get_user_memory(platform=platform, user_id=user_id, scope="profile") or {}
    notes = get_user_memory(platform=platform, user_id=user_id, scope="notes") or {}
    tasks = list_tasks_for_user(platform=platform, user_id=user_id, since_unix=since_unix, limit=limit)
    jobs = list_background_jobs_for_user(user_id=user_id, since_unix=since_unix, limit=limit)
    runs = list_capability_runs_for_user(actor_user_id=user_id, since_unix=since_unix, limit=limit)

    capability_counts = Counter(str(item.get("capability_name") or "").strip().lower() for item in runs if str(item.get("capability_name") or "").strip())
    task_titles = [str(item.get("title") or "").strip() for item in tasks if str(item.get("title") or "").strip()]
    profile_summary = str(profile.get("summary") or "").strip()
    notes_summary = str(notes.get("summary") or "").strip()

    preferred_output = _infer_preferred_output(profile_summary, notes_summary)
    domain_focus = _infer_domain_focus(capability_counts, task_titles)
    approval_sensitivity = _infer_approval_sensitivity(runs)
    core_principles = _infer_core_principles(
        profile_summary=profile_summary,
        notes_summary=notes_summary,
        approval_sensitivity=approval_sensitivity,
    )
    working_style = _infer_working_style(
        capability_counts=capability_counts,
        task_titles=task_titles,
        notes_summary=notes_summary,
    )
    decision_heuristics = _infer_decision_heuristics(
        capability_counts=capability_counts,
        preferred_output=preferred_output,
        approval_sensitivity=approval_sensitivity,
    )
    anti_patterns = _infer_anti_patterns(profile_summary, notes_summary, preferred_output)
    stable_instructions = _build_stable_instructions(profile_summary, notes_summary, preferred_output)

    summary = (
        f"工作风格：{working_style}；"
        f"输出偏好：{preferred_output}；"
        f"领域重点：{domain_focus}；"
        f"审批敏感度：{approval_sensitivity}。"
    )

    profile_payload = {
        "core_principles": core_principles,
        "working_style": working_style,
        "preferred_output": preferred_output,
        "domain_focus": domain_focus,
        "decision_heuristics": decision_heuristics,
        "approval_sensitivity": approval_sensitivity,
        "anti_patterns": anti_patterns,
        "stable_instructions": stable_instructions,
    }
    evidence_payload = {
        "core_principles": _summary_evidence("profile_summary", profile_summary) + _summary_evidence("notes_summary", notes_summary),
        "working_style": _capability_evidence(capability_counts, limit=2) + _summary_evidence("notes_summary", notes_summary),
        "preferred_output": _summary_evidence("profile_summary", profile_summary) + _summary_evidence("notes_summary", notes_summary),
        "domain_focus": _capability_evidence(capability_counts) + _title_evidence("task", task_titles),
        "decision_heuristics": _capability_evidence(capability_counts, limit=2),
        "approval_sensitivity": [
            {
                "kind": "approval_stats",
                "approval_runs": sum(1 for item in runs if str(item.get("approval_id") or "").strip()),
                "denied_runs": sum(
                    1
                    for item in runs
                    if str(item.get("status") or "").strip().lower() == "cancelled"
                    and str(item.get("approval_id") or "").strip()
                ),
                "total_runs": len(runs),
            }
        ],
        "anti_patterns": _summary_evidence("profile_summary", profile_summary) + _summary_evidence("notes_summary", notes_summary),
        "stable_instructions": _summary_evidence("profile_summary", profile_summary) + _summary_evidence("notes_summary", notes_summary),
    }
    sources_payload = {
        "recent_capabilities": dict(capability_counts.most_common(5)),
        "recent_task_titles": task_titles[:5],
        "recent_background_job_titles": [
            str(item.get("title") or "").strip()
            for item in jobs
            if str(item.get("title") or "").strip()
        ][:5],
        "source_window_days": int(since_days),
    }
    return save_user_distilled_profile_draft(
        platform=platform,
        user_id=user_id,
        summary=summary,
        profile=profile_payload,
        evidence=evidence_payload,
        sources=sources_payload,
        updated_by="distiller",
    )


def distill_recent_users(
    *,
    platform: str = "dingtalk",
    since_days: int = 30,
    user_limit: int = 20,
    activity_limit: int = 20,
) -> list[dict[str, Any]]:
    seen_since_unix = int(time.time()) - max(1, int(since_days)) * 86400
    records: list[dict[str, Any]] = []
    for user in list_recent_users(
        platform=platform,
        seen_since_unix=seen_since_unix,
        limit=user_limit,
        include_updated_fallback=True,
    ):
        user_id = str(user.get("user_id") or "").strip()
        if not user_id:
            continue
        distilled = distill_user_profile(
            platform=platform,
            user_id=user_id,
            since_days=since_days,
            limit=activity_limit,
        )
        if distilled:
            records.append(distilled)
    return records
