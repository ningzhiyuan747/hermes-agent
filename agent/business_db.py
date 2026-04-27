from __future__ import annotations

import json
import os
import re
import sqlite3
import time
import uuid
from pathlib import Path
from typing import Any, Dict, Iterable, Optional

from agent.capability_execution_policy import get_capability_execution_policy
from gateway.status import acquire_scoped_lock, release_scoped_lock
from hermes_cli.env_loader import load_hermes_dotenv
from hermes_constants import get_hermes_home, get_hermes_memory_home


load_hermes_dotenv(
    hermes_home=get_hermes_home(),
    project_env=Path(__file__).resolve().parents[1] / ".env",
)


SCHEMA_VERSION = 6

RISK_RANK = {"none": 0, "low": 1, "medium": 2, "high": 3, "critical": 4}
ROLE_MAX_AUTO_RISK = {
    "system": 4,
    "owner": 4,
    "admin": 4,
    "manager": 2,
    "trusted": 2,
    "user": 1,
    "viewer": 0,
    "blocked": -1,
}
ACTIVE_RUN_STATUSES = {"queued", "running", "paused", "blocked", "pending_approval"}
TERMINAL_RUN_STATUSES = {"completed", "failed", "cancelled"}
TERMINAL_TASK_STATUSES = {"completed", "failed", "cancelled"}
PRIVATE_CHAT_TYPES = {
    "dm",
    "direct",
    "direct_message",
    "private",
    "im",
    "1:1",
    "1",
    "p2p",
    "single",
    "singlechat",
}
CONTROL_SURFACE_APPROVAL_BYPASS_PLATFORMS = {"feishu", "weixin"}
ALLOWED_USER_MEMORY_SCOPES = {"profile", "notes", "distilled"}
ALLOWED_TASK_MEMORY_SCOPES = {"shared"}


def _business_approval_mode() -> str:
    raw = str(os.getenv("HERMES_BUSINESS_APPROVAL_MODE", "owner_control_surface") or "").strip().lower()
    if raw in {"off", "disabled", "disable", "false", "0", "no"}:
        return "off"
    if raw in {"enforce", "strict", "on", "enabled", "true", "1", "yes"}:
        return "enforce"
    return "owner_control_surface"


def _feishu_approvals_disabled() -> bool:
    raw = str(os.getenv("HERMES_FEISHU_DISABLE_APPROVALS", "") or "").strip().lower()
    return raw in {"1", "true", "yes", "on"}


def db_path() -> Path:
    override = Path(str(get_hermes_memory_home() / "hermes-business.sqlite3"))
    override.parent.mkdir(parents=True, exist_ok=True)
    return override


def _now() -> int:
    return int(time.time())


def _json(value: Any) -> str:
    return json.dumps(value if value is not None else {}, ensure_ascii=False, sort_keys=True)


def _new_trace_id() -> str:
    return "trace-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _normalize_trace_id(value: Any) -> str:
    normalized = str(value or "").strip()
    return normalized or _new_trace_id()


def _session_env_value(name: str) -> str:
    try:
        from gateway.session_context import get_session_env

        return str(get_session_env(name) or "").strip()
    except Exception:
        return ""


def _current_memory_session_context() -> Dict[str, str]:
    return {
        "platform": _session_env_value("HERMES_SESSION_PLATFORM").lower(),
        "chat_id": _session_env_value("HERMES_SESSION_CHAT_ID"),
        "thread_id": _session_env_value("HERMES_SESSION_THREAD_ID"),
        "chat_type": _session_env_value("HERMES_SESSION_CHAT_TYPE").lower(),
        "user_id": _session_env_value("HERMES_SESSION_USER_ID"),
        "session_key": _session_env_value("HERMES_SESSION_KEY"),
    }


def _attach_memory_governance(
    *,
    payload: Optional[Dict[str, Any]],
    memory_kind: str,
    scope: str,
    owner_ref: str,
    governance_ref: str,
) -> Dict[str, Any]:
    memory = dict(payload or {})
    existing = memory.get("governance") if isinstance(memory.get("governance"), dict) else {}
    session = _current_memory_session_context()
    memory["governance"] = {
        **existing,
        "policy_version": 1,
        "memory_kind": memory_kind,
        "scope": str(scope or "").strip(),
        "owner_ref": str(owner_ref or "").strip(),
        "governance_ref": str(governance_ref or "").strip(),
        "writer": "business_db",
        "updated_at_unix": _now(),
        "session": {
            "platform": session.get("platform", ""),
            "chat_id": session.get("chat_id", ""),
            "thread_id": session.get("thread_id", ""),
            "chat_type": session.get("chat_type", ""),
            "user_id": session.get("user_id", ""),
            "session_key": session.get("session_key", ""),
        },
    }
    return memory


def _validate_user_memory_write(*, platform: str, user_id: str, scope: str) -> None:
    normalized_scope = str(scope or "profile").strip().lower() or "profile"
    if normalized_scope not in ALLOWED_USER_MEMORY_SCOPES:
        allowed = ", ".join(sorted(ALLOWED_USER_MEMORY_SCOPES))
        raise ValueError(f"user memory scope '{normalized_scope}' is not allowed; allowed scopes: {allowed}")

    session = _current_memory_session_context()
    session_platform = session.get("platform", "")
    session_chat_type = session.get("chat_type", "")
    session_user_id = session.get("user_id", "")
    if not session_platform and not session_chat_type and not session_user_id:
        return

    if normalized_scope in {"profile", "notes"}:
        if session_chat_type not in PRIVATE_CHAT_TYPES:
            raise ValueError("person memory writes require a private 1:1 conversation")
        if session_platform and session_platform != str(platform or "").strip().lower():
            raise ValueError("person memory writes cannot cross platform boundaries")
        if session_user_id and session_user_id != str(user_id or "").strip():
            raise ValueError("person memory writes cannot target another user in the current session")


def _validate_task_memory_write(*, task_id: str, scope: str) -> None:
    normalized_scope = str(scope or "shared").strip().lower() or "shared"
    if normalized_scope not in ALLOWED_TASK_MEMORY_SCOPES:
        allowed = ", ".join(sorted(ALLOWED_TASK_MEMORY_SCOPES))
        raise ValueError(f"task memory scope '{normalized_scope}' is not allowed; allowed scopes: {allowed}")

    session = _current_memory_session_context()
    session_platform = session.get("platform", "")
    session_chat_id = session.get("chat_id", "")
    session_thread_id = session.get("thread_id", "")
    session_chat_type = session.get("chat_type", "")
    if not session_platform or not session_chat_id:
        return

    bound_task = get_channel_task(platform=session_platform, chat_id=session_chat_id, thread_id=session_thread_id)
    bound = (bound_task or {}).get("task") if isinstance(bound_task, dict) else None
    bound_task_id = str((bound or {}).get("task_id") or "").strip()
    if session_chat_type in PRIVATE_CHAT_TYPES:
        if bound_task_id and bound_task_id != str(task_id or "").strip():
            raise ValueError("task memory write does not match the task bound to the current private conversation")
        return
    if not bound_task_id:
        raise ValueError("shared-chat task memory writes require the current chat to be bound to a task")
    if bound_task_id != str(task_id or "").strip():
        raise ValueError("task memory write does not match the task bound to the current chat")


def _validate_distilled_profile_mutation(
    *,
    platform: str,
    user_id: str,
    actor_user_id: str = "",
    allow_cross_user: bool = False,
    action: str = "",
) -> None:
    session = _current_memory_session_context()
    session_platform = session.get("platform", "")
    session_chat_type = session.get("chat_type", "")
    session_user_id = session.get("user_id", "")
    if not session_platform and not session_chat_type and not session_user_id:
        return

    normalized_platform = str(platform or "").strip().lower()
    normalized_user_id = str(user_id or "").strip()
    normalized_actor_user_id = str(actor_user_id or "").strip()
    if session_platform and normalized_platform and session_platform != normalized_platform:
        raise ValueError("distilled profile mutations cannot cross platform boundaries")
    if session_chat_type and session_chat_type not in PRIVATE_CHAT_TYPES:
        raise ValueError("distilled profile mutations require a private 1:1 conversation")

    effective_actor_user_id = normalized_actor_user_id or session_user_id
    if effective_actor_user_id and effective_actor_user_id != normalized_user_id and not allow_cross_user:
        action_label = str(action or "mutation").strip() or "mutation"
        raise ValueError(f"distilled profile {action_label} cannot target another user without profile management permission")


def _build_distilled_governance(
    *,
    existing_memory: Optional[Dict[str, Any]],
    platform: str,
    user_id: str,
    action: str,
    actor: str,
    allow_cross_user: bool = False,
) -> Dict[str, Any]:
    normalized = _normalize_distilled_memory(existing_memory)
    governance = dict(normalized.get("governance") or {})
    session = _current_memory_session_context()
    governance.update(
        {
            "memory_kind": "person",
            "scope": "distilled",
            "owner_ref": f"{str(platform or '').strip().lower()}:user:{str(user_id or '').strip()}",
            "last_action": str(action or "").strip(),
            "last_actor": str(actor or "").strip(),
            "last_action_at_unix": _now(),
            "cross_user_override": bool(allow_cross_user),
            "write_context": {
                "platform": session.get("platform", ""),
                "chat_id": session.get("chat_id", ""),
                "thread_id": session.get("thread_id", ""),
                "chat_type": session.get("chat_type", ""),
                "user_id": session.get("user_id", ""),
                "session_key": session.get("session_key", ""),
            },
        }
    )
    return governance


def connect() -> sqlite3.Connection:
    conn = sqlite3.connect(str(db_path()), timeout=30)
    conn.row_factory = sqlite3.Row
    conn.execute("PRAGMA journal_mode=WAL")
    conn.execute("PRAGMA foreign_keys=ON")
    conn.execute("PRAGMA busy_timeout=30000")
    initialize_database(conn)
    return conn


