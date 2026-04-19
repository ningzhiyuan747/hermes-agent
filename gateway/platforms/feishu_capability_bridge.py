from __future__ import annotations

import time
import os
import re
import signal
import uuid
from dataclasses import dataclass
from typing import Any, Callable, Dict, List, Optional

from agent.capability_execution_policy import get_capability_execution_policy
from agent.capability_status_service import (
    cancel_background_jobs as cancel_background_jobs_service,
    get_background_job_snapshot as get_background_job_snapshot_service,
    get_capability_run_snapshot as get_capability_run_snapshot_service,
)
from agent.task_panels import (
    format_background_job_snapshot,
    format_capability_run_snapshot,
)
try:
    from agent.business_db import get_channel_task, list_capability_artifacts, list_capability_runs
except Exception:  # pragma: no cover
    get_channel_task = None  # type: ignore[assignment]
    list_capability_artifacts = None  # type: ignore[assignment]
    list_capability_runs = None  # type: ignore[assignment]
from gateway.platforms.base import MessageEvent


_BID_RESEARCH_PATTERNS = (r"投标", r"招标", r"中标", r"采购公告", r"采购需求", r"招采", r"标书")
_CONTRACT_RETRIEVAL_PATTERNS = (r"中标合同", r"采购合同", r"合同附件", r"合同原件", r"合同扫描件", r"合同")
_CUSTOMER_PATTERNS = (r"客户", r"商机", r"询盘", r"线索", r"联系人", r"account", r"client", r"customer", r"lead", r"prospect")
_FOLLOWUP_PATTERNS = (
    r"跟进", r"回访", r"联系", r"回复", r"回信", r"推进", r"催单", r"邀约",
    r"follow[\s-]?up", r"outreach", r"reply", r"respond",
)
_OPS_OBJECT_PATTERNS = (
    r"hermes", r"gateway", r"bridge", r"watchdog", r"service", r"worker",
    r"feishu", r"dingtalk", r"钉钉", r"飞书", r"网关", r"桥接", r"守护", r"服务",
)
_OPS_ACTION_PATTERNS = (
    r"日志", r"报错", r"故障", r"异常", r"恢复", r"重启", r"修复", r"排查", r"检查",
    r"起不来", r"超时", r"卡住", r"log", r"logs", r"error", r"errors", r"restart",
    r"recover", r"repair", r"debug", r"status", r"health", r"timeout", r"failed",
)
_CODEX_NAME_PATTERNS = (r"\bcodex\b", r"chatgpt\s*5\.4", r"gpt\s*5\.4")
_CODEX_ACTION_PATTERNS = (
    r"让", r"叫", r"用", r"交给", r"请", r"帮我", r"去", r"继续", r"接着", r"恢复", r"resume", r"continue",
    r"修", r"改", r"实现", r"开发", r"排查", r"debug", r"fix", r"edit", r"implement", r"review", r"run",
)
_CODEX_RESUME_PATTERNS = (r"继续", r"接着", r"恢复", r"上一条", r"上一个", r"上次", r"resume", r"continue", r"续上")


def is_contract_retrieval_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not normalized:
        return False
    has_contract = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _CONTRACT_RETRIEVAL_PATTERNS)
    has_context = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _BID_RESEARCH_PATTERNS) or bool(
        re.search(r"(司羿|项目|采购|成交|中选|\d{2,4}\s*年|\d{1,2}\s*[/-]\s*\d{1,2})", normalized, flags=re.IGNORECASE)
    )
    return has_contract and has_context


def is_bid_research_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not normalized:
        return False
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _BID_RESEARCH_PATTERNS)


def is_customer_followup_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not normalized:
        return False
    has_customer = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _CUSTOMER_PATTERNS)
    has_followup = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _FOLLOWUP_PATTERNS)
    return has_customer and has_followup


def is_ops_recovery_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not normalized:
        return False
    has_object = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _OPS_OBJECT_PATTERNS)
    has_action = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _OPS_ACTION_PATTERNS)
    return has_object and has_action


def is_codex_delegate_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not normalized:
        return False
    has_name = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _CODEX_NAME_PATTERNS)
    has_action = any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _CODEX_ACTION_PATTERNS)
    return has_name and has_action


