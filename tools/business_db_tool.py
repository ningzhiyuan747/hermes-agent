from __future__ import annotations

import json
from typing import Any, Dict

from agent.business_db import (
    add_capability_artifact,
    add_capability_step,
    bind_channel_task,
    create_task,
    create_capability,
    create_capability_run,
    create_table_sync_rule,
    decide_approval,
    evaluate_capability_access,
    get_capability_run,
    get_channel_task,
    get_task,
    get_task_memory,
    get_user_memory,
    get_table_sync_rule,
    list_capabilities,
    list_capability_artifacts,
    list_capability_runs,
    list_capability_steps,
    list_approvals,
    list_channels,
    list_table_sync_rules,
    list_tasks,
    list_users,
    status_snapshot,
    unbind_channel_task,
    update_capability,
    update_capability_run,
    update_table_sync_rule,
    upsert_task_memory,
    upsert_channel,
    upsert_user_memory,
    upsert_user,
    write_audit_log,
)


def _origin_from_session() -> Dict[str, str]:
    try:
        from gateway.session_context import get_session_env
    except Exception:
        return {}
    origin = {
        "platform": get_session_env("HERMES_SESSION_PLATFORM") or "",
        "chat_id": get_session_env("HERMES_SESSION_CHAT_ID") or "",
        "chat_name": get_session_env("HERMES_SESSION_CHAT_NAME") or "",
        "chat_type": get_session_env("HERMES_SESSION_CHAT_TYPE") or "",
        "thread_id": get_session_env("HERMES_SESSION_THREAD_ID") or "",
    }
    return {k: v for k, v in origin.items() if v}


def _session_value(key: str) -> str:
    try:
        from gateway.session_context import get_session_env
        return get_session_env(key) or ""
    except Exception:
        return ""


