from unittest.mock import AsyncMock

import pytest

from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.domain import Draft


def msg(body):
    return {"type": "text", "text": {"body": body}}


def button(value):
    return {"type": "interactive", "interactive": {"button_reply": {"id": value}}}


@pytest.fixture
def setup(make_store, media):
    store = make_store()
    wa, ai = AsyncMock(), AsyncMock()
    ai.extract.return_value = Draft(
        client="Awa", product="Riz", total=30000, paid=10000, due_date="2026-12-20"
    )
    ai.transcribe.return_value = "Awa achète du riz."
    wa.audio.return_value = (b"audio", "audio.ogg", "audio/ogg")
    yield Conversation(Settings(), wa, ai, store, media), store, wa, ai
    store.close()


async def test_confirmation_only_and_stale_buttons(setup):
    bot, _, _, _ai = setup
    state, _, agreement = await bot.handle("alice", button("new"), {})
    state, _, _ = await bot.handle("alice", button("language:french:" + state["language_choice"]), state)
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


async def test_audio_is_transcribed_directly_without_saving(setup):
    bot, _, wa, ai = setup
    state, replies, agreement = await bot.handle("alice", {"type": "audio", "audio": {"id": "123"}}, {})
    wa.audio.assert_awaited_once_with("123")
    assert "Awa achète" in str(replies) and agreement is None
    assert "pending_audio" not in state
    assert "autorises-tu" not in str(replies)
    ai.extract.assert_not_called()


async def test_audio_in_draft_returns_transcription_and_unconfirmed_summary(setup):
    bot, _, wa, _ai = setup
    state = {"mode": "draft", "draft": {}}
    state, replies, agreement = await bot.handle("alice", {"type": "audio", "audio": {"id": "456"}}, state)
    wa.audio.assert_awaited_once_with("456")
    assert state["confirmation"] and agreement is None
    assert "Transcription" in str(replies) and "Accord à vérifier" in str(replies)


async def test_available_information_is_summarized_and_requires_confirmation(setup):
    bot, _, _, ai = setup
    ai.extract.return_value = Draft(client="Awa")
    state, replies, agreement = await bot.handle("alice", msg("Awa"), {"mode": "draft"})
    assert state.get("confirmation") and agreement is None
    assert "Non renseigné" in str(replies)
    assert "Valider sans numéro" in str(replies)
    assert "numéro du client" in str(replies)
    assert "Choisir le client" not in str(replies)
    state, replies, agreement = await bot.handle("alice", button("confirm:" + state["confirmation"]), state)
    assert agreement["client"] == "Awa" and agreement["total"] is None
    assert agreement["paid"] is None and agreement["due_date"] is None
    assert state == {} and "Aucun rappel programmé" in str(replies)


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
        store.db.execute("SELECT status FROM outbox WHERE id=%s", (outgoing["id"],)).fetchone()[0]
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


async def test_key_points_prompt_for_phone_and_allow_confirmation_without_it():
    ai = AsyncMock()
    ai.extract.return_value = Draft(key_points="Livraison à préciser avec Salif.")
    bot = Conversation(Settings(public_access=True), AsyncMock(), ai, AsyncMock())
    state, replies, agreement = await bot._extract("message incomplet", {}, [])
    assert agreement is None
    assert "numéro du client" in str(replies)
    assert "Valider sans numéro" in str(replies)
    state, _, agreement = await bot.handle("alice", button("confirm:" + state["confirmation"]), state)
    assert agreement["key_points"] == "Livraison à préciser avec Salif."
    assert agreement["client_phone"] is None
    assert agreement["total"] is None
    assert state == {}


async def test_phone_followup_keeps_key_points_and_replaces_confirmation():
    ai = AsyncMock()
    ai.extract.return_value = Draft(key_points="Livraison à préciser avec Salif.")
    bot = Conversation(Settings(public_access=True), AsyncMock(), ai, AsyncMock())
    state, _, _ = await bot._extract("message incomplet", {}, [])
    old_token = state["confirmation"]
    state, replies, _ = await bot.handle("alice", msg("+226 70 12 34 56"), state)
    assert state["confirmation"] != old_token
    assert "Quel est le numéro" not in str(replies)
    assert "Enregistrer" in str(replies)
    assert "Associer ce numéro" not in str(replies)
    assert "Choisir le client" not in str(replies)
    ai.extract.assert_awaited_once()
    _, _, agreement = await bot.handle("alice", button("confirm:" + state["confirmation"]), state)
    assert agreement["client_phone"] == "22670123456"
    assert agreement["key_points"] == "Livraison à préciser avec Salif."


def test_long_summary_does_not_truncate_phone_request():
    bot = Conversation(Settings(public_access=True), AsyncMock(), AsyncMock(), AsyncMock())
    draft = Draft(key_points="a" * 500, client="b" * 100, product="c" * 200,
                  total=1000000000, paid=1000000000, due_date="2026-12-20")
    _, replies, saved = bot._review(draft, {}, [])
    assert saved is None
    assert replies[0]["text"]["body"] == draft.summary()
    prompt = replies[1]["interactive"]["body"]["text"]
    assert "indicatif pays" in prompt and "valide sans numéro" in prompt
    assert len(prompt) <= 1024
