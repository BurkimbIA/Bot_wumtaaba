from unittest.mock import AsyncMock

import pytest

from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.domain import Draft
from bot_wumtaaba.store import Store


def msg(body):
    return {"type": "text", "text": {"body": body}}


def button(value):
    return {"type": "interactive", "interactive": {"button_reply": {"id": value}}}


@pytest.fixture
def setup():
    store = Store(":memory:")
    wa, ai = AsyncMock(), AsyncMock()
    ai.extract.return_value = Draft(
        client="Awa", product="Riz", total=30000, paid=10000, due_date="2026-12-20"
    )
    ai.transcribe.return_value = "Awa achète du riz."
    wa.audio.return_value = (b"audio", "audio.ogg", "audio/ogg")
    yield Conversation(Settings(), wa, ai, store), store, wa, ai
    store.close()


async def test_confirmation_only_and_stale_buttons(setup):
    bot, _, _, _ai = setup
    state, _, agreement = await bot.handle("alice", button("new"), {})
    state, _, agreement = await bot.handle("alice", msg("Ma vente"), state)
    assert agreement is None
    old = state["confirmation"]
    state, _, _ = await bot.handle("alice", msg("Corrige le prix"), state)
    new = state["confirmation"]
    assert old != new
    state, replies, agreement = await bot.handle("alice", button(f"confirm:{old}"), state)
    assert agreement is None and "expiré" in str(replies)
    state, _, agreement = await bot.handle("alice", button(f"confirm:{new}"), state)
    assert agreement["total"] == 30000 and state == {}
    _, _, duplicate = await bot.handle("alice", button(f"confirm:{new}"), state)
    assert duplicate is None


async def test_audio_requires_specific_consent_and_shows_transcription(setup):
    bot, _, wa, ai = setup
    state, _, _ = await bot.handle("alice", {"type": "audio", "audio": {"id": "123"}}, {})
    wa.audio.assert_not_called()
    ai.transcribe.assert_not_called()
    _, _, _ = await bot.handle("alice", button("audio:wrong"), state)
    wa.audio.assert_not_called()
    token = state["pending_audio"]["token"]
    state, replies, agreement = await bot.handle("alice", button(f"audio:{token}"), state)
    wa.audio.assert_awaited_once_with("123")
    assert "Awa achète" in str(replies) and agreement is None
    assert "pending_audio" not in state
    ai.extract.assert_not_called()


async def test_audio_in_draft_and_refusal(setup):
    bot, _, wa, _ai = setup
    state = {"mode": "draft", "draft": {}}
    state, _, _ = await bot.handle("alice", {"type": "audio", "audio": {"id": "123"}}, state)
    token = state["pending_audio"]["token"]
    state, _, _ = await bot.handle("alice", button(f"refuse:{token}"), state)
    wa.audio.assert_not_called()
    state, _, _ = await bot.handle("alice", {"type": "audio", "audio": {"id": "456"}}, state)
    token = state["pending_audio"]["token"]
    state, replies, agreement = await bot.handle("alice", button(f"audio:{token}"), state)
    assert state["confirmation"] and agreement is None
    assert "Transcription" in str(replies) and "Accord à vérifier" in str(replies)


async def test_missing_information_never_saved(setup):
    bot, _, _, ai = setup
    ai.extract.return_value = Draft(client="Awa")
    state, replies, agreement = await bot.handle("alice", msg("Awa"), {"mode": "draft"})
    assert not state.get("confirmation") and agreement is None
    assert "produit" in str(replies)


async def test_store_atomic_and_owner_scoped(setup):
    _bot, store, _, _ = setup
    assert store.enqueue("m1", "alice", msg("hello"))
    assert not store.enqueue("m1", "alice", msg("hello"))
    job = store.claim_inbox()
    assert job["attempts"] == 1
    agreement = {
        "id": "a1",
        "client": "Awa",
        "product": "Riz",
        "total": 30000,
        "paid": 10000,
        "due_date": "2026-12-20",
    }
    store.finish_inbox("m1", "alice", {"draft": {}}, [{"type": "text"}], agreement)
    assert store.get_session("alice") == {"draft": {}}
    assert len(store.list_agreements("alice")) == 1
    assert store.list_agreements("bob") == []
    assert store.claim_inbox() is None
    outgoing = store.claim_outbox()
    store.recover()
    assert store.claim_outbox() is None  # crash during send is uncertain, never resent
    assert (
        store.db.execute("SELECT status FROM outbox WHERE id=?", (outgoing["id"],)).fetchone()[0]
        == "uncertain"
    )
    with pytest.raises(ValueError):
        store.finish_inbox("m1", "alice", {}, [], agreement)


async def test_failed_correction_invalidates_old_confirmation(setup):
    import httpx

    bot, _, _, ai = setup
    ai.extract.side_effect = httpx.ConnectError("offline")
    state, replies, agreement = await bot.handle(
        "alice",
        msg("Non, c’est 10 000"),
        {"mode": "draft", "confirmation": "old", "draft": {}},
    )
    assert "confirmation" not in state and agreement is None
    assert "Aucun accord enregistré" in str(replies)


def test_inbox_retry_blocks_same_sender_but_not_others(setup):
    _, store, _, _ = setup
    store.enqueue("first", "alice", msg("1"))
    store.enqueue("second", "alice", msg("2"))
    store.enqueue("third", "bob", msg("3"))
    assert store.claim_inbox()["id"] == "first"
    store.retry_inbox("first", "retry later")
    assert store.claim_inbox()["id"] == "third"
    assert store.claim_inbox() is None
