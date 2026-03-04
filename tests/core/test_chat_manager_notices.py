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


def test_identity_session_pointer_roundtrip(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    s1 = manager.get_or_create_session_for_identity("telegram:u1:c9")
    s2 = manager.get_or_create_session_for_identity("telegram:u1:c9")
    assert s1 == s2
    assert manager.get_session_for_identity("telegram:u1:c9") == s1

    s3 = manager.rotate_session_for_identity("telegram:u1:c9")
    assert s3 != s1

    s4 = manager.get_or_create_session_for_identity("telegram:u1:c9")
    assert s4 == s3


def test_identity_pointer_summary_grouping(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    manager.get_or_create_session_for_identity("telegram:u1:c1")
    manager.get_or_create_session_for_identity("telegram:u2:c2")
    manager.get_or_create_session_for_identity("web:userA:browser")

    summary = manager.get_identity_pointer_summary()
    assert summary["total"] == 3
    assert summary["by_platform"] == {"telegram": 2, "web": 1}


def test_get_session_for_identity_returns_none_when_missing(tmp_path):
    manager = ChatSessionManager(str(tmp_path))
    assert manager.get_session_for_identity("telegram:missing:chat") is None


def test_identity_pointer_list_and_prune(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    valid_session = manager.get_or_create_session_for_identity("telegram:u1:c1")

    # Inject an invalid pointer entry manually.
    pointers_path = tmp_path / "session_pointers.json"
    pointers_path.write_text('{"telegram:u1:c1": "%s", "telegram:ghost:c9": "missing-session"}' % valid_session)

    listed = manager.list_identity_pointers(limit=10)
    assert len(listed) == 2
    ghost = next(item for item in listed if item["identity_key"] == "telegram:ghost:c9")
    assert ghost["session_exists"] is False

    removed = manager.prune_invalid_identity_pointers()
    assert removed == 1

    listed_after = manager.list_identity_pointers(limit=10)
    assert len(listed_after) == 1
    assert listed_after[0]["identity_key"] == "telegram:u1:c1"



def test_user_notice_state_transitions(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    created = manager.add_user_notice(
        user_scope="local_default",
        message="Delegated task needs review",
        source_session_id="s1",
        task_id="abc12345",
        status="failed",
    )

    acked = manager.update_user_notice_state("local_default", created["id"], "ack")
    assert acked is not None
    assert acked["read"] is True

    reopened = manager.update_user_notice_state("local_default", created["id"], "reopen")
    assert reopened is not None
    assert reopened["read"] is False
    assert reopened["resolved_at"] is None

    resolved = manager.update_user_notice_state("local_default", created["id"], "resolve")
    assert resolved is not None
    assert resolved["read"] is True
    assert resolved["resolved_at"] is not None


def test_user_notice_state_filtering_and_snooze(tmp_path):
    manager = ChatSessionManager(str(tmp_path))

    n1 = manager.add_user_notice(user_scope="local_default", message="one")
    n2 = manager.add_user_notice(user_scope="local_default", message="two")

    manager.update_user_notice_state("local_default", n1["id"], "snooze", snooze_seconds=3600)
    manager.update_user_notice_state("local_default", n2["id"], "resolve")

    snoozed = manager.list_user_notices("local_default", include_read=True, state="snoozed")
    resolved = manager.list_user_notices("local_default", include_read=True, state="resolved")

    assert len(snoozed) == 1
    assert snoozed[0]["id"] == n1["id"]
    assert snoozed[0]["notice_state"] == "snoozed"

    assert len(resolved) == 1
    assert resolved[0]["id"] == n2["id"]
    assert resolved[0]["notice_state"] == "resolved"
