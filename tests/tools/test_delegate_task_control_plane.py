from __future__ import annotations

import json
from pathlib import Path
from types import SimpleNamespace

from agent import business_db
from tools import delegate_tool


def test_initialize_delegation_record_immediately_links_chat_task(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    parent = SimpleNamespace(
        session_id="agent:main:feishu:group:oc_parent:ou_parent",
        _gateway_session_key="agent:main:feishu:group:oc_parent:ou_parent",
        platform="feishu",
        _user_id="ou_parent",
    )
    child = SimpleNamespace(
        session_id="child-session-1",
        enabled_toolsets=["terminal", "file"],
    )

    record = delegate_tool._initialize_delegation_record(
        task_index=0,
        goal="Collect contract evidence",
        child=child,
        parent_agent=parent,
    )

    meta_path = Path(str(record["_meta_path"]))
    assert meta_path.exists()
    stored = json.loads(meta_path.read_text(encoding="utf-8"))
    assert str(stored.get("control_task_id") or "").strip()
    assert stored["task_scope_key"] == "feishu:chat:oc_parent"
    assert stored["person_memory_key"] == "feishu:user:ou_parent"
    assert stored["conversation_role"] == "chat_surface"
    assert business_db.get_task(stored["control_task_id"]) is not None


def test_standalone_delegation_record_syncs_control_task_state(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    parent = SimpleNamespace(
        session_id="cli-session-1",
        _gateway_session_key="",
        platform="cli",
        _user_id="codex",
    )
    child = SimpleNamespace(
        session_id="child-session-standalone",
        enabled_toolsets=["terminal"],
    )

    record = delegate_tool._initialize_delegation_record(
        task_index=0,
        goal="Run detached CLI analysis",
        child=child,
        parent_agent=parent,
    )

    assert record["standalone_control"] is True
    delegate_tool._update_delegation_record(
        record,
        status="completed",
        current_focus="Delegation task completed.",
        next_step="Review the delegated summary and continue the parent task.",
        blocker="",
        event_kind="completed",
        event_note="Delegation finished cleanly.",
        final_report="Delegation finished cleanly.",
    )

    task = business_db.get_task(str(record["control_task_id"]))
    assert task is not None
    assert task["status"] == "completed"
    control_plane = task["metadata"]["control_plane"]
    assert control_plane["current_executor"] == "subagent"
    assert control_plane["task_scope_key"] == f"task:{record['control_task_id']}"


def test_parent_task_control_plane_refreshes_from_delegation_updates(tmp_path, monkeypatch):
    hermes_home = tmp_path / ".hermes"
    db_file = tmp_path / "hermes-business.sqlite3"
    monkeypatch.setenv("HERMES_HOME", str(hermes_home))
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    parent_task = business_db.create_task(
        title="Existing parent task",
        goal="Coordinate work",
        owner_user_id="ou_parent",
        source_platform="feishu",
        source_chat_id="oc_parent_refresh",
        source_session_id="agent:main:feishu:group:oc_parent_refresh:ou_parent",
        metadata={},
    )
    assert parent_task is not None

    parent = SimpleNamespace(
        session_id="agent:main:feishu:group:oc_parent_refresh:ou_parent",
        _gateway_session_key="agent:main:feishu:group:oc_parent_refresh:ou_parent",
        platform="feishu",
        _user_id="ou_parent",
    )
    child = SimpleNamespace(
        session_id="child-session-parent-refresh",
        enabled_toolsets=["terminal"],
    )

    record = delegate_tool._initialize_delegation_record(
        task_index=0,
        goal="Investigate the issue",
        child=child,
        parent_agent=parent,
    )
    assert record["standalone_control"] is False
    delegate_tool._update_delegation_record(
        record,
        status="running",
        current_focus="Delegation worker is checking logs.",
        next_step="Return a diagnosis.",
        blocker="",
        event_kind="progress",
        event_note="Working",
    )

    task = business_db.get_task(str(parent_task["task_id"]))
    assert task is not None
    assert task["status"] == "running"
    control_plane = task["metadata"]["control_plane"]
    assert control_plane["current_executor"] == "subagent"
    assert control_plane["current_focus"] == "Delegation worker is checking logs."
    assert control_plane["task_scope_key"] == "feishu:chat:oc_parent_refresh"
