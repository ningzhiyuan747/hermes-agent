import json

from agent import business_db
from tools import memory_tool as memory_mod


def _store(tmp_path, monkeypatch):
    monkeypatch.setattr("tools.memory_tool.get_memory_dir", lambda: tmp_path)
    store = memory_mod.MemoryStore(memory_char_limit=500, user_char_limit=300)
    store.load_from_disk()
    return store


def test_dm_user_memory_write_routes_to_profile_scope(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    session = {
        "HERMES_SESSION_PLATFORM": "feishu",
        "HERMES_SESSION_CHAT_TYPE": "dm",
        "HERMES_SESSION_USER_ID": "ou_user_1",
        "HERMES_SESSION_CHAT_ID": "oc_dm_1",
        "HERMES_SESSION_THREAD_ID": "",
    }
    captured = {}

    monkeypatch.setattr(memory_mod, "_get_session_env", lambda name, default="": session.get(name, default))
    monkeypatch.setattr(memory_mod, "_get_bound_task_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(business_db, "get_user_memory", lambda **kwargs: None)
    monkeypatch.setattr(business_db, "upsert_user_memory", lambda **kwargs: captured.setdefault("user", kwargs) or kwargs)

    result = json.loads(memory_mod.memory_tool(action="add", target="user", content="喜欢简洁回答", store=store))

    assert result["success"] is True
    assert captured["user"]["platform"] == "feishu"
    assert captured["user"]["user_id"] == "ou_user_1"
    assert captured["user"]["scope"] == "profile"
    assert captured["user"]["summary"] == "喜欢简洁回答"


def test_dm_agent_memory_write_routes_to_user_notes_scope(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    session = {
        "HERMES_SESSION_PLATFORM": "feishu",
        "HERMES_SESSION_CHAT_TYPE": "dm",
        "HERMES_SESSION_USER_ID": "ou_user_2",
        "HERMES_SESSION_CHAT_ID": "oc_dm_2",
        "HERMES_SESSION_THREAD_ID": "",
    }
    captured = {}

    monkeypatch.setattr(memory_mod, "_get_session_env", lambda name, default="": session.get(name, default))
    monkeypatch.setattr(memory_mod, "_get_bound_task_id", lambda *_args, **_kwargs: "")
    monkeypatch.setattr(business_db, "get_user_memory", lambda **kwargs: None)
    monkeypatch.setattr(business_db, "upsert_user_memory", lambda **kwargs: captured.setdefault("user", kwargs) or kwargs)

    result = json.loads(memory_mod.memory_tool(action="add", target="memory", content="客户沟通里喜欢先看结论", store=store))

    assert result["success"] is True
    assert captured["user"]["scope"] == "notes"
    assert captured["user"]["memory"]["entries"] == ["客户沟通里喜欢先看结论"]


def test_task_group_memory_write_routes_to_task_memory(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    session = {
        "HERMES_SESSION_PLATFORM": "dingtalk",
        "HERMES_SESSION_CHAT_TYPE": "group",
        "HERMES_SESSION_USER_ID": "staff_1",
        "HERMES_SESSION_CHAT_ID": "cid_group_1",
        "HERMES_SESSION_THREAD_ID": "",
    }
    captured = {}

    monkeypatch.setattr(memory_mod, "_get_session_env", lambda name, default="": session.get(name, default))
    monkeypatch.setattr(memory_mod, "_get_bound_task_id", lambda *_args, **_kwargs: "task-123")
    monkeypatch.setattr(business_db, "get_task_memory", lambda **kwargs: None)
    monkeypatch.setattr(business_db, "upsert_task_memory", lambda **kwargs: captured.setdefault("task", kwargs) or kwargs)

    result = json.loads(memory_mod.memory_tool(action="add", target="memory", content="已确认采购单位，下一步追合同附件", store=store))

    assert result["success"] is True
    assert captured["task"]["task_id"] == "task-123"
    assert captured["task"]["scope"] == "shared"
    assert captured["task"]["summary"] == "已确认采购单位，下一步追合同附件"


def test_task_group_user_profile_write_is_rejected(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    session = {
        "HERMES_SESSION_PLATFORM": "dingtalk",
        "HERMES_SESSION_CHAT_TYPE": "group",
        "HERMES_SESSION_USER_ID": "staff_2",
        "HERMES_SESSION_CHAT_ID": "cid_group_2",
        "HERMES_SESSION_THREAD_ID": "",
    }
    called = {"user": 0, "task": 0}

    monkeypatch.setattr(memory_mod, "_get_session_env", lambda name, default="": session.get(name, default))
    monkeypatch.setattr(memory_mod, "_get_bound_task_id", lambda *_args, **_kwargs: "task-456")
    monkeypatch.setattr(business_db, "upsert_user_memory", lambda **kwargs: called.__setitem__("user", called["user"] + 1))
    monkeypatch.setattr(business_db, "upsert_task_memory", lambda **kwargs: called.__setitem__("task", called["task"] + 1))

    result = json.loads(memory_mod.memory_tool(action="add", target="user", content="某成员偏好", store=store))

    assert result["success"] is False
    assert "private 1:1 conversation" in result["error"]
    assert called["user"] == 0
    assert called["task"] == 0


def test_unbound_group_memory_write_is_rejected(tmp_path, monkeypatch):
    store = _store(tmp_path, monkeypatch)
    session = {
        "HERMES_SESSION_PLATFORM": "dingtalk",
        "HERMES_SESSION_CHAT_TYPE": "group",
        "HERMES_SESSION_USER_ID": "staff_3",
        "HERMES_SESSION_CHAT_ID": "cid_group_3",
        "HERMES_SESSION_THREAD_ID": "",
    }

    monkeypatch.setattr(memory_mod, "_get_session_env", lambda name, default="": session.get(name, default))
    monkeypatch.setattr(memory_mod, "_get_bound_task_id", lambda *_args, **_kwargs: "")

    result = json.loads(memory_mod.memory_tool(action="add", target="memory", content="群里临时想法", store=store))

    assert result["success"] is False
    assert "requires a bound task" in result["error"]
