from agent import business_db
from gateway import session_context


def test_user_memory_write_requires_private_conversation(tmp_path, monkeypatch):
    db_file = tmp_path / "memory-governance-group.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="dingtalk", user_id="user-1", display_name="User 1")
    tokens = session_context.set_session_vars(
        platform="dingtalk",
        chat_id="cid-group-1",
        chat_type="group",
        user_id="user-1",
    )
    try:
        try:
            business_db.upsert_user_memory(
                platform="dingtalk",
                user_id="user-1",
                scope="profile",
                summary="偏好简洁回答",
                memory={"entries": ["偏好简洁回答"]},
            )
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "private 1:1 conversation" in str(exc)
    finally:
        session_context.clear_session_vars(tokens)


def test_user_memory_write_cannot_target_another_user(tmp_path, monkeypatch):
    db_file = tmp_path / "memory-governance-cross-user.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="feishu", user_id="ou_target", display_name="Target")
    tokens = session_context.set_session_vars(
        platform="feishu",
        chat_id="oc-dm-1",
        chat_type="dm",
        user_id="ou_actor",
    )
    try:
        try:
            business_db.upsert_user_memory(
                platform="feishu",
                user_id="ou_target",
                scope="notes",
                summary="这是别人的长期偏好",
                memory={"entries": ["这是别人的长期偏好"]},
            )
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "cannot target another user" in str(exc)
    finally:
        session_context.clear_session_vars(tokens)


def test_task_memory_write_requires_bound_shared_chat_task(tmp_path, monkeypatch):
    db_file = tmp_path / "memory-governance-unbound-task.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    task = business_db.create_task(
        title="Contract follow-up",
        goal="Track the contract retrieval work",
        owner_user_id="owner-1",
        source_platform="dingtalk",
        source_chat_id="cid-group-2",
    )
    business_db.upsert_channel(
        platform="dingtalk",
        chat_id="cid-group-2",
        chat_type="group",
        task_id="",
    )
    tokens = session_context.set_session_vars(
        platform="dingtalk",
        chat_id="cid-group-2",
        chat_type="group",
        user_id="owner-1",
    )
    try:
        try:
            business_db.upsert_task_memory(
                task_id=task["task_id"],
                scope="shared",
                summary="群聊里的任务记忆",
                memory={"entries": ["群聊里的任务记忆"]},
            )
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "bound to a task" in str(exc)
    finally:
        session_context.clear_session_vars(tokens)


def test_task_memory_write_must_match_bound_task_and_records_governance(tmp_path, monkeypatch):
    db_file = tmp_path / "memory-governance-task.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    correct_task = business_db.create_task(
        title="Bound Task",
        goal="Track the real shared task",
        owner_user_id="owner-2",
        source_platform="dingtalk",
        source_chat_id="cid-group-3",
    )
    other_task = business_db.create_task(
        title="Other Task",
        goal="A different task",
        owner_user_id="owner-2",
        source_platform="dingtalk",
        source_chat_id="cid-other",
    )
    business_db.upsert_channel(
        platform="dingtalk",
        chat_id="cid-group-3",
        chat_type="group",
        task_id=correct_task["task_id"],
    )
    tokens = session_context.set_session_vars(
        platform="dingtalk",
        chat_id="cid-group-3",
        chat_type="group",
        user_id="owner-2",
    )
    try:
        try:
            business_db.upsert_task_memory(
                task_id=other_task["task_id"],
                scope="shared",
                summary="这不该写到另一个 task",
                memory={"entries": ["这不该写到另一个 task"]},
            )
            assert False, "expected ValueError"
        except ValueError as exc:
            assert "does not match the task bound to the current chat" in str(exc)

        record = business_db.upsert_task_memory(
            task_id=correct_task["task_id"],
            scope="shared",
            summary="当前 blocker 是飞书鉴权",
            memory={"entries": ["当前 blocker 是飞书鉴权"]},
        )
        governance = record["memory"]["governance"]
        assert governance["memory_kind"] == "task"
        assert governance["scope"] == "shared"
        assert governance["owner_ref"] == f"task:{correct_task['task_id']}"
        assert governance["session"]["platform"] == "dingtalk"
        assert governance["session"]["chat_id"] == "cid-group-3"
    finally:
        session_context.clear_session_vars(tokens)


def test_dm_user_memory_write_records_governance(tmp_path, monkeypatch):
    db_file = tmp_path / "memory-governance-user.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    business_db.upsert_user(platform="feishu", user_id="ou-user-3", display_name="User 3")
    tokens = session_context.set_session_vars(
        platform="feishu",
        chat_id="oc-dm-3",
        chat_type="dm",
        user_id="ou-user-3",
    )
    try:
        record = business_db.upsert_user_memory(
            platform="feishu",
            user_id="ou-user-3",
            scope="profile",
            summary="偏好先给结论",
            memory={"entries": ["偏好先给结论"]},
        )
        governance = record["memory"]["governance"]
        assert governance["memory_kind"] == "person"
        assert governance["scope"] == "profile"
        assert governance["owner_ref"] == "feishu:user:ou-user-3"
        assert governance["session"]["chat_type"] == "dm"
    finally:
        session_context.clear_session_vars(tokens)