def initialize_database(conn: Optional[sqlite3.Connection] = None) -> None:
    owns_conn = conn is None
    if conn is None:
        conn = sqlite3.connect(str(db_path()), timeout=30)
    try:
        conn.executescript(
            """
            CREATE TABLE IF NOT EXISTS schema_meta (
                key TEXT PRIMARY KEY,
                value TEXT NOT NULL,
                updated_at_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS users (
                user_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                user_id TEXT NOT NULL,
                display_name TEXT NOT NULL DEFAULT '',
                role TEXT NOT NULL DEFAULT 'user',
                permissions_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                last_seen_at_unix INTEGER
            );

            CREATE TABLE IF NOT EXISTS channels (
                channel_key TEXT PRIMARY KEY,
                platform TEXT NOT NULL,
                chat_id TEXT NOT NULL,
                thread_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                chat_name TEXT NOT NULL DEFAULT '',
                chat_type TEXT NOT NULL DEFAULT '',
                worker_role TEXT NOT NULL DEFAULT '',
                allow_free_chat INTEGER NOT NULL DEFAULT 0,
                policy_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS user_memory (
                memory_key TEXT PRIMARY KEY,
                user_key TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'profile',
                summary TEXT NOT NULL DEFAULT '',
                memory_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                FOREIGN KEY(user_key) REFERENCES users(user_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS tasks (
                task_id TEXT PRIMARY KEY,
                title TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'open',
                owner_user_id TEXT NOT NULL DEFAULT '',
                source_platform TEXT NOT NULL DEFAULT '',
                source_chat_id TEXT NOT NULL DEFAULT '',
                source_thread_id TEXT NOT NULL DEFAULT '',
                source_session_id TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                started_at_unix INTEGER,
                finished_at_unix INTEGER
            );

            CREATE TABLE IF NOT EXISTS task_participants (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                task_id TEXT NOT NULL,
                user_key TEXT NOT NULL,
                role TEXT NOT NULL DEFAULT 'participant',
                added_at_unix INTEGER NOT NULL,
                UNIQUE(task_id, user_key),
                FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE,
                FOREIGN KEY(user_key) REFERENCES users(user_key) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS task_memory (
                memory_key TEXT PRIMARY KEY,
                task_id TEXT NOT NULL,
                scope TEXT NOT NULL DEFAULT 'shared',
                summary TEXT NOT NULL DEFAULT '',
                memory_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                FOREIGN KEY(task_id) REFERENCES tasks(task_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS background_jobs (
                job_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL DEFAULT '',
                task_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                prompt TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                priority TEXT NOT NULL DEFAULT '',
                tags_json TEXT NOT NULL DEFAULT '[]',
                origin_json TEXT NOT NULL DEFAULT '{}',
                session_id TEXT NOT NULL DEFAULT '',
                user_id TEXT NOT NULL DEFAULT '',
                created_at_unix INTEGER,
                updated_at_unix INTEGER,
                started_at_unix INTEGER,
                finished_at_unix INTEGER,
                current_focus TEXT NOT NULL DEFAULT '',
                next_step TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                artifact_paths_json TEXT NOT NULL DEFAULT '[]',
                job_dir TEXT NOT NULL DEFAULT '',
                events_path TEXT NOT NULL DEFAULT '',
                executor TEXT NOT NULL DEFAULT '',
                delivery_status TEXT NOT NULL DEFAULT '',
                delivery_error TEXT NOT NULL DEFAULT '',
                delivery_target TEXT NOT NULL DEFAULT '',
                delivered_at_unix INTEGER
            );

            CREATE TABLE IF NOT EXISTS background_job_events (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                job_id TEXT NOT NULL,
                trace_id TEXT NOT NULL DEFAULT '',
                timestamp_unix INTEGER NOT NULL,
                timestamp TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT '',
                message TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(job_id) REFERENCES background_jobs(job_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS capabilities (
                capability_id TEXT PRIMARY KEY,
                name TEXT NOT NULL UNIQUE,
                description TEXT NOT NULL DEFAULT '',
                category TEXT NOT NULL DEFAULT '',
                risk_level TEXT NOT NULL DEFAULT 'medium',
                default_approval_required INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                config_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS capability_runs (
                run_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL DEFAULT '',
                capability_id TEXT NOT NULL,
                task_id TEXT NOT NULL DEFAULT '',
                title TEXT NOT NULL DEFAULT '',
                goal TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'queued',
                priority TEXT NOT NULL DEFAULT 'normal',
                origin_json TEXT NOT NULL DEFAULT '{}',
                actor_user_id TEXT NOT NULL DEFAULT '',
                session_id TEXT NOT NULL DEFAULT '',
                background_job_id TEXT NOT NULL DEFAULT '',
                approval_id TEXT NOT NULL DEFAULT '',
                current_focus TEXT NOT NULL DEFAULT '',
                next_step TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                result TEXT NOT NULL DEFAULT '',
                input_json TEXT NOT NULL DEFAULT '{}',
                output_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL,
                started_at_unix INTEGER,
                finished_at_unix INTEGER,
                FOREIGN KEY(capability_id) REFERENCES capabilities(capability_id)
            );

            CREATE TABLE IF NOT EXISTS capability_steps (
                step_id TEXT PRIMARY KEY,
                run_id TEXT NOT NULL,
                step_order INTEGER NOT NULL DEFAULT 0,
                name TEXT NOT NULL DEFAULT '',
                status TEXT NOT NULL DEFAULT 'pending',
                current_focus TEXT NOT NULL DEFAULT '',
                evidence TEXT NOT NULL DEFAULT '',
                blocker TEXT NOT NULL DEFAULT '',
                started_at_unix INTEGER,
                finished_at_unix INTEGER,
                payload_json TEXT NOT NULL DEFAULT '{}',
                FOREIGN KEY(run_id) REFERENCES capability_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS capability_artifacts (
                artifact_id TEXT PRIMARY KEY,
                trace_id TEXT NOT NULL DEFAULT '',
                run_id TEXT NOT NULL,
                step_id TEXT NOT NULL DEFAULT '',
                kind TEXT NOT NULL DEFAULT '',
                label TEXT NOT NULL DEFAULT '',
                path_or_ref TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                metadata_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                FOREIGN KEY(run_id) REFERENCES capability_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS table_sync_rules (
                rule_id TEXT PRIMARY KEY,
                name TEXT NOT NULL,
                source_ref TEXT NOT NULL,
                target_ref TEXT NOT NULL,
                match_keys_json TEXT NOT NULL DEFAULT '[]',
                field_map_json TEXT NOT NULL DEFAULT '{}',
                write_policy TEXT NOT NULL DEFAULT 'fill_blank',
                approval_required INTEGER NOT NULL DEFAULT 1,
                enabled INTEGER NOT NULL DEFAULT 1,
                owner_user_id TEXT NOT NULL DEFAULT '',
                created_at_unix INTEGER NOT NULL,
                updated_at_unix INTEGER NOT NULL
            );

            CREATE TABLE IF NOT EXISTS table_sync_runs (
                run_id TEXT PRIMARY KEY,
                rule_id TEXT NOT NULL,
                status TEXT NOT NULL,
                dry_run INTEGER NOT NULL DEFAULT 1,
                actor_user_id TEXT NOT NULL DEFAULT '',
                started_at_unix INTEGER NOT NULL,
                finished_at_unix INTEGER,
                summary TEXT NOT NULL DEFAULT '',
                diff_count INTEGER NOT NULL DEFAULT 0,
                error TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(rule_id) REFERENCES table_sync_rules(rule_id)
            );

            CREATE TABLE IF NOT EXISTS table_sync_diffs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                run_id TEXT NOT NULL,
                target_ref TEXT NOT NULL DEFAULT '',
                sheet_id TEXT NOT NULL DEFAULT '',
                row_key TEXT NOT NULL DEFAULT '',
                cell_ref TEXT NOT NULL DEFAULT '',
                field_name TEXT NOT NULL DEFAULT '',
                old_value TEXT NOT NULL DEFAULT '',
                new_value TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL DEFAULT '',
                FOREIGN KEY(run_id) REFERENCES table_sync_runs(run_id) ON DELETE CASCADE
            );

            CREATE TABLE IF NOT EXISTS approvals (
                approval_id TEXT PRIMARY KEY,
                kind TEXT NOT NULL,
                target_id TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending',
                requested_by TEXT NOT NULL DEFAULT '',
                approved_by TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}',
                created_at_unix INTEGER NOT NULL,
                decided_at_unix INTEGER
            );

            CREATE TABLE IF NOT EXISTS audit_logs (
                id INTEGER PRIMARY KEY AUTOINCREMENT,
                timestamp_unix INTEGER NOT NULL,
                actor_user_id TEXT NOT NULL DEFAULT '',
                platform TEXT NOT NULL DEFAULT '',
                chat_id TEXT NOT NULL DEFAULT '',
                action TEXT NOT NULL,
                target_type TEXT NOT NULL DEFAULT '',
                target_id TEXT NOT NULL DEFAULT '',
                summary TEXT NOT NULL DEFAULT '',
                payload_json TEXT NOT NULL DEFAULT '{}'
            );

            CREATE INDEX IF NOT EXISTS idx_users_platform_user ON users(platform, user_id);
            CREATE INDEX IF NOT EXISTS idx_channels_platform_chat ON channels(platform, chat_id, thread_id);
            CREATE INDEX IF NOT EXISTS idx_user_memory_user_key ON user_memory(user_key, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_tasks_status_updated ON tasks(status, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_task_participants_task_id ON task_participants(task_id, added_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_task_memory_task_id ON task_memory(task_id, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_background_jobs_status_updated ON background_jobs(status, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_background_job_events_job_time ON background_job_events(job_id, timestamp_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_capabilities_enabled ON capabilities(enabled, category, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_capability_runs_status ON capability_runs(status, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_capability_runs_capability ON capability_runs(capability_id, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_capability_steps_run_order ON capability_steps(run_id, step_order);
            CREATE INDEX IF NOT EXISTS idx_capability_artifacts_run ON capability_artifacts(run_id, created_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_table_sync_rules_enabled ON table_sync_rules(enabled, updated_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_table_sync_runs_rule_time ON table_sync_runs(rule_id, started_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_approvals_status ON approvals(status, created_at_unix DESC);
            CREATE INDEX IF NOT EXISTS idx_audit_logs_time ON audit_logs(timestamp_unix DESC);
            """
        )
        _ensure_column(conn, "background_jobs", "trace_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "background_jobs", "task_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "background_job_events", "trace_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "capability_runs", "trace_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "capability_artifacts", "trace_id", "TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_background_jobs_trace_id ON background_jobs(trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_background_job_events_trace_id ON background_job_events(trace_id, timestamp_unix DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_runs_trace_id ON capability_runs(trace_id)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_artifacts_trace_id ON capability_artifacts(trace_id, created_at_unix DESC)")
        _ensure_column(conn, "channels", "policy_json", "TEXT NOT NULL DEFAULT '{}'")
        _ensure_column(conn, "channels", "task_id", "TEXT NOT NULL DEFAULT ''")
        _ensure_column(conn, "capability_runs", "task_id", "TEXT NOT NULL DEFAULT ''")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_channels_task_id ON channels(task_id, updated_at_unix DESC)")
        conn.execute("CREATE INDEX IF NOT EXISTS idx_capability_runs_task_id ON capability_runs(task_id, updated_at_unix DESC)")
        conn.execute(
            """
            INSERT INTO schema_meta(key, value, updated_at_unix)
            VALUES('schema_version', ?, ?)
            ON CONFLICT(key) DO UPDATE SET value=excluded.value, updated_at_unix=excluded.updated_at_unix
            """,
            (str(SCHEMA_VERSION), _now()),
        )
        _ensure_default_capabilities(conn)
        conn.commit()
    finally:
        if owns_conn:
            conn.close()


def _ensure_column(conn: sqlite3.Connection, table: str, column: str, definition: str) -> None:
    rows = conn.execute(f"PRAGMA table_info({table})").fetchall()
    if any(row[1] == column for row in rows):
        return
    conn.execute(f"ALTER TABLE {table} ADD COLUMN {column} {definition}")


def _ensure_default_capabilities(conn: sqlite3.Connection) -> None:
    defaults = [
        {
            "name": "table_sync",
            "description": "Read one business table, map/validate fields, generate diff, and write to another table after approval.",
            "category": "business_automation",
            "risk_level": "high",
            "default_approval_required": True,
            "config": get_capability_execution_policy("table_sync"),
        },
        {
            "name": "bid_research",
            "description": "Collect, deduplicate, and summarize tender/bid opportunities with evidence.",
            "category": "research",
            "risk_level": "medium",
            "default_approval_required": False,
            "config": get_capability_execution_policy("bid_research"),
        },
        {
            "name": "contract_retrieval",
            "description": "Trace and retrieve contract evidence for a named project from local storage, shared drives, and public procurement sources.",
            "category": "research",
            "risk_level": "medium",
            "default_approval_required": False,
            "config": get_capability_execution_policy("contract_retrieval"),
        },
        {
            "name": "customer_followup",
            "description": "Track customer requests, draft follow-up messages, summarize history, and prepare next actions.",
            "category": "sales",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("customer_followup"),
        },
        {
            "name": "quote_generation",
            "description": "Prepare quotation drafts from product/customer data, pricing rules, and required document templates.",
            "category": "sales",
            "risk_level": "high",
            "default_approval_required": True,
            "config": get_capability_execution_policy("quote_generation"),
        },
        {
            "name": "contract_review",
            "description": "Review contracts or order terms, extract risks, compare against standard clauses, and produce approval notes.",
            "category": "legal_compliance",
            "risk_level": "high",
            "default_approval_required": True,
            "config": get_capability_execution_policy("contract_review"),
        },
        {
            "name": "procurement_compare",
            "description": "Compare supplier quotes, delivery times, specs, and risks, then produce procurement recommendations.",
            "category": "procurement",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("procurement_compare"),
        },
        {
            "name": "inventory_check",
            "description": "Reconcile inventory, order, and shipment records, flag mismatches, and prepare correction tasks.",
            "category": "operations",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("inventory_check"),
        },
        {
            "name": "finance_reconcile",
            "description": "Compare invoices, payments, reimbursements, and statements; produce exceptions and reconciliation summaries.",
            "category": "finance",
            "risk_level": "high",
            "default_approval_required": True,
            "config": get_capability_execution_policy("finance_reconcile"),
        },
        {
            "name": "hr_admin",
            "description": "Draft HR/admin notices, summarize attendance or onboarding materials, and prepare routine admin workflows.",
            "category": "hr_admin",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("hr_admin"),
        },
        {
            "name": "meeting_minutes",
            "description": "Turn meeting notes, chat logs, or transcripts into decisions, owners, deadlines, and follow-up tasks.",
            "category": "collaboration",
            "risk_level": "low",
            "default_approval_required": False,
            "config": get_capability_execution_policy("meeting_minutes"),
        },
        {
            "name": "project_tracking",
            "description": "Track project milestones, blockers, owners, deliverables, and produce action-oriented status updates.",
            "category": "project_management",
            "risk_level": "medium",
            "default_approval_required": False,
            "config": get_capability_execution_policy("project_tracking"),
        },
        {
            "name": "document_generate",
            "description": "Generate or update business documents from templates, structured inputs, and knowledge-base references.",
            "category": "documents",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("document_generate"),
        },
        {
            "name": "data_cleaning",
            "description": "Clean, normalize, deduplicate, merge, and validate business datasets with a reviewable change summary.",
            "category": "data",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("data_cleaning"),
        },
        {
            "name": "workflow_dispatch",
            "description": "Receive a broad business request, classify it, split it into capability runs, and route work to owners or workers.",
            "category": "orchestration",
            "risk_level": "medium",
            "default_approval_required": False,
            "config": get_capability_execution_policy("workflow_dispatch"),
        },
        {
            "name": "knowledge_ingest",
            "description": "Ingest submitted files or folders into the company knowledge base with metadata and permissions.",
            "category": "knowledge",
            "risk_level": "medium",
            "default_approval_required": True,
            "config": get_capability_execution_policy("knowledge_ingest"),
        },
        {
            "name": "report_rollup",
            "description": "Summarize tasks, blockers, usage, and deliverables into daily or weekly reports.",
            "category": "reporting",
            "risk_level": "low",
            "default_approval_required": False,
            "config": get_capability_execution_policy("report_rollup"),
        },
        {
            "name": "ops_recovery",
            "description": "Inspect, restart, and recover Hermes service components with audit logging.",
            "category": "operations",
            "risk_level": "high",
            "default_approval_required": True,
            "config": get_capability_execution_policy("ops_recovery"),
        },
    ]
    now = _now()
    for item in defaults:
        capability_id = "cap-" + item["name"].replace("_", "-")
        conn.execute(
            """
            INSERT INTO capabilities(
                capability_id, name, description, category, risk_level,
                default_approval_required, enabled, config_json,
                created_at_unix, updated_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, 1, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                category=excluded.category,
                risk_level=excluded.risk_level,
                default_approval_required=excluded.default_approval_required,
                config_json=excluded.config_json,
                updated_at_unix=excluded.updated_at_unix
            """,
            (
                capability_id,
                item["name"],
                item["description"],
                item["category"],
                item["risk_level"],
                1 if item["default_approval_required"] else 0,
                _json(item.get("config") if isinstance(item.get("config"), dict) else {}),
                now,
                now,
            ),
        )