def business_db(args: Dict[str, Any], **kwargs) -> str:
    action = str(args.get("action") or "status").strip().lower()

    if action == "status":
        return json.dumps({"ok": True, "database": status_snapshot()}, ensure_ascii=False)

    if action == "list_capabilities":
        return json.dumps(
            {
                "ok": True,
                "capabilities": list_capabilities(
                    enabled_only=bool(args.get("enabled_only", True)),
                    limit=int(args.get("limit") or 50),
                ),
            },
            ensure_ascii=False,
        )

    if action == "create_task":
        title = str(args.get("title") or args.get("name") or args.get("goal") or "").strip()
        goal = str(args.get("goal") or title).strip()
        if not title:
            return json.dumps({"error": "title is required"}, ensure_ascii=False)
        record = create_task(
            title=title,
            goal=goal,
            owner_user_id=str(args.get("owner_user_id") or args.get("user_id") or _session_value("HERMES_SESSION_USER_ID") or ""),
            source_platform=str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or ""),
            source_chat_id=str(args.get("chat_id") or _session_value("HERMES_SESSION_CHAT_ID") or ""),
            source_thread_id=str(args.get("thread_id") or _session_value("HERMES_SESSION_THREAD_ID") or ""),
            source_session_id=str(args.get("session_id") or _session_value("HERMES_SESSION_ID") or _session_value("HERMES_SESSION_KEY") or ""),
            metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
            participant_user_ids=args.get("participant_user_ids") if isinstance(args.get("participant_user_ids"), list) else [],
        )
        return json.dumps({"ok": True, "task": record}, ensure_ascii=False)

    if action == "list_tasks":
        return json.dumps(
            {"ok": True, "tasks": list_tasks(status=str(args.get("status") or ""), limit=int(args.get("limit") or 20))},
            ensure_ascii=False,
        )

    if action == "get_task":
        task_id = str(args.get("task_id") or "").strip()
        record = get_task(task_id)
        if not record:
            return json.dumps({"error": f"task not found: {task_id}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "task": record}, ensure_ascii=False)

    if action == "bind_channel_task":
        task_id = str(args.get("task_id") or "").strip()
        platform = str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or "").strip()
        chat_id = str(args.get("chat_id") or _session_value("HERMES_SESSION_CHAT_ID") or "").strip()
        thread_id = str(args.get("thread_id") or _session_value("HERMES_SESSION_THREAD_ID") or "").strip()
        if not task_id or not platform or not chat_id:
            return json.dumps({"error": "task_id, platform, and chat_id are required"}, ensure_ascii=False)
        try:
            record = bind_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id, task_id=task_id)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "channel": record}, ensure_ascii=False)

    if action == "unbind_channel_task":
        platform = str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or "").strip()
        chat_id = str(args.get("chat_id") or _session_value("HERMES_SESSION_CHAT_ID") or "").strip()
        thread_id = str(args.get("thread_id") or _session_value("HERMES_SESSION_THREAD_ID") or "").strip()
        if not platform or not chat_id:
            return json.dumps({"error": "platform and chat_id are required"}, ensure_ascii=False)
        try:
            record = unbind_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id)
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "channel": record}, ensure_ascii=False)

    if action == "get_channel_task":
        platform = str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or "").strip()
        chat_id = str(args.get("chat_id") or _session_value("HERMES_SESSION_CHAT_ID") or "").strip()
        thread_id = str(args.get("thread_id") or _session_value("HERMES_SESSION_THREAD_ID") or "").strip()
        record = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id) if platform and chat_id else None
        if not record:
            return json.dumps({"error": "channel task not found"}, ensure_ascii=False)
        return json.dumps({"ok": True, "channel_task": record}, ensure_ascii=False)

    if action == "upsert_user_memory":
        platform = str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or "").strip()
        user_id = str(args.get("user_id") or _session_value("HERMES_SESSION_USER_ID") or "").strip()
        scope = str(args.get("scope") or "profile").strip()
        if not platform or not user_id:
            return json.dumps({"error": "platform and user_id are required"}, ensure_ascii=False)
        record = upsert_user_memory(
            platform=platform,
            user_id=user_id,
            scope=scope,
            summary=str(args.get("summary") or ""),
            memory=args.get("memory") if isinstance(args.get("memory"), dict) else {},
        )
        return json.dumps({"ok": True, "user_memory": record}, ensure_ascii=False)

    if action == "get_user_memory":
        platform = str(args.get("platform") or _session_value("HERMES_SESSION_PLATFORM") or "").strip()
        user_id = str(args.get("user_id") or _session_value("HERMES_SESSION_USER_ID") or "").strip()
        scope = str(args.get("scope") or "profile").strip()
        record = get_user_memory(platform=platform, user_id=user_id, scope=scope)
        if not record:
            return json.dumps({"error": "user memory not found"}, ensure_ascii=False)
        return json.dumps({"ok": True, "user_memory": record}, ensure_ascii=False)

    if action == "upsert_task_memory":
        task_id = str(args.get("task_id") or "").strip()
        if not task_id:
            return json.dumps({"error": "task_id is required"}, ensure_ascii=False)
        try:
            record = upsert_task_memory(
                task_id=task_id,
                scope=str(args.get("scope") or "shared").strip(),
                summary=str(args.get("summary") or ""),
                memory=args.get("memory") if isinstance(args.get("memory"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "task_memory": record}, ensure_ascii=False)

    if action == "get_task_memory":
        task_id = str(args.get("task_id") or "").strip()
        scope = str(args.get("scope") or "shared").strip()
        record = get_task_memory(task_id=task_id, scope=scope) if task_id else None
        if not record:
            return json.dumps({"error": "task memory not found"}, ensure_ascii=False)
        return json.dumps({"ok": True, "task_memory": record}, ensure_ascii=False)

    if action == "create_capability":
        name = str(args.get("name") or args.get("capability") or "").strip()
        if not name:
            return json.dumps({"error": "name is required"}, ensure_ascii=False)
        try:
            record = create_capability(
                name=name,
                description=str(args.get("description") or ""),
                category=str(args.get("category") or "custom"),
                risk_level=str(args.get("risk_level") or "medium"),
                default_approval_required=bool(args.get("default_approval_required", True)),
                enabled=bool(args.get("enabled", True)),
                config=args.get("config") if isinstance(args.get("config"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        write_audit_log(
            action="create_capability",
            target_type="capability",
            target_id=record.get("capability_id", ""),
            summary=record.get("name", ""),
            payload=record,
        )
        return json.dumps({"ok": True, "capability": record}, ensure_ascii=False)

    if action == "update_capability":
        identifier = str(args.get("capability") or args.get("capability_id") or args.get("name") or "").strip()
        if not identifier:
            return json.dumps({"error": "capability is required"}, ensure_ascii=False)
        record = update_capability(
            identifier,
            description=args.get("description"),
            category=args.get("category"),
            risk_level=args.get("risk_level"),
            default_approval_required=args.get("default_approval_required"),
            enabled=args.get("enabled"),
            config=args.get("config") if isinstance(args.get("config"), dict) else None,
        )
        if not record:
            return json.dumps({"error": f"capability not found: {identifier}"}, ensure_ascii=False)
        write_audit_log(
            action="update_capability",
            target_type="capability",
            target_id=record.get("capability_id", ""),
            summary=record.get("name", ""),
            payload=record,
        )
        return json.dumps({"ok": True, "capability": record}, ensure_ascii=False)

    if action == "create_capability_run":
        capability = str(args.get("capability") or "").strip()
        goal = str(args.get("goal") or "").strip()
        if not capability or not goal:
            return json.dumps({"error": "capability and goal are required"}, ensure_ascii=False)
        try:
            record = create_capability_run(
                capability=capability,
                title=str(args.get("title") or goal[:80]),
                goal=goal,
                origin=args.get("origin") if isinstance(args.get("origin"), dict) else _origin_from_session(),
                actor_user_id=str(args.get("actor_user_id") or args.get("user_id") or _session_value("HERMES_SESSION_USER_ID") or ""),
                session_id=str(args.get("session_id") or _session_value("HERMES_SESSION_ID") or _session_value("HERMES_SESSION_KEY") or ""),
                priority=str(args.get("priority") or "normal"),
                input_data=args.get("input") if isinstance(args.get("input"), dict) else {},
                background_job_id=str(args.get("background_job_id") or ""),
                task_id=str(args.get("task_id") or ""),
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        write_audit_log(
            action="create_capability_run",
            actor_user_id=record.get("actor_user_id", ""),
            platform=str((record.get("origin") or {}).get("platform") or ""),
            chat_id=str((record.get("origin") or {}).get("chat_id") or ""),
            target_type="capability_run",
            target_id=record.get("run_id", ""),
            summary=record.get("title", ""),
            payload=record,
        )
        response = {"ok": True, "run": record}
        if record.get("status") == "pending_approval":
            response["text"] = (
                f"Capability run created and is waiting for approval: {record.get('approval_id') or '-'} "
                f"for {record.get('capability_name') or capability}."
            )
        return json.dumps(response, ensure_ascii=False)

    if action == "evaluate_capability_access":
        decision = evaluate_capability_access(
            capability=str(args.get("capability") or ""),
            actor_user_id=str(args.get("actor_user_id") or args.get("user_id") or ""),
            platform=str(args.get("platform") or ""),
            chat_id=str(args.get("chat_id") or ""),
            thread_id=str(args.get("thread_id") or ""),
            action=str(args.get("access_action") or "run"),
        )
        decision.pop("capability", None)
        decision.pop("user", None)
        decision.pop("channel", None)
        return json.dumps({"ok": True, "decision": decision}, ensure_ascii=False)

    if action == "list_capability_runs":
        return json.dumps(
            {
                "ok": True,
                "runs": list_capability_runs(
                    status=str(args.get("status") or ""),
                    capability=str(args.get("capability") or ""),
                    task_id=str(args.get("task_id") or ""),
                    limit=int(args.get("limit") or 20),
                ),
            },
            ensure_ascii=False,
        )

    if action == "get_capability_run":
        run_id = str(args.get("run_id") or "").strip()
        record = get_capability_run(run_id)
        if not record:
            return json.dumps({"error": f"capability run not found: {run_id}"}, ensure_ascii=False)
        return json.dumps(
            {
                "ok": True,
                "run": record,
                "steps": list_capability_steps(run_id, limit=int(args.get("limit") or 50)),
                "artifacts": list_capability_artifacts(run_id, limit=int(args.get("limit") or 50)),
            },
            ensure_ascii=False,
        )

    if action == "update_capability_run":
        run_id = str(args.get("run_id") or "").strip()
        record = update_capability_run(
            run_id,
            status=args.get("status"),
            priority=args.get("priority"),
            background_job_id=args.get("background_job_id"),
            approval_id=args.get("approval_id"),
            current_focus=args.get("current_focus"),
            next_step=args.get("next_step"),
            blocker=args.get("blocker"),
            result=args.get("result"),
            output=args.get("output") if isinstance(args.get("output"), dict) else None,
        )
        if not record:
            return json.dumps({"error": f"capability run not found: {run_id}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "run": record}, ensure_ascii=False)

    if action == "add_capability_step":
        run_id = str(args.get("run_id") or "").strip()
        try:
            record = add_capability_step(
                run_id=run_id,
                name=str(args.get("name") or args.get("current_focus") or "step"),
                status=str(args.get("status") or "pending"),
                step_order=int(args.get("step_order") or 0),
                current_focus=str(args.get("current_focus") or ""),
                evidence=str(args.get("evidence") or ""),
                blocker=str(args.get("blocker") or ""),
                payload=args.get("payload") if isinstance(args.get("payload"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "step": record}, ensure_ascii=False)

    if action == "add_capability_artifact":
        run_id = str(args.get("run_id") or "").strip()
        try:
            record = add_capability_artifact(
                run_id=run_id,
                kind=str(args.get("kind") or ""),
                label=str(args.get("label") or ""),
                path_or_ref=str(args.get("path_or_ref") or ""),
                summary=str(args.get("summary") or ""),
                step_id=str(args.get("step_id") or ""),
                metadata=args.get("metadata") if isinstance(args.get("metadata"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "artifact": record}, ensure_ascii=False)

    if action == "create_table_sync_rule":
        name = str(args.get("name") or "").strip()
        source_ref = str(args.get("source_ref") or "").strip()
        target_ref = str(args.get("target_ref") or "").strip()
        if not name or not source_ref or not target_ref:
            return json.dumps({"error": "name, source_ref, and target_ref are required"}, ensure_ascii=False)
        record = create_table_sync_rule(
            name=name,
            source_ref=source_ref,
            target_ref=target_ref,
            match_keys=args.get("match_keys") if isinstance(args.get("match_keys"), list) else [],
            field_map=args.get("field_map") if isinstance(args.get("field_map"), dict) else {},
            write_policy=str(args.get("write_policy") or "fill_blank"),
            approval_required=bool(args.get("approval_required", True)),
            enabled=bool(args.get("enabled", True)),
            owner_user_id=str(args.get("owner_user_id") or ""),
        )
        write_audit_log(
            action="create_table_sync_rule",
            target_type="table_sync_rule",
            target_id=record["rule_id"],
            summary=record["name"],
            payload=record,
        )
        return json.dumps({"ok": True, "rule": record}, ensure_ascii=False)

    if action == "list_table_sync_rules":
        return json.dumps(
            {
                "ok": True,
                "rules": list_table_sync_rules(
                    enabled_only=bool(args.get("enabled_only", False)),
                    limit=int(args.get("limit") or 20),
                ),
            },
            ensure_ascii=False,
        )

    if action == "get_table_sync_rule":
        rule_id = str(args.get("rule_id") or "").strip()
        record = get_table_sync_rule(rule_id)
        if not record:
            return json.dumps({"error": f"table sync rule not found: {rule_id}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "rule": record}, ensure_ascii=False)

    if action == "update_table_sync_rule":
        rule_id = str(args.get("rule_id") or "").strip()
        record = update_table_sync_rule(
            rule_id,
            name=args.get("name"),
            source_ref=args.get("source_ref"),
            target_ref=args.get("target_ref"),
            match_keys=args.get("match_keys") if isinstance(args.get("match_keys"), list) else None,
            field_map=args.get("field_map") if isinstance(args.get("field_map"), dict) else None,
            write_policy=args.get("write_policy"),
            approval_required=args.get("approval_required"),
            enabled=args.get("enabled"),
            owner_user_id=args.get("owner_user_id"),
        )
        if not record:
            return json.dumps({"error": f"table sync rule not found: {rule_id}"}, ensure_ascii=False)
        write_audit_log(
            action="update_table_sync_rule",
            target_type="table_sync_rule",
            target_id=record["rule_id"],
            summary=record["name"],
            payload=record,
        )
        return json.dumps({"ok": True, "rule": record}, ensure_ascii=False)

    if action == "record_audit":
        write_audit_log(
            action=str(args.get("audit_action") or args.get("summary") or "manual_audit"),
            actor_user_id=str(args.get("actor_user_id") or ""),
            platform=str(args.get("platform") or ""),
            chat_id=str(args.get("chat_id") or ""),
            target_type=str(args.get("target_type") or ""),
            target_id=str(args.get("target_id") or ""),
            summary=str(args.get("summary") or ""),
            payload=args.get("payload") if isinstance(args.get("payload"), dict) else {},
        )
        return json.dumps({"ok": True}, ensure_ascii=False)

    if action == "upsert_user":
        try:
            record = upsert_user(
                platform=str(args.get("platform") or ""),
                user_id=str(args.get("user_id") or args.get("actor_user_id") or ""),
                display_name=str(args.get("display_name") or ""),
                role=str(args.get("role") or "user"),
                permissions=args.get("permissions") if isinstance(args.get("permissions"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "user": record}, ensure_ascii=False)

    if action == "list_users":
        return json.dumps(
            {
                "ok": True,
                "users": list_users(limit=int(args.get("limit") or 50), role=str(args.get("role") or "")),
            },
            ensure_ascii=False,
        )

    if action == "upsert_channel":
        try:
            record = upsert_channel(
                platform=str(args.get("platform") or ""),
                chat_id=str(args.get("chat_id") or ""),
                thread_id=str(args.get("thread_id") or ""),
                chat_name=str(args.get("chat_name") or ""),
                chat_type=str(args.get("chat_type") or ""),
                worker_role=str(args.get("worker_role") or ""),
                allow_free_chat=bool(args.get("allow_free_chat", False)),
                policy=args.get("policy") if isinstance(args.get("policy"), dict) else {},
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        return json.dumps({"ok": True, "channel": record}, ensure_ascii=False)

    if action == "list_channels":
        return json.dumps(
            {
                "ok": True,
                "channels": list_channels(limit=int(args.get("limit") or 50), platform=str(args.get("platform") or "")),
            },
            ensure_ascii=False,
        )

    if action == "list_approvals":
        return json.dumps(
            {
                "ok": True,
                "approvals": list_approvals(status=str(args.get("status") or "pending"), limit=int(args.get("limit") or 20)),
            },
            ensure_ascii=False,
        )

    if action == "decide_approval":
        try:
            record = decide_approval(
                str(args.get("approval_id") or ""),
                status=str(args.get("status") or ""),
                approved_by=str(args.get("approved_by") or args.get("actor_user_id") or ""),
            )
        except Exception as exc:
            return json.dumps({"error": str(exc)}, ensure_ascii=False)
        if not record:
            return json.dumps({"error": f"approval not found: {args.get('approval_id')}"}, ensure_ascii=False)
        return json.dumps({"ok": True, "approval": record}, ensure_ascii=False)

    return json.dumps({"error": f"unknown business_db action: {action}"}, ensure_ascii=False)


BUSINESS_DB_SCHEMA = {
    "name": "business_db",
    "description": (
        "Read and write Hermes' structured business ledger. Use it for durable state "
        "that must be exact and auditable: table sync rules, approval/audit records, "
        "background job database status, and future DingTalk spreadsheet automation metadata. "
        "When creating capability runs during a live chat session, current platform/chat/user "
        "context is filled automatically if not provided."
    ),
    "parameters": {
        "type": "object",
        "properties": {
            "action": {
                "type": "string",
                "enum": [
                    "status",
                    "list_capabilities",
                    "create_capability",
                    "update_capability",
                    "create_capability_run",
                    "list_capability_runs",
                    "get_capability_run",
                    "update_capability_run",
                    "evaluate_capability_access",
                    "add_capability_step",
                    "add_capability_artifact",
                    "create_table_sync_rule",
                    "list_table_sync_rules",
                    "get_table_sync_rule",
                    "update_table_sync_rule",
                    "record_audit",
                    "upsert_user",
                    "list_users",
                    "upsert_channel",
                    "list_channels",
                    "list_approvals",
                    "decide_approval",
                ],
            },
            "rule_id": {"type": "string"},
            "capability_id": {"type": "string"},
            "run_id": {"type": "string"},
            "step_id": {"type": "string"},
            "capability": {"type": "string", "description": "Capability name or id, e.g. table_sync, bid_research, knowledge_ingest."},
            "goal": {"type": "string"},
            "name": {"type": "string"},
            "description": {"type": "string"},
            "category": {"type": "string"},
            "risk_level": {"type": "string", "description": "low, medium, high, or critical"},
            "default_approval_required": {"type": "boolean"},
            "title": {"type": "string"},
            "source_ref": {"type": "string"},
            "target_ref": {"type": "string"},
            "match_keys": {"type": "array", "items": {"type": "string"}},
            "field_map": {"type": "object"},
            "write_policy": {"type": "string", "description": "fill_blank, overwrite, append_only, or approval_only"},
            "approval_required": {"type": "boolean"},
            "enabled": {"type": "boolean"},
            "enabled_only": {"type": "boolean"},
            "owner_user_id": {"type": "string"},
            "limit": {"type": "integer"},
            "priority": {"type": "string"},
            "background_job_id": {"type": "string"},
            "approval_id": {"type": "string"},
            "current_focus": {"type": "string"},
            "next_step": {"type": "string"},
            "blocker": {"type": "string"},
            "result": {"type": "string"},
            "evidence": {"type": "string"},
            "step_order": {"type": "integer"},
            "kind": {"type": "string"},
            "label": {"type": "string"},
            "path_or_ref": {"type": "string"},
            "input": {"type": "object"},
            "output": {"type": "object"},
            "origin": {"type": "object"},
            "metadata": {"type": "object"},
            "config": {"type": "object"},
            "audit_action": {"type": "string"},
            "actor_user_id": {"type": "string"},
            "user_id": {"type": "string"},
            "display_name": {"type": "string"},
            "role": {"type": "string", "description": "owner, admin, manager, trusted, user, viewer, or blocked"},
            "platform": {"type": "string"},
            "chat_id": {"type": "string"},
            "thread_id": {"type": "string"},
            "chat_name": {"type": "string"},
            "chat_type": {"type": "string"},
            "worker_role": {"type": "string"},
            "allow_free_chat": {"type": "boolean"},
            "policy": {"type": "object"},
            "permissions": {"type": "object"},
            "access_action": {"type": "string"},
            "approved_by": {"type": "string"},
            "target_type": {"type": "string"},
            "target_id": {"type": "string"},
            "summary": {"type": "string"},
            "payload": {"type": "object"},
        },
        "required": ["action"],
    },
}


from tools.registry import registry

registry.register(
    name="business_db",
    toolset="business",
    schema=BUSINESS_DB_SCHEMA,
    handler=lambda args, **kw: business_db(args, **kw),
    emoji="DB",
)
