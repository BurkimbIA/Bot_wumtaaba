"""Regression for real demo buttons falling through to the agreements engine."""

import json
import time
from copy import deepcopy
from types import SimpleNamespace

import pytest

from bot_wumtaaba.annotations import ASSIGNMENT_TTL, handle_annotation
from bot_wumtaaba.annotation_store import apply_annotation_operation
from bot_wumtaaba.portal import PortalConversation

OWNER = "22670000001"
OTHER = "22670000002"
SAMPLE = {
    "id": "conte-1",
    "dataset": "example/asr-benchmark",
    "revision": "a" * 40,
    "sha256": "b" * 64,
    "proposals": {
        "A": {"text": "nin kẽem a yembr", "model_id": "example/whisper"},
        "B": {"text": "nin kẽem a yembre", "model_id": "example/wav2vec"},
    },
}


def choose(action, kind="button_reply"):
    return {"id": "incoming-test", "type": "interactive", "interactive": {kind: {"id": action}}}


def message(body):
    return {"id": "incoming-text", "type": "text", "text": {"body": body}}


def assign(store, sender=OWNER, task="002"):
    now = time.time()
    content = {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": "A : nin kẽem a yembr\nB : nin kẽem a yembre"},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": f"annpreview:{task}:{value}", "title": value}}
                    for value in ("A", "B", "tie")
                ]
            },
        },
    }
    with store.lock, store.db:
        outbox = store.queue_outbox(sender, content)
        store.db.execute(
            "UPDATE outbox SET status='sent',remote_id=%s WHERE id=%s", (f"wamid.fixture.{outbox}", outbox)
        )
        store.db.execute(
            "INSERT INTO annotation_tasks(task_id,kind,sample,audio_path,audio_sha256,audio_mime,created_at) "
            "VALUES(%s,'asr_preference',%s,%s,%s,'audio/flac',%s) ON CONFLICT DO NOTHING",
            (task, json.dumps(SAMPLE), f"annotations/{task}/audio.flac", SAMPLE["sha256"], now),
        )
        store.db.execute(
            "INSERT INTO annotation_assignments(recipient,task_id,source_outbox_id,assigned_at) "
            "VALUES(%s,%s,%s,%s)",
            (sender, task, outbox, now),
        )
    return outbox


async def start(store, state=None):
    return await handle_annotation(store, OWNER, choose("annpreview:002:A"), state or {})


async def test_real_preview_button_is_routed_before_business_engine(store):
    assign(store)

    class UnavailableLegacy:
        async def handle(self, *args):
            raise AssertionError("Annotation button reached the agreements engine")

    bot = PortalConversation(SimpleNamespace(), None, None, store, UnavailableLegacy())
    original = {"portal": "wuumtaaba", "legacy": {"draft": {"client": "Awa"}}}
    snapshot = deepcopy(original)
    state, replies, operation = await bot.handle(OWNER, choose("annpreview:002:A"), original)
    assert original == snapshot
    assert state["legacy"] == snapshot["legacy"]
    assert operation["_operation"] == "annotation"
    assert operation["preference"] == "A" and operation["sample"] == SAMPLE
    assert state["annotation_active"]["stage"] == "rate_A"
    rows = replies[0]["interactive"]["action"]["sections"][0]["rows"]
    assert [row["id"] for row in rows] == [f"ann:002:rate:A:{n}" for n in range(1, 6)]


async def test_complete_flow_records_two_ratings_and_personal_correction(store):
    assign(store)
    state, _, _ = await start(store)
    state, _, operation = await handle_annotation(
        store, OWNER, choose("ann:002:rate:A:4", "list_reply"), state
    )
    assert operation["ratings"] == {"A": 4, "B": None}
    state, _, operation = await handle_annotation(store, OWNER, message("3"), state)
    assert operation["ratings"] == {"A": 4, "B": 3}
    assert state["annotation_active"]["stage"] == "correction"
    state, replies, operation = await handle_annotation(
        store, OWNER, message("nin kẽem a yembr [inaudible]"), state
    )
    assert operation["status"] == "completed"
    assert operation["correction"] == "nin kẽem a yembr [inaudible]"
    assert state["annotation_active"]["stage"] == "done"
    assert replies[0]["type"] == "text"


async def test_skipped_ratings_and_correction_are_not_fabricated(store):
    assign(store)
    state, _, _ = await start(store)
    state, _, _ = await handle_annotation(store, OWNER, message("PASSER"), state)
    state, _, _ = await handle_annotation(store, OWNER, choose("ann:002:skip"), state)
    _, _, operation = await handle_annotation(store, OWNER, choose("ann:002:skip"), state)
    assert operation["status"] == "completed"
    assert operation["ratings"] == {"A": None, "B": None}
    assert operation["correction"] is None


