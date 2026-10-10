"""Read-only administration projections, bounded queries and private media boundary."""

import time

import pytest

from bot_wumtaaba import admin_data
from bot_wumtaaba.store import Store

PHONE = "22670000000"


@pytest.fixture
def store():
    value = Store(":memory:")
    yield value
    value.close()


def seed(store):
    store.enqueue("create", PHONE, {"type": "text", "text": {"body": "<script>alert(1)</script>"}})
    store.claim_inbox()
    store.finish_inbox("create", PHONE, {}, [], {
        "id": "abcd1234-001", "client": "Awa", "client_phone": "22671111111",
        "product": "Riz", "total": 1000, "paid": 100, "due_date": "2099-01-01",
    })


def test_overview_counts_and_no_secret_payload(store):
    seed(store)
    store.enqueue("audio", PHONE, {"type": "audio", "audio": {"id": "secret-media-id"}})
    data = admin_data.overview(store)
    assert data["counts"] == {"contacts": 2, "agreements": 1, "open_agreements": 1,
                              "balance": 900, "audios": 1, "pending_reminders": 4,
                              "failed_messages": 0}
    assert sum(item["count"] for item in data["activity"]) == 2
    assert "payload" not in str(data)
    assert "secret-media-id" not in str(data)


def test_agreements_search_is_parameterized_and_literal(store):
    seed(store)
    assert admin_data.agreements(store, "Awa")["total"] == 1
    assert admin_data.agreements(store, "' OR 1=1 --")["total"] == 0
    assert admin_data.agreements(store, "%")["total"] == 0
    assert admin_data.agreements(store)["items"][0]["status"] == "open"
    assert "data" not in admin_data.agreements(store)["items"][0]


def test_list_pagination_bounded_and_empty(store):
    for number in range(30):
        phone = str(22670000000 + number)
        store.enqueue(str(number), phone, {"type": "text", "text": {"body": str(number)}})
    first = admin_data.conversations(store, page=-9)
    assert first["page"] == 1 and len(first["items"]) == 25
    second = admin_data.conversations(store, page=10**30)
    assert second["page"] == 2 and len(second["items"]) == 5
    assert admin_data.conversations(store, page="bad")["page"] == 1
    assert admin_data.conversations(store, q="missing")["total"] == 0


def test_conversation_is_recent_bounded_ascending_and_preserves_text(store):
    for number in range(205):
        store.enqueue(str(number), PHONE, {"type": "text", "text": {"body": str(number)}, "timestamp": 1000 + number})
    data = admin_data.conversation(store, PHONE)
    assert data["truncated"] is True
    assert len(data["messages"]) == 200
    assert data["messages"][0]["text"] == "5"
    assert data["messages"][-1]["text"] == "204"
    assert admin_data.conversation(store, "' OR 1=1 --") is None
    assert admin_data.conversation(store, "22672222222") is None
    store.enqueue("xss", PHONE, {"type": "text", "text": {"body": "<script>alert(1)</script>"}})
    assert admin_data.conversation(store, PHONE)["messages"][-1]["text"] == "<script>alert(1)</script>"


def test_reminders_status_filter(store):
    seed(store)
    data = admin_data.reminders(store, "pending_consent")
    assert data["total"] == 4
    assert {item["role"] for item in data["items"]} == {"merchant", "customer"}
    assert all(item["reference"] == "WT-ABCD1234" for item in data["items"])
    assert admin_data.reminders(store, "' OR 1=1 --")["total"] == 0


def test_media_projection_and_containment(store, tmp_path):
    store.enqueue("audio", PHONE, {"type": "audio", "audio": {"id": "private"}})
    base = tmp_path / "media"
    base.mkdir()
    audio = base / "one.audio"
    audio.write_bytes(b"audio")
    store.record_media("audio", {"storage_path": "one.audio", "mime_type": "audio/ogg"}, "Bonjour")
    assert admin_data.media_file(store, "in:audio", base) == (audio, "audio/ogg")
    message = admin_data.conversation(store, PHONE)["messages"][0]
    assert message["media"] == {"available": True, "transcript": "Bonjour", "mime_type": "audio/ogg"}
    assert "storage_path" not in str(message) and "private" not in str(message)
    outside = tmp_path / "secret"
    outside.write_text("secret")
    for path in ("../secret", str(outside), "escape"):
        if path == "escape":
            (base / path).symlink_to(outside)
        store.record_media("audio", {"storage_path": path})
        assert admin_data.media_file(store, "in:audio", base) is None
    store.record_media("audio", {"storage_path": "one.audio", "mime_type": "text/html"})
    assert admin_data.media_file(store, "in:audio", base)[1] == "application/octet-stream"
    assert admin_data.media_file(store, "unknown", base) is None
    audio.unlink()
    assert admin_data.media_file(store, "in:audio", base) is None


def test_all_queries_are_read_only(store):
    seed(store)
    before = store.db.total_changes
    for function in (admin_data.overview, admin_data.agreements, admin_data.conversations, admin_data.reminders):
        function(store)
    admin_data.conversation(store, PHONE)
    assert store.db.total_changes == before


def test_activity_uses_recent_period(store):
    store.enqueue("old", PHONE, {"type": "text", "timestamp": time.time() - 30 * 86400})
    assert admin_data.overview(store)["activity"] == []
