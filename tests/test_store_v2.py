"""Durable trace, ownership and consent boundary regression tests."""

import json
import time
from datetime import UTC, datetime, timedelta

import psycopg
import pytest



def make_agreement(store, sender="merchant", client="22670000000", identifier="abcd1234-001"):
    message_id = "create-" + identifier
    store.enqueue(message_id, sender, {"id": message_id, "from": sender, "type": "text", "text": {"body": "confirm"}, "timestamp": str(int(time.time()))})
    store.claim_inbox()
    data = {"id": identifier, "client": "Awa", "client_phone": client, "product": "Riz", "total": 1000, "paid": 100, "due_date": (datetime.now(UTC) + timedelta(days=3)).date().isoformat()}
    store.finish_inbox(message_id, sender, {}, [{"type": "text", "text": {"body": "confirmé"}}], data)
    return data


def test_message_media_and_delivery_trace(make_store):
    store = make_store()
    inbound = {"id": "wamid.audio", "from": "merchant", "type": "audio", "audio": {"id": "media", "mime_type": "audio/ogg"}, "timestamp": "1234"}
    assert store.enqueue(inbound["id"], "merchant", inbound)
    assert not store.enqueue(inbound["id"], "merchant", inbound)
    store.record_media(inbound["id"], {"storage_path": "file.ogg", "byte_count": 15})
    store.record_media(inbound["id"], {"status": "transcribed"}, "Bonjour")
    row = store.db.execute("SELECT * FROM media_assets").fetchone()
    assert row["storage_path"] == "file.ogg"
    assert row["transcript"] == "Bonjour"
    assert json.loads(row["metadata"])["byte_count"] == 15
    store.claim_inbox()
    store.finish_inbox(inbound["id"], "merchant", {}, [{"type": "text", "text": {"body": "Bonjour"}}])
    job = store.claim_outbox()
    store.sent_outbox(job["id"], "wamid.out")
    assert store.record_delivery_status({"id": "wamid.out", "status": "read", "timestamp": "5000"})
    assert not store.record_delivery_status({"id": "wamid.out", "status": "read", "timestamp": "5000"})
    store.record_delivery_status({"id": "wamid.out", "status": "sent", "timestamp": "4000"})
    out = store.db.execute("SELECT * FROM messages WHERE direction='outbound'").fetchone()
    assert out["status"] == "read"
    assert out["source_message_id"] == inbound["id"]
    assert store.db.execute("SELECT COUNT(*) FROM messages").fetchone()[0] == 2


def test_agreement_calendar_and_consent_phone_ownership(make_store):
    store = make_store()
    make_agreement(store)
    events = store.calendar_for("merchant")
    assert len(events) == 1
    assert events[0]["client"] == "Awa"
    assert datetime.fromtimestamp(events[0]["starts_at"], UTC).hour == 9
    assert store.calendar_for("intruder") == []
    assert store.db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 4
    assert not store.set_customer_consent("intruder", "WT-ABCD1234", True)
    assert not store.set_customer_consent("22670000000", "WT-ABCD1234", True, "fake")
    store.enqueue("consent", "22670000000", {"type": "text", "text": {"body": "ACCEPTER WT-ABCD1234"}})
    store.claim_inbox()
    assert store.set_customer_consent("22670000000", "WT-ABCD1234", True, "consent")
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 4
    store.set_reminders_enabled("22670000000", False)
    assert store.db.execute("SELECT granted FROM customer_consents").fetchone()[0] == 0
    store.set_customer_consent("22670000000", "WT-ABCD1234", True, "consent")
    assert store.db.execute("SELECT reminders_enabled FROM contacts WHERE id='22670000000'").fetchone()[0] == 0
    assert not store.settle_agreement("intruder", "WT-ABCD1234")
    assert store.settle_agreement("merchant", "WT-ABCD1234")
    assert store.calendar_for("merchant")[0]["status"] == "settled"
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 0


def test_atomic_agreement_no_partial_commit_and_indefinite_retention(make_store):
    store = make_store()
    data = make_agreement(store)
    store.enqueue("duplicate", "merchant", {"type": "text"})
    store.claim_inbox()
    with pytest.raises(psycopg.errors.UniqueViolation):
        store.finish_inbox("duplicate", "merchant", {"broken": True}, [], data)
    assert store.get_session("merchant") == {}
    assert store.db.execute("SELECT COUNT(*) FROM calendar_events").fetchone()[0] == 1
    store.purge(0)
    assert len(store.list_agreements("merchant")) == 1
    store.purge(1)
    assert len(store.list_agreements("merchant")) == 1


