"""Annotation data survives new DB connections; failed writes cannot produce success replies."""

import hashlib
import json
import time

import pytest

from bot_wumtaaba.annotation_tasks import assign_sent_previews, register_task
from bot_wumtaaba.annotations import handle_annotation

OWNER = "22670000001"
AUDIO = b"fLaC-public-test-fixture"
SAMPLE = {
    "id": "test-clip",
    "dataset": "example/asr",
    "revision": "a" * 40,
    "sha256": hashlib.sha256(AUDIO).hexdigest(),
    "proposals": {
        "A": {"text": "laafi", "model_id": "example/one"},
        "B": {"text": "laafɩ", "model_id": "example/two"},
    },
}


async def seed(store, media):
    await register_task(store, media, "002", SAMPLE, AUDIO)
    card = {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "A : laafi\nB : laafɩ"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": "annpreview:002:" + p, "title": p}}
                    for p in ("A", "B", "tie")
                ]
            },
        },
    }
    with store.lock, store.db:
        oid = store.queue_outbox(OWNER, card)
    store.sent_outbox(oid, "wamid.test-card")
    assert assign_sent_previews(store, "002") == 1


async def receive(store, identifier, message):
    message.update(id=identifier, timestamp=str(int(time.time())))
    store.enqueue(identifier, OWNER, message)
    job = store.claim_inbox()
    assert job["id"] == identifier
    state, replies, operation = await handle_annotation(store, OWNER, message, store.get_session(OWNER))
    store.finish_inbox(identifier, OWNER, state, replies, operation)


async def test_complete_result_and_source_survive_reopening_database(store, make_store, media):
    await seed(store, media)
    await receive(
        store, "vote", {"type": "interactive", "interactive": {"button_reply": {"id": "annpreview:002:B"}}}
    )
    for identifier, body in [("note-a", "4"), ("note-b", "5"), ("correction", "laafɩ [inaudible]")]:
        await receive(store, identifier, {"type": "text", "text": {"body": body}})
    reopened = make_store()
    task = reopened.db.execute("SELECT * FROM annotation_tasks WHERE task_id='002'").fetchone()
    assert json.loads(task["sample"]) == SAMPLE
    assert await media.get(task["audio_path"]) == AUDIO
    response = reopened.db.execute(
        "SELECT * FROM annotation_responses WHERE recipient=%s", (OWNER,)
    ).fetchone()
    assert response["preference"] == "B"
    assert json.loads(response["ratings"]) == {"A": 4, "B": 5}
    assert response["correction"] == "laafɩ [inaudible]"
    assert response["status"] == "completed"
    assert reopened.db.execute("SELECT count(*) FROM annotation_events").fetchone()[0] == 4
    assert reopened.db.execute("SELECT status FROM annotation_assignments").fetchone()[0] == "completed"
    assert reopened.db.execute("SELECT count(*) FROM agreements").fetchone()[0] == 0


async def test_failed_private_storage_verification_does_not_register_task(store):
    class BrokenStorage:
        async def put(self, *args):
            pass

        async def get(self, *args):
            return b"wrong bytes"

    with pytest.raises(ValueError, match="stockage privé"):
        await register_task(store, BrokenStorage(), "002", SAMPLE, AUDIO)
    assert store.db.execute("SELECT count(*) FROM annotation_tasks").fetchone()[0] == 0


async def test_changed_source_cannot_replace_existing_task(store, media):
    await register_task(store, media, "002", SAMPLE, AUDIO)
    changed = {**SAMPLE, "id": "another-clip"}
    with pytest.raises(ValueError, match="autre source"):
        await register_task(store, media, "002", changed, AUDIO)
    assert json.loads(store.db.execute("SELECT sample FROM annotation_tasks").fetchone()[0]) == SAMPLE


async def test_import_rerun_preserves_assignments_and_does_not_send_messages(store, media):
    await seed(store, media)
    before = store.db.execute("SELECT count(*) FROM outbox").fetchone()[0]
    assert assign_sent_previews(store, "002") == 0
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == before
    assert store.db.execute("SELECT count(*) FROM annotation_assignments").fetchone()[0] == 1


async def test_invalid_response_rolls_back_session_and_reply_together(store, media):
    await seed(store, media)
    message = {
        "id": "invalid",
        "type": "interactive",
        "timestamp": str(int(time.time())),
        "interactive": {"button_reply": {"id": "annpreview:002:A"}},
    }
    store.enqueue("invalid", OWNER, message)
    store.claim_inbox()
    state, replies, operation = await handle_annotation(store, OWNER, message, {})
    operation["ratings"]["A"] = 6
    before = store.db.execute("SELECT count(*) FROM outbox").fetchone()[0]
    with pytest.raises(ValueError, match="invalide"):
        store.finish_inbox("invalid", OWNER, state, replies, operation)
    assert store.get_session(OWNER) == {}
    assert store.db.execute("SELECT count(*) FROM annotation_events").fetchone()[0] == 0
    assert store.db.execute("SELECT count(*) FROM outbox").fetchone()[0] == before


def test_annotation_tables_enable_row_security(store):
    rows = store.db.execute(
        "SELECT relname,relrowsecurity FROM pg_class WHERE relnamespace=current_schema()::regnamespace "
        "AND relname IN ('annotation_tasks','annotation_assignments','annotation_events','annotation_responses')"
    ).fetchall()
    assert len(rows) == 4 and all(row["relrowsecurity"] for row in rows)


async def test_purging_old_messages_keeps_annotation_but_revokes_old_callback(store, media):
    await seed(store, media)
    await receive(
        store,
        "vote-before-purge",
        {"type": "interactive", "interactive": {"button_reply": {"id": "annpreview:002:A"}}},
    )
    with store.lock, store.db:
        store.db.execute("UPDATE outbox SET created_at=%s WHERE status='sent'", (time.time() - 10 * 86400,))
    store.purge(retention_days=1)
    assert store.db.execute("SELECT count(*) FROM annotation_responses").fetchone()[0] == 1
    assert store.db.execute("SELECT count(*) FROM annotation_tasks").fetchone()[0] == 1
    _, replies, operation = await handle_annotation(
        store, OWNER, {"type": "interactive", "interactive": {"button_reply": {"id": "annpreview:002:A"}}}, {}
    )
    assert operation is None
    assert "attribué" in replies[0]["text"]["body"]