def create_capability(
    *,
    name: str,
    description: str = "",
    category: str = "custom",
    risk_level: str = "medium",
    default_approval_required: bool = True,
    enabled: bool = True,
    config: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    safe_name = re.sub(r"[^a-zA-Z0-9_]+", "_", str(name or "").strip().lower()).strip("_")
    if not safe_name:
        raise ValueError("Capability name is required")
    capability_id = "cap-" + safe_name.replace("_", "-")
    now = _now()
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO capabilities(
                capability_id, name, description, category, risk_level,
                default_approval_required, enabled, config_json,
                created_at_unix, updated_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(name) DO UPDATE SET
                description=excluded.description,
                category=excluded.category,
                risk_level=excluded.risk_level,
                default_approval_required=excluded.default_approval_required,
                enabled=excluded.enabled,
                config_json=excluded.config_json,
                updated_at_unix=excluded.updated_at_unix
            """,
            (
                capability_id,
                safe_name,
                str(description or "").strip(),
                str(category or "custom").strip() or "custom",
                str(risk_level or "medium").strip() or "medium",
                1 if default_approval_required else 0,
                1 if enabled else 0,
                _json(config or {}),
                now,
                now,
            ),
        )
        conn.commit()
    return get_capability(safe_name) or {}


def update_capability(identifier: str, **fields: Any) -> Optional[Dict[str, Any]]:
    cap = get_capability(identifier)
    if not cap:
        return None
    allowed = {
        "description",
        "category",
        "risk_level",
        "default_approval_required",
        "enabled",
        "config",
    }
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return cap

    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        column = "config_json" if key == "config" else key
        assignments.append(f"{column}=?")
        if key == "config":
            values.append(_json(value if isinstance(value, dict) else {}))
        elif key in {"default_approval_required", "enabled"}:
            values.append(1 if bool(value) else 0)
        else:
            values.append(str(value))
    assignments.append("updated_at_unix=?")
    values.append(_now())
    values.append(cap["capability_id"])
    with connect() as conn:
        conn.execute(f"UPDATE capabilities SET {', '.join(assignments)} WHERE capability_id=?", values)
        conn.commit()
    return get_capability(cap["capability_id"])


def _user_key(platform: str, user_id: str) -> str:
    return f"{str(platform or '').strip().lower()}:{str(user_id or '').strip()}"


def _channel_key(platform: str, chat_id: str, thread_id: str = "") -> str:
    base = f"{str(platform or '').strip().lower()}:{str(chat_id or '').strip()}"
    thread = str(thread_id or "").strip()
    return f"{base}:{thread}" if thread else base


def _safe_json_loads(value: str, fallback: Any) -> Any:
    try:
        return json.loads(value or "")
    except Exception:
        return fallback


def _new_task_id() -> str:
    return "task-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]


def _normalize_task_id(value: Any) -> str:
    return str(value or "").strip()


def upsert_user(
    *,
    platform: str,
    user_id: str,
    display_name: str = "",
    role: str = "user",
    permissions: Optional[Dict[str, Any]] = None,
    mark_seen: bool = True,
) -> Dict[str, Any]:
    key = _user_key(platform, user_id)
    if not key.split(":", 1)[-1]:
        raise ValueError("user_id is required")
    now = _now()
    existing_user = get_user(key) or {}
    existing_role = str(existing_user.get("role") or "").strip().lower()
    next_role = str(role or "user").strip().lower()
    merged_permissions = dict(existing_user.get("permissions") or {})
    merged_permissions.update(permissions or {})
    if existing_role == "owner" and next_role != "blocked":
        next_role = "owner"
    if next_role == "owner":
        merged_permissions["bypass_approval"] = True
        merged_permissions["global_owner"] = True
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO users(user_key, platform, user_id, display_name, role, permissions_json, created_at_unix, updated_at_unix, last_seen_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(user_key) DO UPDATE SET
                display_name=excluded.display_name,
                role=excluded.role,
                permissions_json=excluded.permissions_json,
                updated_at_unix=excluded.updated_at_unix,
                last_seen_at_unix=excluded.last_seen_at_unix
            """,
            (
                key,
                str(platform or "").strip().lower(),
                str(user_id or "").strip(),
                str(display_name or "").strip(),
                next_role,
                _json(merged_permissions),
                now,
                now,
                now if mark_seen else None,
            ),
        )
        conn.commit()
    return get_user(key) or {}


def _user_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "user_key": row["user_key"],
        "platform": row["platform"],
        "user_id": row["user_id"],
        "display_name": row["display_name"],
        "role": row["role"],
        "permissions": _safe_json_loads(row["permissions_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
        "last_seen_at_unix": row["last_seen_at_unix"],
    }


def get_user(identifier: str = "", *, platform: str = "", user_id: str = "") -> Optional[Dict[str, Any]]:
    key = str(identifier or "").strip()
    if not key and platform and user_id:
        key = _user_key(platform, user_id)
    if not key:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM users WHERE user_key=? OR user_id=? ORDER BY updated_at_unix DESC LIMIT 1",
            (key, key),
        ).fetchone()
    return _user_from_row(row) if row else None


def list_users(limit: int = 50, role: str = "") -> list[Dict[str, Any]]:
    query = "SELECT * FROM users"
    params: list[Any] = []
    if role:
        query += " WHERE role=?"
        params.append(role)
    query += " ORDER BY updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_user_from_row(row) for row in rows]


def list_recent_users(
    *,
    platform: str = "",
    seen_since_unix: int = 0,
    limit: int = 50,
    include_updated_fallback: bool = False,
) -> list[Dict[str, Any]]:
    query = "SELECT * FROM users"
    clauses: list[str] = []
    params: list[Any] = []
    if platform:
        clauses.append("platform=?")
        params.append(str(platform or "").strip().lower())
    if seen_since_unix > 0:
        activity_column = "COALESCE(last_seen_at_unix, updated_at_unix, 0)" if include_updated_fallback else "COALESCE(last_seen_at_unix, 0)"
        clauses.append(f"{activity_column} >= ?")
        params.append(int(seen_since_unix))
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY COALESCE(last_seen_at_unix, updated_at_unix) DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_user_from_row(row) for row in rows]