def is_codex_resume_question(question: str) -> bool:
    normalized = str(question or "").strip()
    if not is_codex_delegate_question(normalized):
        return False
    return any(re.search(pattern, normalized, flags=re.IGNORECASE) for pattern in _CODEX_RESUME_PATTERNS)


def build_codex_broker_brief(question: str) -> str:
    action = "resume" if is_codex_resume_question(question) else "start"
    return (
        "这是一个明确要求交给 Codex CLI 处理的任务。\n"
        f"如果 `codex_broker` 工具可用，第一优先动作就是调用它，action={action}；不要只口头分析。\n"
        "1. 用户指定了项目路径时，把它传给 `cwd`；没指定时，默认用 F:\\hermes-dingtalk-bridge。\n"
        "2. 如果是继续上一条 Codex 会话，优先用 `resume`，并沿用最近一次 broker 会话或其 thread_id。\n"
        "3. 启动后继续用 `status`、`read`、`wait` 跟进，直到拿到可验证结果，再回给用户。\n"
        "4. 没有 broker 证据时，不要说 Codex 已经运行、修改或完成。\n"
        f"\n当前用户原始问题：{question}"
    )


def detect_capability_route(question: str) -> Optional[Dict[str, str]]:
    normalized = str(question or "").strip()
    if not normalized:
        return None
    compact = re.sub(r"\s+", "", normalized)
    if is_contract_retrieval_question(normalized):
        policy = get_capability_execution_policy("contract_retrieval")
        return {
            "capability": "contract_retrieval",
            "mode": str(policy.get("route_mode") or "guided"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "openclaw_agent": str(policy.get("openclaw_agent") or "").strip(),
            "title": f"Contract retrieval - {normalized[:64]}",
        }
    if is_bid_research_question(normalized):
        policy = get_capability_execution_policy("bid_research")
        return {
            "capability": "bid_research",
            "mode": str(policy.get("route_mode") or "guided"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "openclaw_agent": str(policy.get("openclaw_agent") or "").strip(),
            "title": f"Bid research - {normalized[:64]}",
        }
    if is_customer_followup_question(normalized):
        policy = get_capability_execution_policy("customer_followup")
        return {
            "capability": "customer_followup",
            "mode": str(policy.get("route_mode") or "guided"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "openclaw_agent": str(policy.get("openclaw_agent") or "").strip(),
            "title": f"Customer follow-up - {normalized[:64]}",
        }
    if is_ops_recovery_question(normalized):
        policy = get_capability_execution_policy("ops_recovery")
        return {
            "capability": "ops_recovery",
            "mode": str(policy.get("route_mode") or "strict"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "openclaw_agent": str(policy.get("openclaw_agent") or "").strip(),
            "title": f"Ops recovery - {normalized[:64]}",
        }
    if re.search(r"(会议纪要|会议记录|整理会议|纪要整理|会议总结)", compact, re.IGNORECASE):
        policy = get_capability_execution_policy("meeting_minutes")
        return {
            "capability": "meeting_minutes",
            "mode": str(policy.get("route_mode") or "guided"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "title": f"Meeting minutes - {normalized[:64]}",
        }
    if re.search(r"(日报|周报|月报|进展汇总|工作汇总|任务汇总)", compact, re.IGNORECASE):
        policy = get_capability_execution_policy("report_rollup")
        return {
            "capability": "report_rollup",
            "mode": str(policy.get("route_mode") or "guided"),
            "executor": str(policy.get("executor") or "hermes"),
            "worker_kind": str(policy.get("worker_kind") or "").strip().lower(),
            "title": f"Report rollup - {normalized[:64]}",
        }
    return None


def build_contract_retrieval_brief(question: str) -> str:
    return (
        "这是“具体合同/附件取证”任务，不是普通关键词搜索。\n"
        "先自己判断最有希望的取证路径，再把下面步骤当检查清单；如果发现更快、更可靠的证据链，可以调整顺序。\n"
        "1. 先拆线索：年份、公司名、产品/方案名、项目简称、日期/批次号、采购主体。\n"
        "2. 先搜可直接访问的内部来源：本机目录、共享盘、映射盘、知识库、历史导出文件。\n"
        "3. 再搜公开来源，但要按证据链追：中标公告 -> 合同公告 -> 附件/归档 PDF。\n"
        "4. 对同一项目做别名归并，避免同名项目混淆。\n"
        "5. 输出时明确证据等级、链接/路径、判断依据、还缺什么才能拿到真正合同。\n"
        "6. 不允许臆造合同内容；找不到就明确卡点。\n"
        f"\n当前用户原始问题：{question}"
    )


def build_bid_research_brief(question: str) -> str:
    return (
        "这是商务投标/招标检索任务，不要退化成只打一个关键词去搜索。\n"
        "先自己理解任务，再把下面流程当脚手架：\n"
        "1. 拆出产品/方案别名、采购主体、地区、时间窗、预算、场景。\n"
        "2. 生成检索矩阵，而不是单一关键词。\n"
        "3. 分层搜索来源：政府采购网、公共资源交易中心、医院/高校/机构采购页、历史中标。\n"
        "4. 先判断业务匹配，再收录；不匹配的结果不要硬塞。\n"
        "5. 结果要去重、打分、排序，并给出下一步建议。\n"
        f"\n当前用户原始问题：{question}"
    )


def build_customer_followup_brief(question: str) -> str:
    return (
        "这是客户跟进任务，不要只给泛泛建议。\n"
        "先抽取客户现状、阻塞点、时间线和下一步动作，再输出可执行的跟进建议或草稿。\n"
        "如果关键信息不足，要明确说清缺口，而不是假设客户已经承诺。\n"
        f"\n当前用户原始问题：{question}"
    )


def build_ops_recovery_brief(question: str) -> str:
    return (
        "这是运维恢复任务，先看证据，再做最小可验证动作。\n"
        "优先说明现象、日志/状态证据、恢复动作、验证结果和剩余风险。\n"
        "没有验证就不要宣称已经恢复。\n"
        f"\n当前用户原始问题：{question}"
    )


def build_capability_route_brief(route: Dict[str, str], question: str) -> str:
    capability = str(route.get("capability") or "").strip()
    mode = str(route.get("mode") or "guided").strip().lower()
    common = (
        f"自动识别业务能力：{capability}（{mode}）\n"
        "先自己思考最有效的完成路径，再把 capability 当脚手架推进；不要退化成僵硬脚本。\n"
        "输出中至少体现：目标、关键步骤、证据来源、结果、下一步。"
    )
    if capability == "bid_research":
        return common + "\n优先按投标检索工作流执行，不要退化成单关键词搜索。"
    if capability == "contract_retrieval":
        return common + "\n优先按合同取证工作流执行，不要把中标公告误当合同原件。"
    if capability == "customer_followup":
        return common + "\n优先提炼客户信号、阻塞点和推荐跟进动作，不要输出空泛销售话术。"
    if capability == "ops_recovery":
        return common + "\n优先按运维排障和恢复流程执行，没有验证证据就不要宣称已修复。"
    return common + f"\n当前用户原始问题：{question}"


def _new_trace_id() -> str:
    return "trace-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


@dataclass(frozen=True)
class FeishuCapabilityBridgeConfig:
    offload_enabled: bool
    offload_capabilities: frozenset[str]


class FeishuCapabilityBridge:
    def __init__(self, config: FeishuCapabilityBridgeConfig | None = None) -> None:
        self.config = config or FeishuCapabilityBridgeConfig(
            offload_enabled=False,
            offload_capabilities=frozenset(),
        )

    @staticmethod
    def _scope_fields(
        *,
        platform: str,
        chat_id: str,
        thread_id: str,
        actor_user_id: str,
        session_id: str = "",
    ) -> Dict[str, str]:
        platform_value = str(platform or "").strip().lower()
        chat_value = str(chat_id or "").strip()
        thread_value = str(thread_id or "").strip()
        actor_value = str(actor_user_id or "").strip()
        session_value = str(session_id or "").strip()
        task_scope_key = ""
        if platform_value and chat_value:
            task_scope_key = f"{platform_value}:chat:{chat_value}"
            if thread_value:
                task_scope_key += f":thread:{thread_value}"
        person_memory_key = f"{platform_value}:user:{actor_value}" if platform_value and actor_value else ""
        if platform_value == "dingtalk" and chat_value:
            conversation_role = "task_group"
        elif chat_value:
            conversation_role = "chat_surface"
        elif session_value:
            conversation_role = "task_unit"
        else:
            conversation_role = "system"
        return {
            "task_scope_key": task_scope_key,
            "person_memory_key": person_memory_key,
            "conversation_role": conversation_role,
        }

    def build_effective_question(self, question: str) -> str:
        route = detect_capability_route(question)
        parts: List[str] = []
        if is_codex_delegate_question(question):
            parts.append(build_codex_broker_brief(question))
        if route:
            parts.append(build_capability_route_brief(route, question))
        if is_contract_retrieval_question(question):
            parts.append(build_contract_retrieval_brief(question))
        elif is_bid_research_question(question):
            parts.append(build_bid_research_brief(question))
        elif is_customer_followup_question(question):
            parts.append(build_customer_followup_brief(question))
        elif is_ops_recovery_question(question):
            parts.append(build_ops_recovery_brief(question))
        if not parts:
            return question
        parts.append(f"用户问题：{question}")
        return "\n\n".join(parts)

    def route_prefers_openclaw(self, route: Optional[Dict[str, str]]) -> bool:
        if not self.config.offload_enabled or not route:
            return False
        capability = str(route.get("capability") or "").strip().lower()
        executor = str(route.get("executor") or get_capability_execution_policy(capability).get("executor") or "hermes").strip().lower()
        return executor == "openclaw" and capability in self.config.offload_capabilities

    def create_capability_run_for_route(
        self,
        event: MessageEvent,
        route: Dict[str, str],
        *,
        session_key_builder: Callable[[MessageEvent], str],
        create_capability_run_func: Optional[Callable[..., Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        if create_capability_run_func is None:
            return None
        session_key = session_key_builder(event)
        origin = {
            "platform": "feishu",
            "chat_id": str(event.source.chat_id or "").strip(),
            "chat_name": str(event.source.chat_name or "").strip(),
            "chat_type": str(event.source.chat_type or "").strip(),
            "thread_id": str(event.source.thread_id or "").strip(),
        }
        actor_user_id = str(event.source.user_id or getattr(event.source, "user_id_alt", "") or "").strip()
        scopes = self._scope_fields(
            platform="feishu",
            chat_id=origin["chat_id"],
            thread_id=origin["thread_id"],
            actor_user_id=actor_user_id,
            session_id=session_key,
        )
        try:
            return create_capability_run_func(
                capability=str(route.get("capability") or "").strip(),
                title=str(route.get("title") or (event.text or "")[:80]).strip(),
                goal=str(event.text or "").strip(),
                origin=origin,
                actor_user_id=actor_user_id,
                session_id=session_key,
                priority="normal",
                input_data={
                    "message_id": str(event.message_id or "").strip(),
                    "worker_kind": str(route.get("worker_kind") or "").strip().lower(),
                    "executor": str(route.get("executor") or "hermes").strip().lower(),
                    **scopes,
                },
            )
        except Exception as exc:
            return {"error": str(exc)}

    def create_background_job_for_route(
        self,
        event: MessageEvent,
        route: Dict[str, str],
        *,
        run_record: Optional[Dict[str, Any]],
        session_key_builder: Callable[[MessageEvent], str],
        create_job_func: Optional[Callable[..., Dict[str, Any]]],
        update_capability_run_func: Optional[Callable[..., Dict[str, Any]]],
    ) -> Optional[Dict[str, Any]]:
        if create_job_func is None:
            return None
        capability = str(route.get("capability") or "").strip().lower()
        session_key = session_key_builder(event)
        prompt = self.build_effective_question(event.text or "")
        origin = {
            "platform": "feishu",
            "chat_id": str(event.source.chat_id or "").strip(),
            "chat_name": str(event.source.chat_name or "").strip(),
            "chat_type": str(event.source.chat_type or "").strip(),
            "thread_id": str(event.source.thread_id or "").strip(),
        }
        actor_user_id = str(event.source.user_id or getattr(event.source, "user_id_alt", "") or "").strip()
        scopes = self._scope_fields(
            platform="feishu",
            chat_id=origin["chat_id"],
            thread_id=origin["thread_id"],
            actor_user_id=actor_user_id,
            session_id=session_key,
        )
        record = create_job_func(
            title=str(route.get("title") or ((event.text or "")[:80])).strip(),
            prompt=prompt,
            origin=origin,
            session_id=session_key,
            user_id=actor_user_id,
            priority="normal",
            trace_id=str((run_record or {}).get("trace_id") or "").strip() or _new_trace_id(),
            executor=str(route.get("executor") or get_capability_execution_policy(capability).get("executor") or "hermes").strip().lower(),
            tags=[
                "executor:openclaw",
                "source:feishu",
                f"capability:{capability}",
                f"route_mode:{str(route.get('mode') or 'guided').strip().lower()}",
            ]
            + (
                [f"worker_kind:{str(route.get('worker_kind') or '').strip().lower()}"]
                if str(route.get("worker_kind") or "").strip()
                else []
            )
            + (
                [f"openclaw_agent:{str(route.get('openclaw_agent') or '').strip()}"]
                if str(route.get("openclaw_agent") or "").strip()
                else []
            )
            + (
                [f"task_scope:{str(scopes.get('task_scope_key') or '').strip()}"]
                if str(scopes.get("task_scope_key") or "").strip()
                else []
            )
            + (
                [f"person_memory:{str(scopes.get('person_memory_key') or '').strip()}"]
                if str(scopes.get("person_memory_key") or "").strip()
                else []
            )
            + (
                [f"conversation_role:{str(scopes.get('conversation_role') or '').strip()}"]
                if str(scopes.get("conversation_role") or "").strip()
                else []
            )
            + (
                [f"capability_run:{str((run_record or {}).get('run_id') or '').strip()}"]
                if str((run_record or {}).get("run_id") or "").strip()
                else []
            ),
        )
        run_id = str((run_record or {}).get("run_id") or "").strip()
        if run_id and update_capability_run_func is not None:
            try:
                update_capability_run_func(
                    run_id,
                    background_job_id=str(record.get("job_id") or "").strip(),
                    current_focus="Queued for OpenClaw research worker.",
                    next_step="Background worker will dispatch this run to OpenClaw and auto-deliver the result back to Feishu.",
                    output={
                        "offloaded_to": "openclaw",
                        "background_job_id": str(record.get("job_id") or "").strip(),
                        "route_mode": str(route.get("mode") or "guided").strip().lower(),
                        "worker_kind": str(route.get("worker_kind") or "").strip().lower(),
                        **scopes,
                    },
                )
            except Exception:
                pass
        return record

    def get_background_job_snapshot(
        self,
        session_key: str,
        *,
        list_jobs_func: Optional[Callable[..., List[Dict[str, Any]]]],
        active_only: bool = True,
        global_fallback: bool = False,
        platform: str = "feishu",
        chat_id: str = "",
        thread_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        if list_jobs_func is None:
            return None
        return get_background_job_snapshot_service(
            session_id=session_key,
            active_only=active_only,
            global_fallback=global_fallback,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
        )

    def get_capability_run_snapshot(
        self,
        session_key: str,
        *,
        active_only: bool = True,
        global_fallback: bool = False,
        platform: str = "feishu",
        chat_id: str = "",
        thread_id: str = "",
    ) -> Optional[Dict[str, Any]]:
        return get_capability_run_snapshot_service(
            session_id=session_key,
            active_only=active_only,
            global_fallback=global_fallback,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
        )

    @staticmethod
    def format_capability_run_snapshot(run: Dict[str, Any]) -> str:
        return format_capability_run_snapshot(run, fullwidth_colon=True)

    @staticmethod
    def format_background_job_snapshot(job: Dict[str, Any]) -> str:
        return format_background_job_snapshot(job, fullwidth_colon=True)

    def cancel_background_job_for_session(
        self,
        session_key: str,
        *,
        list_jobs_func: Optional[Callable[..., List[Dict[str, Any]]]],
        update_job_func: Optional[Callable[..., Dict[str, Any]]],
        append_job_event_func: Optional[Callable[..., Dict[str, Any]]],
        update_capability_run_func: Optional[Callable[..., Dict[str, Any]]],
        global_fallback: bool = False,
        platform: str = "feishu",
        chat_id: str = "",
        thread_id: str = "",
    ) -> Dict[str, Any]:
        if list_jobs_func is None or update_job_func is None or append_job_event_func is None or update_capability_run_func is None:
            return {"ok": False, "cancelled": 0}
        return cancel_background_jobs_service(
            session_id=session_key,
            global_fallback=global_fallback,
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            cancel_label="Feishu control command",
            cancel_source="feishu_control",
        )
