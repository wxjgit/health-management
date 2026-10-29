"""Chat history cursors stay stable across new messages and timestamp ties."""

import pytest
from authenticated_client import AuthenticatedClient as TestClient
from app.common.config import Settings
from app.main import create_app
from app.workflows.engine import Engine


@pytest.fixture
def engine(tmp_path):
    runtime = Engine(Settings(data_dir=tmp_path, scheduler_enabled=False), user_id='00000000-0000-4000-8000-000000000001')
    yield runtime
    runtime.close()


def insert(repo, index, conversation="main"):
    with repo.conn:
        repo.conn.execute(
            "INSERT INTO messages VALUES (?,?,?,?,?,?,?,?,?,?)",
            (f"m{index}", f"t{index}", conversation, "user" if index % 2 == 0 else "assistant",
             f"message {index}", "[]", "[]", "2026-10-28T12:00:00+08:00", "[]", 0),
        )


def test_cursor_handles_equal_timestamps_new_messages_and_page_end(engine):
    repo = engine.repo
    for index in range(45):
        insert(repo, index)
    latest = repo.messages(limit=20)
    assert [row["id"] for row in latest] == [f"m{i}" for i in range(25, 45)]
    insert(repo, 45)
    older = repo.messages(limit=20, before=latest[0]["id"])
    assert [row["id"] for row in older] == [f"m{i}" for i in range(5, 25)]
    oldest = repo.messages(limit=20, before=older[0]["id"])
    assert [row["id"] for row in oldest] == [f"m{i}" for i in range(5)]
    assert repo.messages(limit=20, before=oldest[0]["id"]) == []


def test_cursor_cannot_cross_conversations_or_include_expired_context(engine):
    repo = engine.repo
    insert(repo, 0)
    insert(repo, 1)
    insert(repo, 2, "other")
    repo.register_workflow("t1", "r1", "f1", "chat")
    with repo.conn:
        repo.conn.execute("INSERT OR REPLACE INTO workflow_activity VALUES (?,?,?)", ("t1", "2000-01-01", "2000-01-02"))
    assert [row["id"] for row in repo.messages()] == ["m0"]
    assert repo.messages(before="m2") == []
    assert repo.messages(before="missing") == []


def test_api_validates_page_size(tmp_path):
    with TestClient(create_app(Settings(data_dir=tmp_path, scheduler_enabled=False))) as client:
        assert client.get("/api/messages?limit=20").json() == []
        assert client.get("/api/messages?limit=0").status_code == 422
        assert client.get("/api/messages?limit=101").status_code == 422