def test_archive_unprocessed_and_uncertain_preserved(make_store):
    store = make_store()
    message = {"id": "old", "from": "someone", "type": "text", "text": {"body": "bonjour"}}
    assert store.archive_message(message, reason="late")
    assert not store.archive_message(message, reason="late")
    assert store.claim_inbox() is None
    with store.lock, store.db:
        identifier = store.queue_outbox("someone", {"type": "text", "text": {"body": "test"}})
    store.claim_outbox()
    store.recover()
    assert store.db.execute("SELECT status FROM messages WHERE outbox_id=%s", (identifier,)).fetchone()[0] == "uncertain"


@pytest.mark.parametrize("state", ["delivered", "read"])
@pytest.mark.parametrize("early_callback", [True, False])
def test_delivery_survives_send_ack_and_recovery(state, early_callback, make_store):
    store = make_store()
    with store.lock, store.db:
        identifier = store.queue_outbox("owner", {"type": "text", "text": {"body": "hello"}})
    store.claim_outbox()
    if early_callback:
        store.record_delivery_status({"id": "remote", "status": state, "timestamp": "12345"})
    store.sent_outbox(identifier, "remote")
    if not early_callback:
        store.record_delivery_status({"id": "remote", "status": state, "timestamp": "12345"})
    store.recover()
    assert store.db.execute("SELECT status FROM messages WHERE outbox_id=%s", (identifier,)).fetchone()[0] == state
    assert store.db.execute("SELECT status FROM outbox WHERE id=%s", (identifier,)).fetchone()[0] == "sent"


def test_stop_then_explicit_resume_requires_client_consent_and_never_requeues(make_store):
    store = make_store()
    make_agreement(store)
    store.enqueue("yes", "22670000000", {"type": "text", "text": {"body": "ACCEPTER WT-ABCD1234"}})
    assert store.set_customer_consent("22670000000", "ABCD1234", True, "yes")
    store.set_reminders_enabled("merchant", False)
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE recipient='merchant' AND status='stopped'").fetchone()[0] == 2
    store.set_reminders_enabled("merchant", True)
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE recipient='merchant' AND status='pending'").fetchone()[0] == 2
    store.set_reminders_enabled("22670000000", False)
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 0
    store.set_reminders_enabled("22670000000", True)
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 0
    # Queued/ambiguous outputs remain permanently attached, even after fresh opt-in.
    row = store.db.execute("SELECT id FROM reminders LIMIT 1").fetchone()[0]
    with store.lock, store.db:
        outgoing = store.queue_outbox("merchant", {"type": "text", "text": {"body": "prior"}})
        store.db.execute("UPDATE reminders SET outbox_id=%s,status='cancelled_consent' WHERE id=%s", (outgoing, row))
    assert store.set_customer_consent("22670000000", "ABCD1234", True, "yes")
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 3
    assert store.db.execute("SELECT status FROM reminders WHERE id=%s", (row,)).fetchone()[0] == "cancelled_consent"
    store.settle_agreement("merchant", "ABCD1234")
    store.set_reminders_enabled("merchant", False)
    store.set_reminders_enabled("merchant", True)
    assert store.db.execute("SELECT COUNT(*) FROM reminders WHERE status='pending'").fetchone()[0] == 0


def test_generated_audio_metadata_is_private_and_not_sent_to_meta(make_store):

    store = make_store()
    with store.lock, store.db:
        identifier = store.queue_outbox("22601010101", {
            "type": "audio", "audio": {"id": "12345"},
            "_local_audio": {"storage_path": "private.audio", "mime_type": "audio/mpeg",
                             "sha256": "abc", "source_text": "Ne y yibeogo"},
        })
    outgoing = store.claim_outbox()
    assert outgoing["content"] == {"type": "audio", "audio": {"id": "12345"}}
    media = store.db.execute("SELECT storage_path,metadata FROM media_assets WHERE message_id=%s",
                             ("out:" + str(identifier),)).fetchone()
    assert media["storage_path"] == "private.audio"
    assert "Ne y yibeogo" in media["metadata"]
    store.close()


def test_float_timestamps_roundtrip_even_with_low_server_precision(monkeypatch, make_store):
    connect = psycopg.connect

    def low_precision_connect(*args, **kwargs):
        connection = connect(*args, **kwargs)
        connection.execute("SET extra_float_digits=0")
        return connection

    monkeypatch.setattr(psycopg, "connect", low_precision_connect)
    store = make_store()
    timestamp = float.fromhex("0x1.abcdef1234567p+30")
    for _ in range(2):
        assert store.db.execute("SHOW extra_float_digits").fetchone()[0] == "3"
        restored = store.db.execute("SELECT %s::double precision", (timestamp,)).fetchone()[0]
        assert restored.hex() == timestamp.hex()
        # The setting must be restored after an idle connection is reopened too.
        store.db.close()
