from agent import business_db


def test_list_recent_users_can_fall_back_to_updated_at(tmp_path, monkeypatch):
    db_file = tmp_path / "recent-users.sqlite3"
    monkeypatch.setattr(business_db, "db_path", lambda: db_file)

    with business_db.connect() as conn:
        conn.execute(
            """
            INSERT INTO users(user_key, platform, user_id, display_name, role, permissions_json, created_at_unix, updated_at_unix, last_seen_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dingtalk:u-updated",
                "dingtalk",
                "u-updated",
                "Updated Only",
                "user",
                "{}",
                100,
                250,
                None,
            ),
        )
        conn.execute(
            """
            INSERT INTO users(user_key, platform, user_id, display_name, role, permissions_json, created_at_unix, updated_at_unix, last_seen_at_unix)
            VALUES(?, ?, ?, ?, ?, ?, ?, ?, ?)
            """,
            (
                "dingtalk:u-seen",
                "dingtalk",
                "u-seen",
                "Seen User",
                "user",
                "{}",
                100,
                180,
                180,
            ),
        )
        conn.commit()

    without_fallback = business_db.list_recent_users(
        platform="dingtalk",
        seen_since_unix=200,
        limit=10,
    )
    with_fallback = business_db.list_recent_users(
        platform="dingtalk",
        seen_since_unix=200,
        limit=10,
        include_updated_fallback=True,
    )

    assert [item["user_id"] for item in without_fallback] == []
    assert [item["user_id"] for item in with_fallback] == ["u-updated"]