def upsert_channel(
    *,
    platform: str,
    chat_id: str,
    thread_id: str = "",
    task_id: str = "",
    chat_name: str = "",
    chat_type: str = "",
    worker_role: str = "",
    allow_free_chat: bool = False,
    policy: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    key = _channel_key(platform, chat_id, thread_id)
    if not str(chat_id or "").strip():
        raise ValueError("chat_id is required")
    now = _now()
    existing_channel = get_channel(key) or {}
    effective_task_id = _normalize_task_id(task_id or (existing_channel.get("task_id") or ""))
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO channels(channel_key, platform, chat_id, thread_id, task_id, chat_name, chat_type, worker_role, allow_free_chat, policy_json, created_at_unix, updated_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(channel_key) DO UPDATE SET
                task_id=excluded.task_id,
                chat_name=excluded.chat_name,
                chat_type=excluded.chat_type,
                worker_role=excluded.worker_role,
                allow_free_chat=excluded.allow_free_chat,
                policy_json=excluded.policy_json,
                updated_at_unix=excluded.updated_at_unix
            """,
            (
                key,
                str(platform or "").strip().lower(),
                str(chat_id or "").strip(),
                str(thread_id or "").strip(),
                effective_task_id,
                str(chat_name or "").strip(),
                str(chat_type or "").strip(),
                str(worker_role or "").strip(),
                1 if allow_free_chat else 0,
                _json(policy or {}),
                now,
                now,
            ),
        )
        conn.commit()
    return get_channel(key) or {}


def _channel_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "channel_key": row["channel_key"],
        "platform": row["platform"],
        "chat_id": row["chat_id"],
        "thread_id": row["thread_id"],
        "task_id": row["task_id"],
        "chat_name": row["chat_name"],
        "chat_type": row["chat_type"],
        "worker_role": row["worker_role"],
        "allow_free_chat": bool(row["allow_free_chat"]),
        "policy": _safe_json_loads(row["policy_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
    }


def get_channel(identifier: str = "", *, platform: str = "", chat_id: str = "", thread_id: str = "") -> Optional[Dict[str, Any]]:
    key = str(identifier or "").strip()
    if not key and platform and chat_id:
        key = _channel_key(platform, chat_id, thread_id)
    if not key:
        return None
    with connect() as conn:
        row = conn.execute("SELECT * FROM channels WHERE channel_key=?", (key,)).fetchone()
    return _channel_from_row(row) if row else None


def list_channels(limit: int = 50, platform: str = "") -> list[Dict[str, Any]]:
    query = "SELECT * FROM channels"
    params: list[Any] = []
    if platform:
        query += " WHERE platform=?"
        params.append(platform)
    query += " ORDER BY updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 50)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_channel_from_row(row) for row in rows]


def list_task_channels(task_id: str, *, platform: str = "", limit: int = 100) -> list[Dict[str, Any]]:
    normalized_task_id = _normalize_task_id(task_id)
    if not normalized_task_id:
        return []
    query = "SELECT * FROM channels WHERE task_id=?"
    params: list[Any] = [normalized_task_id]
    if platform:
        query += " AND platform=?"
        params.append(str(platform or "").strip().lower())
    query += " ORDER BY updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 100)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_channel_from_row(row) for row in rows]


def _memory_key(prefix: str, identifier: str, scope: str) -> str:
    return f"{prefix}:{str(identifier or '').strip()}:{str(scope or 'shared').strip().lower()}"


_DISTILLED_PROFILE_FIELDS = (
    "core_principles",
    "working_style",
    "preferred_output",
    "domain_focus",
    "decision_heuristics",
    "approval_sensitivity",
    "anti_patterns",
    "stable_instructions",
)
DISTILLED_PROFILE_FIELDS = _DISTILLED_PROFILE_FIELDS
_DISTILLED_HISTORY_LIMIT = 20


def _normalize_distilled_memory(memory: Optional[Dict[str, Any]]) -> Dict[str, Any]:
    payload = memory if isinstance(memory, dict) else {}
    nested_profile = payload.get("profile") if isinstance(payload.get("profile"), dict) else {}
    nested_draft = payload.get("draft") if isinstance(payload.get("draft"), dict) else {}
    profile: Dict[str, Any] = {}
    for field in _DISTILLED_PROFILE_FIELDS:
        value = nested_profile.get(field)
        if value in (None, "", []):
            value = payload.get(field)
        if value not in (None, "", []):
            profile[field] = value

    draft_profile: Dict[str, Any] = {}
    nested_draft_profile = nested_draft.get("profile") if isinstance(nested_draft.get("profile"), dict) else {}
    for field in _DISTILLED_PROFILE_FIELDS:
        value = nested_draft_profile.get(field)
        if value not in (None, "", []):
            draft_profile[field] = value

    nested_evidence = payload.get("evidence") if isinstance(payload.get("evidence"), dict) else {}
    nested_sources = payload.get("sources") if isinstance(payload.get("sources"), dict) else {}
    nested_draft_evidence = nested_draft.get("evidence") if isinstance(nested_draft.get("evidence"), dict) else {}
    nested_draft_sources = nested_draft.get("sources") if isinstance(nested_draft.get("sources"), dict) else {}
    manual_overrides = payload.get("manual_overrides") if isinstance(payload.get("manual_overrides"), dict) else {}
    locked_fields = payload.get("locked_fields") if isinstance(payload.get("locked_fields"), list) else []
    governance = payload.get("governance") if isinstance(payload.get("governance"), dict) else {}
    raw_history = payload.get("history") if isinstance(payload.get("history"), list) else []
    history: list[Dict[str, Any]] = []
    for item in raw_history[:_DISTILLED_HISTORY_LIMIT]:
        if not isinstance(item, dict):
            continue
        history.append(
            {
                "timestamp_unix": int(item.get("timestamp_unix") or 0),
                "action": str(item.get("action") or "").strip(),
                "actor": str(item.get("actor") or "").strip(),
                "summary": str(item.get("summary") or "").strip(),
                "field_names": [
                    str(field).strip()
                    for field in item.get("field_names", [])
                    if str(field).strip()
                ] if isinstance(item.get("field_names"), list) else [],
            }
        )

    sources = dict(nested_sources)
    for legacy_key in ("recent_capabilities", "recent_task_titles", "recent_background_job_titles", "source_window_days"):
        if legacy_key in payload and legacy_key not in sources:
            sources[legacy_key] = payload.get(legacy_key)

    return {
        "schema_version": int(payload.get("schema_version") or 1),
        "profile": profile,
        "draft": {
            "summary": str(nested_draft.get("summary") or "").strip(),
            "profile": draft_profile,
            "evidence": nested_draft_evidence,
            "sources": nested_draft_sources,
            "generated_at_unix": int(nested_draft.get("generated_at_unix") or 0),
            "updated_by": str(nested_draft.get("updated_by") or "").strip(),
        },
        "evidence": nested_evidence,
        "sources": sources,
        "manual_overrides": {str(key).strip(): value for key, value in manual_overrides.items() if str(key).strip()},
        "locked_fields": sorted({str(item).strip() for item in locked_fields if str(item).strip()}),
        "governance": dict(governance),
        "history": history,
        "generated_at_unix": int(payload.get("generated_at_unix") or 0),
        "updated_by": str(payload.get("updated_by") or "").strip(),
    }


def _build_distilled_summary(profile: Dict[str, Any]) -> str:
    working_style = str(profile.get("working_style") or "").strip()
    preferred_output = str(profile.get("preferred_output") or "").strip()
    domain_focus = str(profile.get("domain_focus") or "").strip()
    approval_sensitivity = str(profile.get("approval_sensitivity") or "").strip()
    parts = []
    if working_style:
        parts.append(f"工作风格：{working_style}")
    if preferred_output:
        parts.append(f"输出偏好：{preferred_output}")
    if domain_focus:
        parts.append(f"领域重点：{domain_focus}")
    if approval_sensitivity:
        parts.append(f"审批敏感度：{approval_sensitivity}")
    return "；".join(parts) + ("。" if parts else "")


def _empty_distilled_draft() -> Dict[str, Any]:
    return {
        "summary": "",
        "profile": {},
        "evidence": {},
        "sources": {},
        "generated_at_unix": 0,
        "updated_by": "",
    }


def _distilled_payload_from_normalized(normalized: Dict[str, Any]) -> Dict[str, Any]:
    draft = normalized.get("draft") if isinstance(normalized.get("draft"), dict) else {}
    return {
        "schema_version": 2,
        "profile": dict(normalized.get("profile") or {}),
        "draft": {
            "summary": str(draft.get("summary") or "").strip(),
            "profile": dict(draft.get("profile") or {}),
            "evidence": dict(draft.get("evidence") or {}),
            "sources": dict(draft.get("sources") or {}),
            "generated_at_unix": int(draft.get("generated_at_unix") or 0),
            "updated_by": str(draft.get("updated_by") or "").strip(),
        },
        "evidence": dict(normalized.get("evidence") or {}),
        "sources": dict(normalized.get("sources") or {}),
        "manual_overrides": dict(normalized.get("manual_overrides") or {}),
        "locked_fields": list(normalized.get("locked_fields") or []),
        "governance": dict(normalized.get("governance") or {}),
        "history": list(normalized.get("history") or []),
        "generated_at_unix": int(normalized.get("generated_at_unix") or 0),
        "updated_by": str(normalized.get("updated_by") or "").strip(),
    }


def _make_distilled_history_entry(
    *,
    action: str,
    actor: str,
    summary: str = "",
    field_names: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    return {
        "timestamp_unix": _now(),
        "action": str(action or "").strip(),
        "actor": str(actor or "").strip(),
        "summary": str(summary or "").strip(),
        "field_names": [
            str(field).strip()
            for field in (field_names or [])
            if str(field).strip()
        ],
    }


def _append_distilled_history(
    *,
    platform: str,
    user_id: str,
    record: Dict[str, Any],
    entry: Optional[Dict[str, Any]],
) -> Dict[str, Any]:
    if not isinstance(record, dict) or not isinstance(entry, dict):
        return record
    normalized = _normalize_distilled_memory(record.get("memory") if isinstance(record, dict) else None)
    history = [entry]
    history.extend(normalized.get("history") or [])
    normalized["history"] = history[:_DISTILLED_HISTORY_LIMIT]
    return upsert_user_memory(
        platform=platform,
        user_id=user_id,
        scope="distilled",
        summary=str(record.get("summary") or "").strip(),
        memory=_distilled_payload_from_normalized(normalized),
    )


def upsert_user_memory(*, platform: str, user_id: str, scope: str = "profile", summary: str = "", memory: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    normalized_platform = str(platform or "").strip().lower()
    normalized_user_id = str(user_id or "").strip()
    normalized_scope = str(scope or "profile").strip().lower() or "profile"
    _validate_user_memory_write(platform=normalized_platform, user_id=normalized_user_id, scope=normalized_scope)
    user = upsert_user(platform=platform, user_id=user_id, mark_seen=False)
    user_key = str(user.get("user_key") or "").strip()
    if not user_key:
        raise ValueError("user_key is required")
    now = _now()
    memory_key = _memory_key("user-memory", user_key, normalized_scope)
    governed_memory = _attach_memory_governance(
        payload=memory,
        memory_kind="person",
        scope=normalized_scope,
        owner_ref=f"{normalized_platform}:user:{normalized_user_id}",
        governance_ref=f"user-memory:{user_key}:{normalized_scope}",
    )
    record = {
        "memory_key": memory_key,
        "user_key": user_key,
        "scope": normalized_scope,
        "summary": str(summary or "").strip(),
        "memory": governed_memory,
        "created_at_unix": now,
        "updated_at_unix": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO user_memory(memory_key, user_key, scope, summary, memory_json, created_at_unix, updated_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_key) DO UPDATE SET
                summary=excluded.summary,
                memory_json=excluded.memory_json,
                updated_at_unix=excluded.updated_at_unix
            """,
            (
                record["memory_key"],
                record["user_key"],
                record["scope"],
                record["summary"],
                _json(record["memory"]),
                now,
                now,
            ),
        )
        conn.commit()
    return get_user_memory(platform=platform, user_id=user_id, scope=normalized_scope) or {}


def get_user_memory(*, platform: str, user_id: str, scope: str = "profile") -> Optional[Dict[str, Any]]:
    user = get_user(platform=platform, user_id=user_id)
    if not user:
        return None
    memory_key = _memory_key("user-memory", str(user.get("user_key") or "").strip(), scope)
    with connect() as conn:
        row = conn.execute("SELECT * FROM user_memory WHERE memory_key=?", (memory_key,)).fetchone()
    if not row:
        return None
    return {
        "memory_key": row["memory_key"],
        "user_key": row["user_key"],
        "scope": row["scope"],
        "summary": row["summary"],
        "memory": _safe_json_loads(row["memory_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
    }


def upsert_user_distilled_profile(
    *,
    platform: str,
    user_id: str,
    summary: str = "",
    memory: Optional[Dict[str, Any]] = None,
    profile: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    sources: Optional[Dict[str, Any]] = None,
    manual_overrides: Optional[Dict[str, Any]] = None,
    locked_fields: Optional[Iterable[str]] = None,
    updated_by: str = "",
    governance: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    if any(value is not None for value in (profile, evidence, sources, manual_overrides, locked_fields)):
        existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
        existing_memory = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
        existing_profile = dict(existing_memory.get("profile") or {})
        final_profile = dict(profile or existing_profile)

        final_manual_overrides = dict(existing_memory.get("manual_overrides") or {})
        if manual_overrides is not None:
            final_manual_overrides.update(
                {str(key).strip(): value for key, value in manual_overrides.items() if str(key).strip()}
            )

        final_locked_fields = set(existing_memory.get("locked_fields") or [])
        if locked_fields is not None:
            final_locked_fields = {str(item).strip() for item in locked_fields if str(item).strip()}

        for field in final_locked_fields:
            if field in final_manual_overrides:
                final_profile[field] = final_manual_overrides[field]
            elif field in existing_profile:
                final_profile[field] = existing_profile[field]

        for field, value in final_manual_overrides.items():
            final_profile[field] = value

        payload = {
            "schema_version": 2,
            "profile": final_profile,
            "draft": dict(existing_memory.get("draft") or _empty_distilled_draft()),
            "evidence": evidence or {},
            "sources": sources or existing_memory.get("sources") or {},
            "manual_overrides": final_manual_overrides,
            "locked_fields": sorted(final_locked_fields),
            "governance": dict(governance or existing_memory.get("governance") or {}),
            "history": list(existing_memory.get("history") or []),
            "generated_at_unix": _now(),
            "updated_by": str(updated_by or "distiller").strip() or "distiller",
        }
        return upsert_user_memory(
            platform=platform,
            user_id=user_id,
            scope="distilled",
            summary=str(summary or "").strip() or _build_distilled_summary(final_profile),
            memory=payload,
        )

    return upsert_user_memory(
        platform=platform,
        user_id=user_id,
        scope="distilled",
        summary=summary,
        memory={
            **(memory or {}),
            "governance": dict(governance or ((memory or {}).get("governance") if isinstance(memory, dict) else {}) or {}),
        },
    )


def get_user_distilled_profile(*, platform: str, user_id: str) -> Optional[Dict[str, Any]]:
    return get_user_memory(platform=platform, user_id=user_id, scope="distilled")


def get_user_distilled_profile_draft(*, platform: str, user_id: str) -> Optional[Dict[str, Any]]:
    record = get_user_distilled_profile(platform=platform, user_id=user_id)
    if not isinstance(record, dict):
        return None
    normalized = _normalize_distilled_memory(record.get("memory") if isinstance(record, dict) else None)
    draft = normalized.get("draft") if isinstance(normalized, dict) else None
    if not isinstance(draft, dict):
        return None
    if not draft.get("profile") and not draft.get("summary"):
        return None
    return {
        "summary": str(draft.get("summary") or "").strip(),
        "profile": dict(draft.get("profile") or {}),
        "evidence": dict(draft.get("evidence") or {}),
        "sources": dict(draft.get("sources") or {}),
        "generated_at_unix": int(draft.get("generated_at_unix") or 0),
        "updated_by": str(draft.get("updated_by") or "").strip(),
    }


def save_user_distilled_profile_draft(
    *,
    platform: str,
    user_id: str,
    summary: str = "",
    profile: Optional[Dict[str, Any]] = None,
    evidence: Optional[Dict[str, Any]] = None,
    sources: Optional[Dict[str, Any]] = None,
    updated_by: str = "",
) -> Dict[str, Any]:
    existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
    normalized = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
    normalized["governance"] = _build_distilled_governance(
        existing_memory=existing.get("memory") if isinstance(existing, dict) else None,
        platform=platform,
        user_id=user_id,
        action="draft_saved",
        actor=str(updated_by or "distiller").strip() or "distiller",
        allow_cross_user=False,
    )
    normalized["draft"] = {
        "summary": str(summary or "").strip() or _build_distilled_summary(profile or {}),
        "profile": dict(profile or {}),
        "evidence": dict(evidence or {}),
        "sources": dict(sources or {}),
        "generated_at_unix": _now(),
        "updated_by": str(updated_by or "distiller").strip() or "distiller",
    }
    record = upsert_user_memory(
        platform=platform,
        user_id=user_id,
        scope="distilled",
        summary=str(existing.get("summary") or "").strip(),
        memory=_distilled_payload_from_normalized(normalized),
    )
    return _append_distilled_history(
        platform=platform,
        user_id=user_id,
        record=record,
        entry=_make_distilled_history_entry(
            action="draft_saved",
            actor=str(updated_by or "distiller").strip() or "distiller",
            summary=str(normalized["draft"].get("summary") or "").strip(),
            field_names=(profile or {}).keys(),
        ),
    )


def publish_user_distilled_profile_draft(
    *,
    platform: str,
    user_id: str,
    published_by: str = "",
    actor_user_id: str = "",
    allow_cross_user: bool = False,
) -> Dict[str, Any]:
    _validate_distilled_profile_mutation(
        platform=platform,
        user_id=user_id,
        actor_user_id=actor_user_id or published_by,
        allow_cross_user=allow_cross_user,
        action="publish",
    )
    existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
    normalized = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
    draft = normalized.get("draft") if isinstance(normalized, dict) else None
    if not isinstance(draft, dict) or not draft.get("profile"):
        raise ValueError("distilled draft is empty")
    record = upsert_user_distilled_profile(
        platform=platform,
        user_id=user_id,
        summary=str(draft.get("summary") or "").strip(),
        profile=dict(draft.get("profile") or {}),
        evidence=dict(draft.get("evidence") or {}),
        sources=dict(draft.get("sources") or {}),
        manual_overrides=dict(normalized.get("manual_overrides") or {}),
        locked_fields=list(normalized.get("locked_fields") or []),
        updated_by=str(published_by or "profile_publish").strip() or "profile_publish",
        governance=_build_distilled_governance(
            existing_memory=existing.get("memory") if isinstance(existing, dict) else None,
            platform=platform,
            user_id=user_id,
            action="published",
            actor=str(published_by or actor_user_id or "profile_publish").strip() or "profile_publish",
            allow_cross_user=allow_cross_user,
        ),
    )
    published = _append_distilled_history(
        platform=platform,
        user_id=user_id,
        record=record,
        entry=_make_distilled_history_entry(
            action="published",
            actor=str(published_by or "profile_publish").strip() or "profile_publish",
            summary=str(draft.get("summary") or "").strip(),
            field_names=(draft.get("profile") or {}).keys() if isinstance(draft.get("profile"), dict) else [],
        ),
    )
    return discard_user_distilled_profile_draft(
        platform=platform,
        user_id=user_id,
        discarded_by=str(published_by or "profile_publish").strip() or "profile_publish",
        actor_user_id=actor_user_id,
        allow_cross_user=allow_cross_user,
        update_governance=False,
    ) if isinstance(published, dict) else published


def discard_user_distilled_profile_draft(
    *,
    platform: str,
    user_id: str,
    discarded_by: str = "",
    actor_user_id: str = "",
    allow_cross_user: bool = False,
    update_governance: bool = True,
) -> Dict[str, Any]:
    _validate_distilled_profile_mutation(
        platform=platform,
        user_id=user_id,
        actor_user_id=actor_user_id or discarded_by,
        allow_cross_user=allow_cross_user,
        action="discard",
    )
    existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
    normalized = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
    had_draft = bool((normalized.get("draft") or {}).get("profile") or (normalized.get("draft") or {}).get("summary"))
    if update_governance:
        normalized["governance"] = _build_distilled_governance(
            existing_memory=existing.get("memory") if isinstance(existing, dict) else None,
            platform=platform,
            user_id=user_id,
            action="draft_discarded",
            actor=str(discarded_by or actor_user_id or "profile_discard").strip() or "profile_discard",
            allow_cross_user=allow_cross_user,
        )
    normalized["draft"] = {
        "summary": "",
        "profile": {},
        "evidence": {},
        "sources": {},
        "generated_at_unix": _now(),
        "updated_by": str(discarded_by or "profile_discard").strip() or "profile_discard",
    }
    record = upsert_user_memory(
        platform=platform,
        user_id=user_id,
        scope="distilled",
        summary=str(existing.get("summary") or "").strip(),
        memory=_distilled_payload_from_normalized(normalized),
    )
    if not had_draft:
        return record
    return _append_distilled_history(
        platform=platform,
        user_id=user_id,
        record=record,
        entry=_make_distilled_history_entry(
            action="draft_discarded",
            actor=str(discarded_by or "profile_discard").strip() or "profile_discard",
        ),
    )


def set_user_distilled_profile_overrides(
    *,
    platform: str,
    user_id: str,
    overrides: Optional[Dict[str, Any]] = None,
    locked_fields: Optional[Iterable[str]] = None,
    actor_user_id: str = "",
    allow_cross_user: bool = False,
) -> Dict[str, Any]:
    _validate_distilled_profile_mutation(
        platform=platform,
        user_id=user_id,
        actor_user_id=actor_user_id or "manual_override",
        allow_cross_user=allow_cross_user,
        action="override",
    )
    existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
    normalized = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
    final_manual = dict(normalized.get("manual_overrides") or {})
    final_manual.update({str(key).strip(): value for key, value in (overrides or {}).items() if str(key).strip()})
    final_locked = set(normalized.get("locked_fields") or [])
    if locked_fields is not None:
        final_locked.update({str(item).strip() for item in locked_fields if str(item).strip()})
    record = upsert_user_distilled_profile(
        platform=platform,
        user_id=user_id,
        summary="",
        profile=dict(normalized.get("profile") or {}),
        evidence=dict(normalized.get("evidence") or {}),
        sources=dict(normalized.get("sources") or {}),
        manual_overrides=final_manual,
        locked_fields=sorted(final_locked),
        updated_by="manual_override",
        governance=_build_distilled_governance(
            existing_memory=existing.get("memory") if isinstance(existing, dict) else None,
            platform=platform,
            user_id=user_id,
            action="override_set",
            actor=str(actor_user_id or "manual_override").strip() or "manual_override",
            allow_cross_user=allow_cross_user,
        ),
    )
    return _append_distilled_history(
        platform=platform,
        user_id=user_id,
        record=record,
        entry=_make_distilled_history_entry(
            action="override_set",
            actor="manual_override",
            field_names=(overrides or {}).keys(),
        ),
    )


def clear_user_distilled_profile_override(
    *,
    platform: str,
    user_id: str,
    field: str,
    unlock: bool = True,
    actor_user_id: str = "",
    allow_cross_user: bool = False,
) -> Dict[str, Any]:
    _validate_distilled_profile_mutation(
        platform=platform,
        user_id=user_id,
        actor_user_id=actor_user_id or "manual_override",
        allow_cross_user=allow_cross_user,
        action="unlock" if unlock else "override_clear",
    )
    normalized_field = str(field or "").strip()
    existing = get_user_distilled_profile(platform=platform, user_id=user_id) or {}
    normalized = _normalize_distilled_memory(existing.get("memory") if isinstance(existing, dict) else None)
    final_manual = dict(normalized.get("manual_overrides") or {})
    final_manual.pop(normalized_field, None)
    final_locked = set(normalized.get("locked_fields") or [])
    if unlock:
        final_locked.discard(normalized_field)
    record = upsert_user_distilled_profile(
        platform=platform,
        user_id=user_id,
        summary="",
        profile=dict(normalized.get("profile") or {}),
        evidence=dict(normalized.get("evidence") or {}),
        sources=dict(normalized.get("sources") or {}),
        manual_overrides=final_manual,
        locked_fields=sorted(final_locked),
        updated_by="manual_override",
        governance=_build_distilled_governance(
            existing_memory=existing.get("memory") if isinstance(existing, dict) else None,
            platform=platform,
            user_id=user_id,
            action="override_cleared",
            actor=str(actor_user_id or "manual_override").strip() or "manual_override",
            allow_cross_user=allow_cross_user,
        ),
    )
    return _append_distilled_history(
        platform=platform,
        user_id=user_id,
        record=record,
        entry=_make_distilled_history_entry(
            action="override_cleared",
            actor="manual_override",
            field_names=[normalized_field],
        ),
    )


def list_user_distilled_profile_history(*, platform: str, user_id: str, limit: int = 10) -> list[Dict[str, Any]]:
    record = get_user_distilled_profile(platform=platform, user_id=user_id)
    if not isinstance(record, dict):
        return []
    normalized = _normalize_distilled_memory(record.get("memory") if isinstance(record, dict) else None)
    history = normalized.get("history") if isinstance(normalized, dict) else None
    if not isinstance(history, list):
        return []
    return [dict(item) for item in history[: max(1, int(limit or 10))] if isinstance(item, dict)]


def create_task(
    *,
    title: str,
    goal: str = "",
    owner_user_id: str = "",
    source_platform: str = "",
    source_chat_id: str = "",
    source_thread_id: str = "",
    source_session_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
    participant_user_ids: Optional[Iterable[str]] = None,
) -> Dict[str, Any]:
    task_id = _new_task_id()
    now = _now()
    participant_keys: set[str] = set()
    if owner_user_id and source_platform:
        owner_user = get_user(platform=source_platform, user_id=owner_user_id)
        if owner_user:
            participant_keys.add(str(owner_user.get("user_key") or "").strip())
    for participant in participant_user_ids or []:
        participant_id = str(participant or "").strip()
        if not participant_id or not source_platform:
            continue
        participant_user = get_user(platform=source_platform, user_id=participant_id)
        if participant_user:
            participant_keys.add(str(participant_user.get("user_key") or "").strip())
    record = {
        "task_id": task_id,
        "title": str(title or goal or "").strip()[:160],
        "goal": str(goal or "").strip(),
        "status": "open",
        "owner_user_id": str(owner_user_id or "").strip(),
        "source_platform": str(source_platform or "").strip().lower(),
        "source_chat_id": str(source_chat_id or "").strip(),
        "source_thread_id": str(source_thread_id or "").strip(),
        "source_session_id": str(source_session_id or "").strip(),
        "metadata": metadata or {},
        "created_at_unix": now,
        "updated_at_unix": now,
        "started_at_unix": None,
        "finished_at_unix": None,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO tasks(
                task_id, title, goal, status, owner_user_id, source_platform, source_chat_id,
                source_thread_id, source_session_id, metadata_json, created_at_unix,
                updated_at_unix, started_at_unix, finished_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["task_id"],
                record["title"],
                record["goal"],
                record["status"],
                record["owner_user_id"],
                record["source_platform"],
                record["source_chat_id"],
                record["source_thread_id"],
                record["source_session_id"],
                _json(record["metadata"]),
                now,
                now,
                None,
                None,
            ),
        )
        for user_key in {item for item in participant_keys if item}:
            conn.execute(
                """
                INSERT OR IGNORE INTO task_participants(task_id, user_key, role, added_at_unix)
                VALUES(?, ?, ?, ?)
                """,
                (task_id, user_key, "participant", now),
            )
        conn.commit()
    return get_task(task_id) or {}


def get_task(task_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM tasks WHERE task_id=?", (task_id,)).fetchone()
        if not row:
            return None
        participants = conn.execute(
            """
            SELECT p.user_key, p.role, p.added_at_unix, u.platform, u.user_id, u.display_name
            FROM task_participants p
            LEFT JOIN users u ON u.user_key = p.user_key
            WHERE p.task_id=?
            ORDER BY p.added_at_unix ASC
            """,
            (task_id,),
        ).fetchall()
    return {
        "task_id": row["task_id"],
        "title": row["title"],
        "goal": row["goal"],
        "status": row["status"],
        "owner_user_id": row["owner_user_id"],
        "source_platform": row["source_platform"],
        "source_chat_id": row["source_chat_id"],
        "source_thread_id": row["source_thread_id"],
        "source_session_id": row["source_session_id"],
        "metadata": _safe_json_loads(row["metadata_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
        "started_at_unix": row["started_at_unix"],
        "finished_at_unix": row["finished_at_unix"],
        "participants": [
            {
                "user_key": item["user_key"],
                "role": item["role"],
                "added_at_unix": item["added_at_unix"],
                "platform": item["platform"],
                "user_id": item["user_id"],
                "display_name": item["display_name"],
            }
            for item in participants
        ],
    }


def list_tasks(status: str = "", limit: int = 20) -> list[Dict[str, Any]]:
    query = "SELECT * FROM tasks"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "task_id": row["task_id"],
            "title": row["title"],
            "goal": row["goal"],
            "status": row["status"],
            "owner_user_id": row["owner_user_id"],
            "source_platform": row["source_platform"],
            "source_chat_id": row["source_chat_id"],
            "source_thread_id": row["source_thread_id"],
            "source_session_id": row["source_session_id"],
            "metadata": _safe_json_loads(row["metadata_json"], {}),
            "created_at_unix": row["created_at_unix"],
            "updated_at_unix": row["updated_at_unix"],
            "started_at_unix": row["started_at_unix"],
            "finished_at_unix": row["finished_at_unix"],
        }
        for row in rows
    ]


def update_task(task_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    current = get_task(task_id)
    if not current:
        return None
    allowed = {
        "title",
        "goal",
        "status",
        "owner_user_id",
        "source_platform",
        "source_chat_id",
        "source_thread_id",
        "source_session_id",
        "metadata",
        "started_at_unix",
        "finished_at_unix",
    }
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return current

    normalized_status = str(updates.get("status") or "").strip().lower()
    if normalized_status:
        updates["status"] = normalized_status
        if normalized_status in {"running", "queued", "pending_approval", "blocked", "paused"} and not current.get("started_at_unix"):
            updates["started_at_unix"] = _now()
        if normalized_status in {"completed", "failed", "cancelled"} and not current.get("finished_at_unix"):
            updates["finished_at_unix"] = _now()

    assignments: list[str] = []
    values: list[Any] = []
    for key, value in updates.items():
        column = "metadata_json" if key == "metadata" else key
        assignments.append(f"{column}=?")
        if key == "metadata":
            values.append(_json(value if isinstance(value, dict) else {}))
        elif key in {"started_at_unix", "finished_at_unix"}:
            values.append(value)
        else:
            values.append(str(value))
    assignments.append("updated_at_unix=?")
    values.append(_now())
    values.append(task_id)
    with connect() as conn:
        conn.execute(f"UPDATE tasks SET {', '.join(assignments)} WHERE task_id=?", values)
        conn.commit()
    return get_task(task_id)


def list_tasks_for_user(
    *,
    platform: str,
    user_id: str,
    since_unix: int = 0,
    limit: int = 20,
) -> list[Dict[str, Any]]:
    if not str(platform or "").strip() or not str(user_id or "").strip():
        return []
    query = "SELECT * FROM tasks WHERE source_platform=? AND owner_user_id=?"
    params: list[Any] = [str(platform or "").strip().lower(), str(user_id or "").strip()]
    if since_unix > 0:
        query += " AND COALESCE(updated_at_unix, created_at_unix, 0) >= ?"
        params.append(int(since_unix))
    query += " ORDER BY COALESCE(updated_at_unix, created_at_unix, 0) DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [
        {
            "task_id": row["task_id"],
            "title": row["title"],
            "goal": row["goal"],
            "status": row["status"],
            "owner_user_id": row["owner_user_id"],
            "source_platform": row["source_platform"],
            "source_chat_id": row["source_chat_id"],
            "source_thread_id": row["source_thread_id"],
            "source_session_id": row["source_session_id"],
            "metadata": _safe_json_loads(row["metadata_json"], {}),
            "created_at_unix": row["created_at_unix"],
            "updated_at_unix": row["updated_at_unix"],
            "started_at_unix": row["started_at_unix"],
            "finished_at_unix": row["finished_at_unix"],
        }
        for row in rows
    ]


def _find_latest_task_for_source(
    *,
    source_platform: str,
    source_chat_id: str,
    source_thread_id: str = "",
    source_session_id: str = "",
) -> Optional[Dict[str, Any]]:
    normalized_platform = str(source_platform or "").strip().lower()
    normalized_chat_id = str(source_chat_id or "").strip()
    normalized_thread_id = str(source_thread_id or "").strip()
    normalized_session_id = str(source_session_id or "").strip()
    if not normalized_platform or not normalized_chat_id:
        return None
    query = """
        SELECT task_id
        FROM tasks
        WHERE source_platform=?
          AND source_chat_id=?
          AND source_thread_id=?
          AND status NOT IN (?, ?, ?)
    """
    params: list[Any] = [
        normalized_platform,
        normalized_chat_id,
        normalized_thread_id,
        "completed",
        "failed",
        "cancelled",
    ]
    if normalized_session_id:
        query += " AND (source_session_id=? OR source_session_id='')"
        params.append(normalized_session_id)
    query += " ORDER BY updated_at_unix DESC, created_at_unix DESC LIMIT 1"
    with connect() as conn:
        row = conn.execute(query, params).fetchone()
    if not row:
        return None
    return get_task(str(row["task_id"] or "").strip())


def _ensure_task_for_origin(
    *,
    task_id: str = "",
    title: str = "",
    goal: str = "",
    actor_user_id: str = "",
    session_id: str = "",
    origin: Optional[Dict[str, Any]] = None,
    metadata: Optional[Dict[str, Any]] = None,
) -> str:
    explicit = _normalize_task_id(task_id)
    if explicit:
        return explicit
    origin = origin if isinstance(origin, dict) else {}
    platform = str(origin.get("platform") or "").strip().lower()
    chat_id = str(origin.get("chat_id") or "").strip()
    thread_id = str(origin.get("thread_id") or "").strip()
    if not platform or not chat_id:
        return ""

    bound = get_channel_task(platform=platform, chat_id=chat_id, thread_id=thread_id)
    bound_task = bound.get("task") if isinstance(bound, dict) else None
    bound_task_id = _normalize_task_id((bound_task or {}).get("task_id") or "")
    if bound_task_id:
        return bound_task_id

    existing = _find_latest_task_for_source(
        source_platform=platform,
        source_chat_id=chat_id,
        source_thread_id=thread_id,
        source_session_id=session_id,
    )
    if existing:
        existing_task_id = _normalize_task_id(existing.get("task_id") or "")
        if existing_task_id:
            upsert_channel(
                platform=platform,
                chat_id=chat_id,
                thread_id=thread_id,
                task_id=existing_task_id,
                chat_name=str(origin.get("chat_name") or "").strip(),
                chat_type=str(origin.get("chat_type") or "").strip(),
            )
            return existing_task_id

    created = create_task(
        title=str(title or goal or "").strip(),
        goal=str(goal or title or "").strip(),
        owner_user_id=str(actor_user_id or "").strip(),
        source_platform=platform,
        source_chat_id=chat_id,
        source_thread_id=thread_id,
        source_session_id=str(session_id or "").strip(),
        metadata={
            "auto_materialized": True,
            "origin": origin,
            **(metadata or {}),
        },
    )
    created_task_id = _normalize_task_id(created.get("task_id") or "")
    if created_task_id:
        upsert_channel(
            platform=platform,
            chat_id=chat_id,
            thread_id=thread_id,
            task_id=created_task_id,
            chat_name=str(origin.get("chat_name") or "").strip(),
            chat_type=str(origin.get("chat_type") or "").strip(),
        )
    return created_task_id


def bind_channel_task(*, platform: str, chat_id: str, thread_id: str = "", task_id: str) -> Dict[str, Any]:
    current = get_channel(platform=platform, chat_id=chat_id, thread_id=thread_id)
    task = get_task(task_id)
    if not current:
        raise ValueError("channel not found")
    if not task:
        raise ValueError("task not found")
    return upsert_channel(
        platform=platform,
        chat_id=chat_id,
        thread_id=thread_id,
        task_id=task_id,
        chat_name=str(current.get("chat_name") or ""),
        chat_type=str(current.get("chat_type") or ""),
        worker_role=str(current.get("worker_role") or ""),
        allow_free_chat=bool(current.get("allow_free_chat")),
        policy=current.get("policy") if isinstance(current.get("policy"), dict) else {},
    )


def unbind_channel_task(*, platform: str, chat_id: str, thread_id: str = "") -> Dict[str, Any]:
    current = get_channel(platform=platform, chat_id=chat_id, thread_id=thread_id)
    if not current:
        raise ValueError("channel not found")
    return upsert_channel(
        platform=platform,
        chat_id=chat_id,
        thread_id=thread_id,
        task_id="",
        chat_name=str(current.get("chat_name") or ""),
        chat_type=str(current.get("chat_type") or ""),
        worker_role=str(current.get("worker_role") or ""),
        allow_free_chat=bool(current.get("allow_free_chat")),
        policy=current.get("policy") if isinstance(current.get("policy"), dict) else {},
    )


def get_channel_task(*, platform: str, chat_id: str, thread_id: str = "") -> Optional[Dict[str, Any]]:
    channel = get_channel(platform=platform, chat_id=chat_id, thread_id=thread_id)
    if not channel:
        return None
    task_id = _normalize_task_id(channel.get("task_id") or "")
    if not task_id:
        return {"channel": channel, "task": None}
    return {"channel": channel, "task": get_task(task_id)}


def upsert_task_memory(*, task_id: str, scope: str = "shared", summary: str = "", memory: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    task = get_task(task_id)
    if not task:
        raise ValueError("task not found")
    normalized_scope = str(scope or "shared").strip().lower() or "shared"
    _validate_task_memory_write(task_id=task_id, scope=normalized_scope)
    now = _now()
    memory_key = _memory_key("task-memory", task_id, normalized_scope)
    governed_memory = _attach_memory_governance(
        payload=memory,
        memory_kind="task",
        scope=normalized_scope,
        owner_ref=f"task:{task_id}",
        governance_ref=f"task-memory:{task_id}:{normalized_scope}",
    )
    record = {
        "memory_key": memory_key,
        "task_id": task_id,
        "scope": normalized_scope,
        "summary": str(summary or "").strip(),
        "memory": governed_memory,
        "created_at_unix": now,
        "updated_at_unix": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO task_memory(memory_key, task_id, scope, summary, memory_json, created_at_unix, updated_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(memory_key) DO UPDATE SET
                summary=excluded.summary,
                memory_json=excluded.memory_json,
                updated_at_unix=excluded.updated_at_unix
            """,
            (
                record["memory_key"],
                record["task_id"],
                record["scope"],
                record["summary"],
                _json(record["memory"]),
                now,
                now,
            ),
        )
        conn.commit()
    return get_task_memory(task_id=task_id, scope=normalized_scope) or {}


def get_task_memory(*, task_id: str, scope: str = "shared") -> Optional[Dict[str, Any]]:
    memory_key = _memory_key("task-memory", task_id, scope)
    with connect() as conn:
        row = conn.execute("SELECT * FROM task_memory WHERE memory_key=?", (memory_key,)).fetchone()
    if not row:
        return None
    return {
        "memory_key": row["memory_key"],
        "task_id": row["task_id"],
        "scope": row["scope"],
        "summary": row["summary"],
        "memory": _safe_json_loads(row["memory_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
    }


def evaluate_capability_access(
    *,
    capability: str,
    actor_user_id: str = "",
    platform: str = "",
    chat_id: str = "",
    thread_id: str = "",
    action: str = "run",
) -> Dict[str, Any]:
    cap = get_capability(capability)
    if not cap:
        return {"allowed": False, "approval_required": False, "reason": f"Unknown capability: {capability}"}

    user = get_user(actor_user_id, platform=platform, user_id=actor_user_id) if actor_user_id else None
    channel = get_channel(platform=platform, chat_id=chat_id, thread_id=thread_id) if platform and chat_id else None
    role = str((user or {}).get("role") or ("system" if not actor_user_id else "user")).lower()
    permissions = (user or {}).get("permissions") or {}
    channel_policy = (channel or {}).get("policy") or {}
    cap_name = cap["name"]
    category = cap["category"]

    if role == "owner":
        return {
            "allowed": True,
            "approval_required": False,
            "reason": "Allowed as global owner.",
            "role": role,
            "risk_level": str(cap.get("risk_level") or "medium").lower(),
            "capability": cap,
            "user": user,
            "channel": channel,
        }

    for policy in (permissions, channel_policy):
        denied = set(policy.get("deny_capabilities") or []) | set(policy.get("deny_categories") or [])
        if cap_name in denied or category in denied or "*" in denied:
            return {"allowed": False, "approval_required": False, "reason": "Denied by explicit policy.", "role": role, "capability": cap}
        allowed = set(policy.get("allow_capabilities") or [])
        allowed_categories = set(policy.get("allow_categories") or [])
        if allowed and cap_name not in allowed and "*" not in allowed and category not in allowed_categories:
            return {"allowed": False, "approval_required": False, "reason": "Not included in allow policy.", "role": role, "capability": cap}

    if role == "blocked":
        return {"allowed": False, "approval_required": False, "reason": "User role is blocked.", "role": role, "capability": cap}
    if role == "viewer" and action not in {"read", "status"}:
        return {"allowed": False, "approval_required": False, "reason": "Viewer role cannot execute capabilities.", "role": role, "capability": cap}

    risk = str(cap.get("risk_level") or "medium").lower()
    risk_rank = RISK_RANK.get(risk, 2)
    max_auto = ROLE_MAX_AUTO_RISK.get(role, 1)
    approval_required = bool(cap.get("default_approval_required")) or risk_rank > max_auto
    approval_mode = _business_approval_mode()
    if channel_policy.get("require_approval") is True:
        approval_required = True
    if approval_mode == "off":
        approval_required = False
    elif str(platform or "").strip().lower() == "feishu" and _feishu_approvals_disabled():
        approval_required = False
    elif permissions.get("bypass_approval") is True and role in {"owner", "admin"}:
        approval_required = False
    elif (
        approval_mode == "owner_control_surface"
        and
        role in {"owner", "admin"}
        and channel_policy.get("require_approval") is not True
        and str(platform or "").strip().lower() in CONTROL_SURFACE_APPROVAL_BYPASS_PLATFORMS
        and str((channel or {}).get("chat_type") or "").strip().lower() in PRIVATE_CHAT_TYPES
    ):
        approval_required = False

    reason = "Allowed; approval required." if approval_required else "Allowed."
    if approval_mode == "off":
        reason = "Allowed; business approval rollout is off."
    elif (
        not approval_required
        and str(platform or "").strip().lower() == "feishu"
        and _feishu_approvals_disabled()
    ):
        reason = "Allowed on Feishu; approvals are disabled by platform policy."
    elif not approval_required and role in {"owner", "admin"}:
        normalized_platform = str(platform or "").strip().lower()
        normalized_chat_type = str((channel or {}).get("chat_type") or "").strip().lower()
        if normalized_platform in CONTROL_SURFACE_APPROVAL_BYPASS_PLATFORMS and normalized_chat_type in PRIVATE_CHAT_TYPES:
            reason = "Allowed on private control surface; business approval bypassed for owner/admin."
        elif permissions.get("bypass_approval") is True:
            reason = "Allowed; approval bypassed by owner/admin policy."

    return {
        "allowed": True,
        "approval_required": approval_required,
        "reason": reason,
        "role": role,
        "risk_level": risk,
        "capability": cap,
        "user": user,
        "channel": channel,
    }


def _resolve_task_id(task_id: str = "", origin: Optional[Dict[str, Any]] = None) -> str:
    explicit = _normalize_task_id(task_id)
    if explicit:
        return explicit
    origin = origin or {}
    platform = str(origin.get("platform") or "").strip().lower()
    chat_id = str(origin.get("chat_id") or "").strip()
    thread_id = str(origin.get("thread_id") or "").strip()
    if not platform or not chat_id:
        return ""
    channel = get_channel(platform=platform, chat_id=chat_id, thread_id=thread_id)
    return _normalize_task_id((channel or {}).get("task_id") or "")


def create_approval_request(
    *,
    kind: str,
    target_id: str,
    requested_by: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    approval_id = "approval-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = _now()
    record = {
        "approval_id": approval_id,
        "kind": str(kind or "").strip(),
        "target_id": str(target_id or "").strip(),
        "status": "pending",
        "requested_by": str(requested_by or "").strip(),
        "approved_by": "",
        "payload": payload or {},
        "created_at_unix": now,
        "decided_at_unix": None,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO approvals(approval_id, kind, target_id, status, requested_by, approved_by, payload_json, created_at_unix, decided_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                approval_id,
                record["kind"],
                record["target_id"],
                record["status"],
                record["requested_by"],
                record["approved_by"],
                _json(record["payload"]),
                now,
                None,
            ),
        )
        conn.commit()
    return record


def _approval_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "approval_id": row["approval_id"],
        "kind": row["kind"],
        "target_id": row["target_id"],
        "status": row["status"],
        "requested_by": row["requested_by"],
        "approved_by": row["approved_by"],
        "payload": _safe_json_loads(row["payload_json"], {}),
        "created_at_unix": row["created_at_unix"],
        "decided_at_unix": row["decided_at_unix"],
    }


def list_approvals(status: str = "pending", limit: int = 20) -> list[Dict[str, Any]]:
    query = "SELECT * FROM approvals"
    params: list[Any] = []
    if status:
        query += " WHERE status=?"
        params.append(status)
    query += " ORDER BY created_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_approval_from_row(row) for row in rows]


def decide_approval(approval_id: str, *, status: str, approved_by: str = "") -> Optional[Dict[str, Any]]:
    normalized = str(status or "").strip().lower()
    if normalized not in {"approved", "denied", "cancelled"}:
        raise ValueError("approval status must be approved, denied, or cancelled")
    with connect() as conn:
        row = conn.execute("SELECT * FROM approvals WHERE approval_id=?", (approval_id,)).fetchone()
        if not row:
            return None
        target_task_id = ""
        payload = _safe_json_loads(row["payload_json"], {})
        if isinstance(payload, dict):
            target_task_id = _normalize_task_id(payload.get("task_id") or "")
        conn.execute(
            "UPDATE approvals SET status=?, approved_by=?, decided_at_unix=? WHERE approval_id=?",
            (normalized, str(approved_by or "").strip(), _now(), approval_id),
        )
        if row["kind"] == "capability_run":
            if normalized == "approved":
                conn.execute(
                    """
                    UPDATE capability_runs
                    SET status='queued', current_focus='Approved; waiting for execution.',
                        next_step='Start execution and record progress.', approval_id=?, updated_at_unix=?
                    WHERE run_id=?
                    """,
                    (approval_id, _now(), row["target_id"]),
                )
            elif normalized in {"denied", "cancelled"}:
                blocker = "Approval denied." if normalized == "denied" else "Approval cancelled."
                result = (
                    "Capability run was denied before execution."
                    if normalized == "denied"
                    else "Capability run was cancelled before execution."
                )
                conn.execute(
                    """
                    UPDATE capability_runs
                    SET status='cancelled', blocker=?,
                        result=?, approval_id=?,
                        finished_at_unix=?, updated_at_unix=?
                    WHERE run_id=?
                    """,
                    (blocker, result, approval_id, _now(), _now(), row["target_id"]),
                )
        conn.commit()
    if not target_task_id and row["kind"] == "capability_run":
        run = get_capability_run(str(row["target_id"] or "").strip())
        target_task_id = _normalize_task_id((run or {}).get("task_id") or "")
    if target_task_id:
        try:
            from agent.task_panel_service import sync_task_control_state

            sync_task_control_state(target_task_id)
        except Exception:
            pass
    return next((item for item in list_approvals(status="", limit=200) if item["approval_id"] == approval_id), None)


def _background_job_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "job_id": row["job_id"],
        "trace_id": row["trace_id"],
        "task_id": row["task_id"],
        "title": row["title"],
        "prompt": row["prompt"],
        "status": row["status"],
        "priority": row["priority"],
        "tags": _safe_json_loads(row["tags_json"], []),
        "origin": _safe_json_loads(row["origin_json"], {}),
        "session_id": row["session_id"],
        "user_id": row["user_id"],
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
        "started_at_unix": row["started_at_unix"],
        "finished_at_unix": row["finished_at_unix"],
        "current_focus": row["current_focus"],
        "next_step": row["next_step"],
        "blocker": row["blocker"],
        "result": row["result"],
        "artifact_paths": _safe_json_loads(row["artifact_paths_json"], []),
        "job_dir": row["job_dir"],
        "events_path": row["events_path"],
        "executor": row["executor"],
        "delivery_status": row["delivery_status"],
        "delivery_error": row["delivery_error"],
        "delivery_target": row["delivery_target"],
        "delivered_at_unix": row["delivered_at_unix"],
    }


def upsert_background_job(record: Dict[str, Any]) -> None:
    job_id = str(record.get("job_id") or "").strip()
    if not job_id:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO background_jobs(
                job_id, trace_id, task_id, title, prompt, status, priority, tags_json, origin_json,
                session_id, user_id, created_at_unix, updated_at_unix, started_at_unix,
                finished_at_unix, current_focus, next_step, blocker, result,
                artifact_paths_json, job_dir, events_path, executor, delivery_status,
                delivery_error, delivery_target, delivered_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            ON CONFLICT(job_id) DO UPDATE SET
                trace_id=excluded.trace_id,
                task_id=excluded.task_id,
                title=excluded.title,
                prompt=excluded.prompt,
                status=excluded.status,
                priority=excluded.priority,
                tags_json=excluded.tags_json,
                origin_json=excluded.origin_json,
                session_id=excluded.session_id,
                user_id=excluded.user_id,
                created_at_unix=excluded.created_at_unix,
                updated_at_unix=excluded.updated_at_unix,
                started_at_unix=excluded.started_at_unix,
                finished_at_unix=excluded.finished_at_unix,
                current_focus=excluded.current_focus,
                next_step=excluded.next_step,
                blocker=excluded.blocker,
                result=excluded.result,
                artifact_paths_json=excluded.artifact_paths_json,
                job_dir=excluded.job_dir,
                events_path=excluded.events_path,
                executor=excluded.executor,
                delivery_status=excluded.delivery_status,
                delivery_error=excluded.delivery_error,
                delivery_target=excluded.delivery_target,
                delivered_at_unix=excluded.delivered_at_unix
            """,
            (
                job_id,
                _normalize_trace_id(record.get("trace_id")),
                str(record.get("task_id") or ""),
                str(record.get("title") or ""),
                str(record.get("prompt") or ""),
                str(record.get("status") or ""),
                str(record.get("priority") or ""),
                _json(record.get("tags") or []),
                _json(record.get("origin") or {}),
                str(record.get("session_id") or ""),
                str(record.get("user_id") or ""),
                record.get("created_at_unix"),
                record.get("updated_at_unix"),
                record.get("started_at_unix"),
                record.get("finished_at_unix"),
                str(record.get("current_focus") or ""),
                str(record.get("next_step") or ""),
                str(record.get("blocker") or ""),
                str(record.get("result") or ""),
                _json(record.get("artifact_paths") or []),
                str(record.get("job_dir") or ""),
                str(record.get("events_path") or ""),
                str(record.get("executor") or ""),
                str(record.get("delivery_status") or ""),
                str(record.get("delivery_error") or ""),
                str(record.get("delivery_target") or ""),
                record.get("delivered_at_unix"),
            ),
        )
        conn.commit()


def list_background_jobs_for_user(*, user_id: str, since_unix: int = 0, limit: int = 20) -> list[Dict[str, Any]]:
    if not str(user_id or "").strip():
        return []
    query = "SELECT * FROM background_jobs WHERE user_id=?"
    params: list[Any] = [str(user_id or "").strip()]
    if since_unix > 0:
        query += " AND COALESCE(updated_at_unix, created_at_unix, 0) >= ?"
        params.append(int(since_unix))
    query += " ORDER BY COALESCE(updated_at_unix, created_at_unix, 0) DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_background_job_from_row(row) for row in rows]


def insert_background_job_event(job_id: str, event: Dict[str, Any]) -> None:
    if not job_id:
        return
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO background_job_events(job_id, trace_id, timestamp_unix, timestamp, kind, status, message, payload_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                job_id,
                _normalize_trace_id(event.get("trace_id")),
                int(event.get("timestamp_unix") or _now()),
                str(event.get("timestamp") or ""),
                str(event.get("kind") or ""),
                str(event.get("status") or ""),
                str(event.get("message") or ""),
                _json(event),
            ),
        )
        conn.commit()


def _capability_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "capability_id": row["capability_id"],
        "name": row["name"],
        "description": row["description"],
        "category": row["category"],
        "risk_level": row["risk_level"],
        "default_approval_required": bool(row["default_approval_required"]),
        "enabled": bool(row["enabled"]),
        "config": json.loads(row["config_json"] or "{}"),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
    }


def _list_capability_runs_by_name(
    conn: sqlite3.Connection,
    *,
    capability_name: str,
    statuses: Iterable[str],
    limit: int = 10,
) -> list[sqlite3.Row]:
    normalized_statuses = [str(item).strip().lower() for item in statuses if str(item).strip()]
    if not normalized_statuses:
        return []
    placeholders = ", ".join("?" for _ in normalized_statuses)
    query = f"""
        SELECT r.*, c.name AS capability_name
        FROM capability_runs r
        JOIN capabilities c ON c.capability_id = r.capability_id
        WHERE c.name=? AND r.status IN ({placeholders})
        ORDER BY r.updated_at_unix DESC
        LIMIT ?
    """
    params: list[Any] = [capability_name, *normalized_statuses, max(1, int(limit or 10))]
    return conn.execute(query, params).fetchall()


def _is_recursive_ops_recovery_request(
    *,
    input_data: Optional[Dict[str, Any]],
    origin: Optional[Dict[str, Any]],
) -> bool:
    payload = input_data if isinstance(input_data, dict) else {}
    origin_payload = origin if isinstance(origin, dict) else {}
    triggering_capability = str(payload.get("triggering_capability") or origin_payload.get("triggering_capability") or "").strip().lower()
    parent_capability = str(payload.get("parent_capability") or origin_payload.get("parent_capability") or "").strip().lower()
    if triggering_capability == "ops_recovery" or parent_capability == "ops_recovery":
        return True
    parent_run_id = str(payload.get("parent_run_id") or origin_payload.get("parent_run_id") or "").strip()
    if parent_run_id:
        parent = get_capability_run(parent_run_id)
        if parent and str(parent.get("capability_name") or "").strip().lower() == "ops_recovery":
            return True
    return False


def _enforce_ops_recovery_guards(
    *,
    conn: sqlite3.Connection,
    cap: Dict[str, Any],
    decision: Dict[str, Any],
    actor_user_id: str,
    input_data: Optional[Dict[str, Any]],
    origin: Optional[Dict[str, Any]],
) -> None:
    if str(cap.get("name") or "").strip().lower() != "ops_recovery":
        return
    config = cap.get("config") if isinstance(cap.get("config"), dict) else {}
    role = str(decision.get("role") or "").strip().lower()
    if bool(config.get("owner_only")) and role != "owner":
        raise PermissionError("ops_recovery is owner-only.")
    if bool(config.get("forbid_recursive")) and _is_recursive_ops_recovery_request(input_data=input_data, origin=origin):
        raise RuntimeError("ops_recovery cannot trigger another ops_recovery run.")
    if bool(config.get("single_active")):
        active_rows = _list_capability_runs_by_name(conn, capability_name="ops_recovery", statuses=ACTIVE_RUN_STATUSES, limit=5)
        if active_rows:
            active_run_id = str(active_rows[0]["run_id"] or "").strip()
            raise RuntimeError(f"ops_recovery already has an active run: {active_run_id or 'unknown'}")
    cooldown_seconds = int(config.get("cooldown_seconds") or 0)
    if cooldown_seconds > 0:
        recent_rows = _list_capability_runs_by_name(conn, capability_name="ops_recovery", statuses=TERMINAL_RUN_STATUSES, limit=1)
        if recent_rows:
            last_finished = int(recent_rows[0]["finished_at_unix"] or recent_rows[0]["updated_at_unix"] or 0)
            age = _now() - last_finished
            if last_finished and age < cooldown_seconds:
                remaining = max(1, cooldown_seconds - age)
                raise RuntimeError(f"ops_recovery is in cooldown for another {remaining}s.")


def list_capabilities(*, enabled_only: bool = True, limit: int = 50) -> list[Dict[str, Any]]:
    query = "SELECT * FROM capabilities"
    params: list[Any] = []
    if enabled_only:
        query += " WHERE enabled=1"
    query += " ORDER BY category, name LIMIT ?"
    params.append(max(1, int(limit or 50)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_capability_from_row(row) for row in rows]


def get_capability(identifier: str) -> Optional[Dict[str, Any]]:
    ident = str(identifier or "").strip()
    if not ident:
        return None
    with connect() as conn:
        row = conn.execute(
            "SELECT * FROM capabilities WHERE capability_id=? OR name=?",
            (ident, ident),
        ).fetchone()
    return _capability_from_row(row) if row else None


def create_capability_run(
    *,
    capability: str,
    title: str,
    goal: str,
    origin: Optional[Dict[str, Any]] = None,
    actor_user_id: str = "",
    session_id: str = "",
    priority: str = "normal",
    input_data: Optional[Dict[str, Any]] = None,
    background_job_id: str = "",
    task_id: str = "",
) -> Dict[str, Any]:
    cap = get_capability(capability)
    if not cap:
        raise ValueError(f"Unknown capability: {capability}")
    run_id = "run-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = _now()
    origin = origin or {}
    input_data = input_data or {}
    resolved_task_id = _resolve_task_id(task_id, origin)
    if not resolved_task_id:
        resolved_task_id = _ensure_task_for_origin(
            task_id=task_id,
            title=title,
            goal=goal,
            actor_user_id=actor_user_id,
            session_id=session_id,
            origin=origin,
            metadata={
                "materialized_by": "create_capability_run",
                "capability": str(capability or "").strip(),
            },
        )
    trace_id = _normalize_trace_id(
        input_data.get("trace_id")
        if isinstance(input_data, dict)
        else ""
    )
    decision = evaluate_capability_access(
        capability=cap["name"],
        actor_user_id=actor_user_id,
        platform=str(origin.get("platform") or ""),
        chat_id=str(origin.get("chat_id") or ""),
        thread_id=str(origin.get("thread_id") or ""),
        action="run",
    )
    if not decision.get("allowed"):
        raise PermissionError(str(decision.get("reason") or "Capability access denied."))
    ops_lock_acquired = False
    try:
        if str(cap.get("name") or "").strip().lower() == "ops_recovery":
            ops_lock_acquired, _ = acquire_scoped_lock(
                "capability-ops-recovery",
                "global",
                metadata={
                    "actor_user_id": actor_user_id,
                    "platform": str(origin.get("platform") or ""),
                    "chat_id": str(origin.get("chat_id") or ""),
                },
            )
            if not ops_lock_acquired:
                raise RuntimeError("ops_recovery creation is already in progress.")
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            _enforce_ops_recovery_guards(
                conn=conn,
                cap=cap,
                decision=decision,
                actor_user_id=actor_user_id,
                input_data=input_data,
                origin=origin,
            )
    except Exception:
        if ops_lock_acquired:
            release_scoped_lock("capability-ops-recovery", "global")
        raise
    try:
        approval_id = ""
        status = "queued"
        current_focus = "Queued; waiting for execution."
        next_step = "Plan the first concrete step and record progress."
        if decision.get("approval_required"):
            approval = create_approval_request(
                kind="capability_run",
                target_id=run_id,
                requested_by=actor_user_id,
                payload={
                    "capability": cap["name"],
                    "title": title,
                    "goal": goal,
                    "task_id": resolved_task_id,
                    "origin": origin,
                    "decision": {
                        "role": decision.get("role"),
                        "risk_level": decision.get("risk_level"),
                        "reason": decision.get("reason"),
                    },
                },
            )
            approval_id = approval["approval_id"]
            status = "pending_approval"
            current_focus = "Waiting for approval before execution."
            next_step = "Owner/admin should approve or deny the pending approval request."
        record = {
            "run_id": run_id,
            "trace_id": trace_id,
            "capability_id": cap["capability_id"],
            "capability_name": cap["name"],
            "task_id": resolved_task_id,
            "title": title.strip() or goal.strip()[:80],
            "goal": goal.strip(),
            "status": status,
            "priority": priority.strip() or "normal",
            "origin": origin,
            "actor_user_id": actor_user_id.strip(),
            "session_id": session_id.strip(),
            "background_job_id": background_job_id.strip(),
            "approval_id": approval_id,
            "current_focus": current_focus,
            "next_step": next_step,
            "blocker": "",
            "result": "",
            "input": input_data,
            "output": {},
            "created_at_unix": now,
            "updated_at_unix": now,
            "started_at_unix": None,
            "finished_at_unix": None,
        }
        with connect() as conn:
            conn.execute("BEGIN IMMEDIATE")
            conn.execute(
                """
                INSERT INTO capability_runs(
                    run_id, trace_id, capability_id, task_id, title, goal, status, priority,
                    origin_json, actor_user_id, session_id, background_job_id,
                    approval_id, current_focus, next_step, blocker, result,
                    input_json, output_json, created_at_unix, updated_at_unix,
                    started_at_unix, finished_at_unix
                )
                VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
                """,
                (
                    record["run_id"],
                    record["trace_id"],
                    record["capability_id"],
                    record["task_id"],
                    record["title"],
                    record["goal"],
                    record["status"],
                    record["priority"],
                    _json(record["origin"]),
                    record["actor_user_id"],
                    record["session_id"],
                    record["background_job_id"],
                    record["approval_id"],
                    record["current_focus"],
                    record["next_step"],
                    record["blocker"],
                    record["result"],
                    _json(record["input"]),
                    _json(record["output"]),
                    now,
                    now,
                    None,
                    None,
                ),
            )
            conn.commit()
        write_audit_log(
            action="create_capability_run",
            actor_user_id=actor_user_id,
            platform=str((origin or {}).get("platform") or ""),
            chat_id=str((origin or {}).get("chat_id") or ""),
            target_type="capability_run",
            target_id=run_id,
            summary=record["title"],
            payload={
                "capability": cap["name"],
                "goal": goal,
                "permission_decision": {
                    "allowed": decision.get("allowed"),
                    "approval_required": decision.get("approval_required"),
                    "role": decision.get("role"),
                    "risk_level": decision.get("risk_level"),
                    "reason": decision.get("reason"),
                },
                "approval_id": approval_id,
                "trace_id": trace_id,
                "task_id": resolved_task_id,
            },
        )
        if resolved_task_id:
            try:
                from agent.task_panel_service import sync_task_control_state

                sync_task_control_state(resolved_task_id)
            except Exception:
                pass
        return record
    finally:
        if ops_lock_acquired:
            release_scoped_lock("capability-ops-recovery", "global")


def _run_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "run_id": row["run_id"],
        "trace_id": row["trace_id"],
        "capability_id": row["capability_id"],
        "capability_name": row["capability_name"],
        "task_id": row["task_id"],
        "title": row["title"],
        "goal": row["goal"],
        "status": row["status"],
        "priority": row["priority"],
        "origin": json.loads(row["origin_json"] or "{}"),
        "actor_user_id": row["actor_user_id"],
        "session_id": row["session_id"],
        "background_job_id": row["background_job_id"],
        "approval_id": row["approval_id"],
        "current_focus": row["current_focus"],
        "next_step": row["next_step"],
        "blocker": row["blocker"],
        "result": row["result"],
        "input": json.loads(row["input_json"] or "{}"),
        "output": json.loads(row["output_json"] or "{}"),
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
        "started_at_unix": row["started_at_unix"],
        "finished_at_unix": row["finished_at_unix"],
    }


def get_capability_run(run_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute(
            """
            SELECT r.*, c.name AS capability_name
            FROM capability_runs r
            JOIN capabilities c ON c.capability_id = r.capability_id
            WHERE r.run_id=?
            """,
            (run_id,),
        ).fetchone()
    return _run_from_row(row) if row else None


def list_capability_runs(status: str = "", capability: str = "", task_id: str = "", limit: int = 20) -> list[Dict[str, Any]]:
    query = """
        SELECT r.*, c.name AS capability_name
        FROM capability_runs r
        JOIN capabilities c ON c.capability_id = r.capability_id
    """
    clauses: list[str] = []
    params: list[Any] = []
    if status:
        clauses.append("r.status=?")
        params.append(status)
    if capability:
        clauses.append("(c.name=? OR c.capability_id=?)")
        params.extend([capability, capability])
    if task_id:
        clauses.append("r.task_id=?")
        params.append(task_id)
    if clauses:
        query += " WHERE " + " AND ".join(clauses)
    query += " ORDER BY r.updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_run_from_row(row) for row in rows]


def list_capability_runs_for_user(*, actor_user_id: str, since_unix: int = 0, limit: int = 20) -> list[Dict[str, Any]]:
    if not str(actor_user_id or "").strip():
        return []
    query = """
        SELECT r.*, c.name AS capability_name
        FROM capability_runs r
        JOIN capabilities c ON c.capability_id = r.capability_id
        WHERE r.actor_user_id=?
    """
    params: list[Any] = [str(actor_user_id or "").strip()]
    if since_unix > 0:
        query += " AND COALESCE(r.updated_at_unix, r.created_at_unix, 0) >= ?"
        params.append(int(since_unix))
    query += " ORDER BY COALESCE(r.updated_at_unix, r.created_at_unix, 0) DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_run_from_row(row) for row in rows]


def update_capability_run(run_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    current = get_capability_run(run_id)
    if not current:
        return None
    allowed = {
        "status",
        "task_id",
        "priority",
        "background_job_id",
        "approval_id",
        "current_focus",
        "next_step",
        "blocker",
        "result",
        "output",
        "started_at_unix",
        "finished_at_unix",
    }
    updates = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return current
    status = str(updates.get("status") or "").strip().lower()
    if status == "running" and not current.get("started_at_unix"):
        updates["started_at_unix"] = _now()
    if status in {"completed", "failed", "cancelled"} and not current.get("finished_at_unix"):
        updates["finished_at_unix"] = _now()

    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        column = "output_json" if key == "output" else key
        assignments.append(f"{column}=?")
        values.append(_json(value) if key == "output" else str(value) if value is not None and key not in {"started_at_unix", "finished_at_unix"} else value)
    assignments.append("updated_at_unix=?")
    values.append(_now())
    values.append(run_id)
    with connect() as conn:
        conn.execute(f"UPDATE capability_runs SET {', '.join(assignments)} WHERE run_id=?", values)
        conn.commit()
    updated = get_capability_run(run_id)
    resolved_task_id = str((updated or {}).get("task_id") or current.get("task_id") or "").strip()
    if resolved_task_id:
        try:
            from agent.task_panel_service import sync_task_control_state

            sync_task_control_state(resolved_task_id)
        except Exception:
            pass
    return updated


def add_capability_step(
    *,
    run_id: str,
    name: str,
    status: str = "pending",
    step_order: int = 0,
    current_focus: str = "",
    evidence: str = "",
    blocker: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run = get_capability_run(run_id)
    if not run:
        raise ValueError(f"Unknown capability run: {run_id}")
    step_id = "step-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = _now()
    record = {
        "step_id": step_id,
        "run_id": run_id,
        "step_order": int(step_order or 0),
        "name": name.strip(),
        "status": status.strip() or "pending",
        "current_focus": current_focus.strip(),
        "evidence": evidence.strip(),
        "blocker": blocker.strip(),
        "started_at_unix": now if status == "running" else None,
        "finished_at_unix": now if status in {"completed", "failed", "cancelled"} else None,
        "payload": payload or {},
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO capability_steps(
                step_id, run_id, step_order, name, status, current_focus,
                evidence, blocker, started_at_unix, finished_at_unix, payload_json
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["step_id"],
                run_id,
                record["step_order"],
                record["name"],
                record["status"],
                record["current_focus"],
                record["evidence"],
                record["blocker"],
                record["started_at_unix"],
                record["finished_at_unix"],
                _json(record["payload"]),
            ),
        )
        conn.commit()
    return record


def list_capability_steps(run_id: str, limit: int = 50) -> list[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM capability_steps WHERE run_id=? ORDER BY step_order, step_id LIMIT ?",
            (run_id, max(1, int(limit or 50))),
        ).fetchall()
    return [
        {
            "step_id": row["step_id"],
            "run_id": row["run_id"],
            "step_order": row["step_order"],
            "name": row["name"],
            "status": row["status"],
            "current_focus": row["current_focus"],
            "evidence": row["evidence"],
            "blocker": row["blocker"],
            "started_at_unix": row["started_at_unix"],
            "finished_at_unix": row["finished_at_unix"],
            "payload": json.loads(row["payload_json"] or "{}"),
        }
        for row in rows
    ]


def add_capability_artifact(
    *,
    run_id: str,
    kind: str,
    label: str,
    path_or_ref: str,
    summary: str = "",
    step_id: str = "",
    metadata: Optional[Dict[str, Any]] = None,
) -> Dict[str, Any]:
    run = get_capability_run(run_id)
    if not run:
        raise ValueError(f"Unknown capability run: {run_id}")
    artifact_id = "artifact-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = _now()
    record = {
        "artifact_id": artifact_id,
        "trace_id": str(run.get("trace_id") or "").strip(),
        "run_id": run_id,
        "step_id": step_id.strip(),
        "kind": kind.strip(),
        "label": label.strip(),
        "path_or_ref": path_or_ref.strip(),
        "summary": summary.strip(),
        "metadata": {
            **({"trace_id": str(run.get("trace_id") or "").strip()} if str(run.get("trace_id") or "").strip() else {}),
            **(metadata or {}),
        },
        "created_at_unix": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO capability_artifacts(
                artifact_id, trace_id, run_id, step_id, kind, label, path_or_ref,
                summary, metadata_json, created_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                artifact_id,
                record["trace_id"],
                run_id,
                record["step_id"],
                record["kind"],
                record["label"],
                record["path_or_ref"],
                record["summary"],
                _json(record["metadata"]),
                now,
            ),
        )
        conn.commit()
    return record


def list_capability_artifacts(run_id: str, limit: int = 50) -> list[Dict[str, Any]]:
    with connect() as conn:
        rows = conn.execute(
            "SELECT * FROM capability_artifacts WHERE run_id=? ORDER BY created_at_unix DESC LIMIT ?",
            (run_id, max(1, int(limit or 50))),
        ).fetchall()
    return [
        {
            "artifact_id": row["artifact_id"],
            "trace_id": row["trace_id"],
            "run_id": row["run_id"],
            "step_id": row["step_id"],
            "kind": row["kind"],
            "label": row["label"],
            "path_or_ref": row["path_or_ref"],
            "summary": row["summary"],
            "metadata": json.loads(row["metadata_json"] or "{}"),
            "created_at_unix": row["created_at_unix"],
        }
        for row in rows
    ]


def write_audit_log(
    *,
    action: str,
    actor_user_id: str = "",
    platform: str = "",
    chat_id: str = "",
    target_type: str = "",
    target_id: str = "",
    summary: str = "",
    payload: Optional[Dict[str, Any]] = None,
) -> None:
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO audit_logs(timestamp_unix, actor_user_id, platform, chat_id, action, target_type, target_id, summary, payload_json)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (_now(), actor_user_id, platform, chat_id, action, target_type, target_id, summary, _json(payload or {})),
        )
        conn.commit()


def create_table_sync_rule(
    *,
    name: str,
    source_ref: str,
    target_ref: str,
    match_keys: Optional[list[str]] = None,
    field_map: Optional[Dict[str, Any]] = None,
    write_policy: str = "fill_blank",
    approval_required: bool = True,
    enabled: bool = True,
    owner_user_id: str = "",
) -> Dict[str, Any]:
    rule_id = "rule-" + time.strftime("%Y%m%d-%H%M%S") + "-" + uuid.uuid4().hex[:8]
    now = _now()
    record = {
        "rule_id": rule_id,
        "name": name.strip(),
        "source_ref": source_ref.strip(),
        "target_ref": target_ref.strip(),
        "match_keys": match_keys or [],
        "field_map": field_map or {},
        "write_policy": write_policy.strip() or "fill_blank",
        "approval_required": bool(approval_required),
        "enabled": bool(enabled),
        "owner_user_id": owner_user_id.strip(),
        "created_at_unix": now,
        "updated_at_unix": now,
    }
    with connect() as conn:
        conn.execute(
            """
            INSERT INTO table_sync_rules(
                rule_id, name, source_ref, target_ref, match_keys_json,
                field_map_json, write_policy, approval_required, enabled,
                owner_user_id, created_at_unix, updated_at_unix
            )
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                record["rule_id"],
                record["name"],
                record["source_ref"],
                record["target_ref"],
                _json(record["match_keys"]),
                _json(record["field_map"]),
                record["write_policy"],
                1 if record["approval_required"] else 0,
                1 if record["enabled"] else 0,
                record["owner_user_id"],
                now,
                now,
            ),
        )
        conn.commit()
    return record


def _rule_from_row(row: sqlite3.Row) -> Dict[str, Any]:
    return {
        "rule_id": row["rule_id"],
        "name": row["name"],
        "source_ref": row["source_ref"],
        "target_ref": row["target_ref"],
        "match_keys": json.loads(row["match_keys_json"] or "[]"),
        "field_map": json.loads(row["field_map_json"] or "{}"),
        "write_policy": row["write_policy"],
        "approval_required": bool(row["approval_required"]),
        "enabled": bool(row["enabled"]),
        "owner_user_id": row["owner_user_id"],
        "created_at_unix": row["created_at_unix"],
        "updated_at_unix": row["updated_at_unix"],
    }


def list_table_sync_rules(*, enabled_only: bool = False, limit: int = 20) -> list[Dict[str, Any]]:
    query = "SELECT * FROM table_sync_rules"
    params: list[Any] = []
    if enabled_only:
        query += " WHERE enabled=1"
    query += " ORDER BY updated_at_unix DESC LIMIT ?"
    params.append(max(1, int(limit or 20)))
    with connect() as conn:
        rows = conn.execute(query, params).fetchall()
    return [_rule_from_row(row) for row in rows]


def get_table_sync_rule(rule_id: str) -> Optional[Dict[str, Any]]:
    with connect() as conn:
        row = conn.execute("SELECT * FROM table_sync_rules WHERE rule_id=?", (rule_id,)).fetchone()
    return _rule_from_row(row) if row else None


def update_table_sync_rule(rule_id: str, **fields: Any) -> Optional[Dict[str, Any]]:
    allowed = {
        "name",
        "source_ref",
        "target_ref",
        "match_keys",
        "field_map",
        "write_policy",
        "approval_required",
        "enabled",
        "owner_user_id",
    }
    current = get_table_sync_rule(rule_id)
    if not current:
        return None
    updates: Dict[str, Any] = {key: value for key, value in fields.items() if key in allowed and value is not None}
    if not updates:
        return current

    column_map = {
        "match_keys": "match_keys_json",
        "field_map": "field_map_json",
    }
    assignments = []
    values: list[Any] = []
    for key, value in updates.items():
        column = column_map.get(key, key)
        assignments.append(f"{column}=?")
        if key in {"match_keys", "field_map"}:
            values.append(_json(value))
        elif key in {"approval_required", "enabled"}:
            values.append(1 if bool(value) else 0)
        else:
            values.append(str(value))
    assignments.append("updated_at_unix=?")
    values.append(_now())
    values.append(rule_id)

    with connect() as conn:
        conn.execute(f"UPDATE table_sync_rules SET {', '.join(assignments)} WHERE rule_id=?", values)
        conn.commit()
    return get_table_sync_rule(rule_id)


def count_rows(conn: sqlite3.Connection, tables: Iterable[str]) -> Dict[str, int]:
    counts: Dict[str, int] = {}
    for table in tables:
        row = conn.execute(f"SELECT COUNT(*) AS count FROM {table}").fetchone()
        counts[table] = int(row["count"] if row else 0)
    return counts


def status_snapshot() -> Dict[str, Any]:
    path = db_path()
    with connect() as conn:
        schema_row = conn.execute("SELECT value FROM schema_meta WHERE key='schema_version'").fetchone()
        counts = count_rows(
            conn,
            [
                "users",
                "user_memory",
                "channels",
                "tasks",
                "task_participants",
                "task_memory",
                "background_jobs",
                "background_job_events",
                "capabilities",
                "capability_runs",
                "capability_steps",
                "capability_artifacts",
                "table_sync_rules",
                "table_sync_runs",
                "approvals",
                "audit_logs",
            ],
        )
        active_jobs = conn.execute(
            "SELECT COUNT(*) AS count FROM background_jobs WHERE status IN ('queued', 'running', 'paused', 'blocked')"
        ).fetchone()
    return {
        "path": str(path),
        "exists": path.exists(),
        "size_bytes": path.stat().st_size if path.exists() else 0,
        "schema_version": int(schema_row["value"]) if schema_row else 0,
        "counts": counts,
        "active_background_jobs": int(active_jobs["count"] if active_jobs else 0),
    }
