from ai_assistant.core.chat_manager import ChatSessionManager


def test_user_notice_roundtrip(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    created = manager.add_user_notice(
        user_scope="local_default",
        message="Delegated task abc done",
        source_session_id="s1",
        task_id="abc12345",
        status="completed",
    )

    notices = manager.list_user_notices("local_default")
    assert len(notices) == 1
    assert notices[0]["id"] == created["id"]
    assert notices[0]["task_id"] == "abc12345"
    assert notices[0]["read"] is False

    changed = manager.mark_user_notices_read("local_default")
    assert changed == 1
    assert manager.list_user_notices("local_default") == []
    all_notices = manager.list_user_notices("local_default", include_read=True)
    assert len(all_notices) == 1
    assert all_notices[0]["read"] is True
