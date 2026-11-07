"""Single-carnet routing, including sessions saved before the portal was removed."""

from copy import deepcopy
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest

from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.domain import Draft
from bot_wumtaaba.portal import PortalConversation

OWNER = "22670000000"


def message(body):
    return {"type": "text", "text": {"body": body}}


def choose(action):
    return {"type": "interactive", "interactive": {"button_reply": {"id": action}}}


@pytest.fixture
def setup(make_store, media):
    store = make_store()
    settings = Settings(_env_file=None, allowed_senders=OWNER)
    wa, api, intelligence = AsyncMock(), AsyncMock(), AsyncMock()
    intelligence.extract.return_value = Draft(
        client="Test", product="Riz", total=30000, paid=10000, due_date="2026-12-20"
    )
    api.translate.return_value = "Vente de riz, 30000 francs, avance 10000."
    legacy = Conversation(settings, wa, intelligence, store, media, api)
    bot = PortalConversation(settings, wa, api, store, legacy, media)
    yield SimpleNamespace(bot=bot, store=store, api=api, wa=wa, intelligence=intelligence)
    store.close()


@pytest.mark.parametrize("command", ["menu", "bonjour", "salut", "accueil", "wuumtaaba", "wumtaaba"])
async def test_direct_carnet_menu(setup, command):
    state, replies, op = await setup.bot.handle(OWNER, message(command), {})
    assert state == {"portal": "wuumtaaba", "legacy": {}}
    assert op is None
    titles = [b["reply"]["title"] for b in replies[0]["interactive"]["action"]["buttons"]]
    assert titles == ["Nouvel accord", "Mes accords", "Ajouter paiement"]
    assert "Choisis ton espace" not in str(replies)


async def test_new_agreement_without_selecting_space(setup):
    state, replies, op = await setup.bot.handle(OWNER, message("nouvel accord"), {})
    assert state["legacy"]["mode"] == "draft"
    assert "Mooré" in str(replies) and op is None


@pytest.mark.parametrize("command", ["menu", "annuler", "accueil"])
async def test_navigation_cancels_old_confirmation(setup, command):
    original = {"portal": "wuumtaaba", "legacy": {"mode": "draft", "confirmation": "old"}}
    snapshot = deepcopy(original)
    state, replies, _ = await setup.bot.handle(OWNER, message(command), original)
    assert original == snapshot
    assert state["legacy"] == {} and "Nouvel accord" in str(replies)
    _, replies, op = await setup.bot.handle(OWNER, choose("confirm:old"), state)
    assert op is None and "expiré" in str(replies)


@pytest.mark.parametrize(
    "state",
    [
        {"mode": "draft", "draft": {}, "language": "moore"},
        {"portal": "wuumtaaba", "legacy": {"mode": "draft", "draft": {}, "language": "moore"}},
    ],
)
async def test_existing_moore_drafts_still_use_translation(setup, state):
    result, replies, op = await setup.bot.handle(OWNER, message("Description mooré"), state)
    setup.api.translate.assert_awaited_once_with("Description mooré", source="moore", target="french")
    setup.intelligence.extract.assert_awaited_once_with(setup.api.translate.return_value, {})
    assert result["legacy"]["language"] == "moore" and result["legacy"]["confirmation"]
    assert "Écouter en mooré" in str(replies) and op is None


@pytest.mark.parametrize(
    "action",
    ["portal:burkimbia", "portal:wuumtaaba", "portal:home", "bi:translate", "bi-audio:old", "bi-refuse:old"],
)
async def test_old_portal_buttons_return_to_carnet_without_provider_calls(setup, action):
    old = {"portal": "burkimbia", "feature": "transcribe", "pending_audio": {"id": "123", "token": "old"}}
    state, replies, op = await setup.bot.handle(OWNER, choose(action), old)
    assert state == {"portal": "wuumtaaba", "legacy": {}}
    assert "Nouvel accord" in str(replies) and op is None
    setup.api.translate.assert_not_called()
    setup.api.transcribe.assert_not_called()
    setup.wa.audio.assert_not_called()


async def test_old_burkimbia_session_discards_pending_audio(setup):
    state, replies, op = await setup.bot.handle(
        OWNER,
        message("Bonjour"),
        {"portal": "burkimbia", "feature": "transcribe", "pending_audio": {"token": "old", "id": "123"}},
    )
    assert state["legacy"] == {} and "Nouvel accord" in str(replies) and op is None
    setup.wa.audio.assert_not_called()


async def test_customer_permissions_and_stop_preserved(setup):
    setup.store.is_known_customer = lambda sender: sender == "22671111111"
    state, replies, op = await setup.bot.handle("22671111111", message("menu"), {})
    assert "ACCEPTER" in str(replies) and op is None
    _, replies, op = await setup.bot.handle("22671111111", choose("new"), state)
    assert "ACCEPTER" in str(replies) and op is None
    state, _, _ = await setup.bot.handle(OWNER, message("STOP"), {"portal": "burkimbia"})
    assert state == {} and not setup.store.reminders_enabled(OWNER)