@pytest.mark.parametrize("value", ["0", "6", "4.5", "cinq"])
async def test_out_of_range_or_ambiguous_rating_does_not_advance(store, value):
    assign(store)
    state, _, _ = await start(store)
    snapshot = deepcopy(state)
    result, _, operation = await handle_annotation(store, OWNER, message(value), state)
    assert result == state == snapshot
    assert operation is None


async def test_other_recipient_cannot_use_known_task_id(store):
    assign(store)
    state, _, operation = await handle_annotation(store, OTHER, choose("annpreview:002:A"), {})
    assert state == {} and operation is None


@pytest.mark.parametrize(
    "mutation", ["pending", "no_remote_id", "cancelled", "expired", "future", "stopped", "disabled"]
)
async def test_assignment_must_be_live_and_really_sent(store, mutation):
    outbox = assign(store)
    with store.lock, store.db:
        if mutation == "pending":
            store.db.execute("UPDATE outbox SET status='pending' WHERE id=%s", (outbox,))
        elif mutation == "no_remote_id":
            store.db.execute("UPDATE outbox SET remote_id=NULL WHERE id=%s", (outbox,))
        elif mutation == "cancelled":
            store.db.execute("UPDATE annotation_assignments SET status='cancelled'")
        elif mutation in {"expired", "future"}:
            stamp = time.time() - ASSIGNMENT_TTL - 1 if mutation == "expired" else time.time() + 3600
            store.db.execute("UPDATE annotation_assignments SET assigned_at=%s", (stamp,))
        elif mutation == "stopped":
            store.db.execute("UPDATE contacts SET reminders_enabled=0 WHERE id=%s", (OWNER,))
        else:
            store.db.execute("UPDATE annotation_tasks SET enabled=0")
    state, _, operation = await start(store)
    assert state == {} and operation is None


async def test_known_card_cannot_invert_the_model_mapping(store):
    assign(store)
    sample = deepcopy(SAMPLE)
    sample["proposals"]["A"], sample["proposals"]["B"] = sample["proposals"]["B"], sample["proposals"]["A"]
    with store.lock, store.db:
        store.db.execute("UPDATE annotation_tasks SET sample=%s", (json.dumps(sample),))
    _, _, operation = await start(store)
    assert operation is None


async def test_wrong_task_and_wrong_stage_do_not_change_active_answers(store):
    assign(store)
    assign(store, task="030")
    state, _, _ = await start(store)
    snapshot = deepcopy(state)
    for action in ("ann:030:rate:A:4", "ann:002:rate:B:4"):
        result, _, operation = await handle_annotation(store, OWNER, choose(action, "list_reply"), state)
        assert result == snapshot and state == snapshot and operation is None


async def test_repeated_preference_does_not_reset_existing_notes(store):
    assign(store)
    state, _, _ = await start(store)
    state, _, _ = await handle_annotation(store, OWNER, message("4"), state)
    snapshot = deepcopy(state)
    result, _, operation = await handle_annotation(store, OWNER, choose("annpreview:002:B"), state)
    assert result == snapshot and state == snapshot and operation is None


async def test_other_bot_actions_and_stop_keep_their_existing_route(store):
    assign(store)
    state, _, _ = await start(store)
    snapshot = deepcopy(state)
    for inbound in [
        message("STOP"),
        message("menu"),
        message("annuler"),
        choose("new"),
        {"type": "audio", "audio": {"id": "123"}},
    ]:
        assert await handle_annotation(store, OWNER, inbound, state) is None
    assert state == snapshot


async def test_correction_limit_is_enforced_without_dropping_the_draft(store):
    assign(store)
    state, _, _ = await start(store)
    state, _, _ = await handle_annotation(store, OWNER, message("PASSER"), state)
    state, _, _ = await handle_annotation(store, OWNER, message("PASSER"), state)
    snapshot = deepcopy(state)
    result, _, operation = await handle_annotation(store, OWNER, message("x" * 4001), state)
    assert result == snapshot and operation is None
    _, _, operation = await handle_annotation(store, OWNER, message("x" * 4000), state)
    assert operation["status"] == "completed" and len(operation["correction"]) == 4000


async def test_an_old_audit_entry_does_not_replace_a_durable_assignment(store):
    assign(store)
    with store.lock, store.db:
        store.db.execute("DELETE FROM annotation_assignments")
        store._audit("annotation_eval_preview_queued", "old-demo", {"sample": SAMPLE})
    _, _, operation = await start(store)
    assert operation is None


async def test_completed_response_cannot_be_reopened_by_an_old_preference_button(store):
    assign(store)
    state, _, _ = await start(store)
    for _ in range(3):
        state, _, operation = await handle_annotation(store, OWNER, message("PASSER"), state)
    with store.lock, store.db:
        apply_annotation_operation(store.db, OWNER, "completed-message", operation)
    result, _, operation = await handle_annotation(store, OWNER, choose("annpreview:002:B"), {})
    assert result == {} and operation is None
    saved = store.db.execute("SELECT preference,status FROM annotation_responses").fetchone()
    assert saved["preference"] == "A" and saved["status"] == "completed"
