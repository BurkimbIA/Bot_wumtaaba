from pathlib import Path
from types import SimpleNamespace
from unittest.mock import AsyncMock, Mock

import pytest

from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.media import archive_audio


def message(body):
    return {"type": "text", "text": {"body": body}}


def button(action):
    return {"type": "interactive", "interactive": {"button_reply": {"id": action}}}


@pytest.fixture
def bot(tmp_path):
    settings = SimpleNamespace(media_directory=tmp_path, senders={"merchant"})
    store = Mock()
    store.is_known_customer.return_value = False
    wa, intelligence = AsyncMock(), AsyncMock()
    wa.audio.return_value = (b"private audio", "audio.ogg", "audio/ogg")
    intelligence.transcribe.return_value = "Bonjour le client"
    return Conversation(settings, wa, intelligence, store)


async def test_voice_archive_bound_to_original_message_and_consent(bot):
    state, replies, _ = await bot.handle("merchant", {
        "id": "wamid-original", "type": "audio", "audio": {"id": "123"}
    }, {})
    assert "conservé en privé" in str(replies)
    assert list(bot.settings.media_directory.iterdir()) == []
    await bot.handle("merchant", button("audio:obsolete"), state)
    bot.whatsapp.audio.assert_not_awaited()
    token = state["pending_audio"]["token"]
    _, replies, _ = await bot.handle("merchant", button("audio:" + token), state)
    path = next(bot.settings.media_directory.iterdir())
    assert path.read_bytes() == b"private audio"
    assert path.stat().st_mode & 0o777 == 0o600
    calls = bot.store.record_media.call_args_list
    assert all(call.args[0] == "wamid-original" for call in calls)
    assert calls[0].kwargs["metadata"]["storage_path"] == path.name
    assert calls[1].kwargs["transcript"] == "Bonjour le client"
    assert "Bonjour le client" in str(replies)


async def test_failed_transcription_keeps_file_and_records_failure(bot):
    bot.intelligence.transcribe.side_effect = ValueError("invalid provider JSON")
    state, _, _ = await bot.handle("merchant", {
        "id": "original", "type": "audio", "audio": {"id": "123"}
    }, {})
    _, replies, _ = await bot.handle("merchant", button("audio:" + state["pending_audio"]["token"]), state)
    assert len(list(bot.settings.media_directory.iterdir())) == 1
    assert bot.store.record_media.call_args.kwargs["metadata"]["status"] == "failed"
    assert "invalid provider" not in str(replies)


def test_archive_deterministic_private_and_no_path_injection(tmp_path):
    first = archive_audio(tmp_path, "../../evil", b"voice", "audio/ogg")
    second = archive_audio(tmp_path, "../../evil", b"voice", "audio/ogg")
    assert first == second
    assert len(list(tmp_path.iterdir())) == 1
    assert Path(first["storage_path"]).name == first["storage_path"]


async def test_client_consent_and_refusal_are_reference_scoped(bot):
    await bot.handle("customer", message("ACCEPTER WT-A1B2C3D4"), {})
    bot.store.set_customer_consent.assert_called_once_with("customer", "WT-A1B2C3D4", True)
    await bot.handle("customer", message("REFUSER WT-A1B2C3D4"), {})
    bot.store.set_customer_consent.assert_called_with("customer", "WT-A1B2C3D4", False)
    bot.store.set_reminders_enabled.assert_not_called()


async def test_stop_does_not_implicitly_reenable_on_menu(bot):
    await bot.handle("merchant", message("STOP"), {})
    await bot.handle("merchant", message("menu"), {})
    bot.store.set_reminders_enabled.assert_called_once_with("merchant", False)
    await bot.handle("merchant", message("rappels oui"), {})
    bot.store.set_reminders_enabled.assert_called_with("merchant", True)


async def test_known_client_cannot_create_agreements(bot):
    bot.store.is_known_customer.return_value = True
    state, replies, agreement = await bot.handle("customer", button("new"), {})
    assert state == {} and agreement is None
    assert "ACCEPTER" in str(replies)


async def test_settled_command_uses_ownership_store_method(bot):
    await bot.handle("merchant", message("payé WT-A1B2C3D4"), {})
    bot.store.settle_agreement.assert_called_once_with("merchant", "WT-A1B2C3D4")


async def test_known_customer_can_archive_voice_without_merchant_draft(bot):
    bot.store.is_known_customer.return_value = True
    state, _, _ = await bot.handle("customer", {
        "id": "customer-audio", "type": "audio", "audio": {"id": "123"}
    }, {"mode": "draft", "draft": {"client": "should be discarded"}, "confirmation": "old"})
    assert "draft" not in state and "mode" not in state
    bot.whatsapp.audio.assert_not_awaited()
    state, replies, agreement = await bot.handle(
        "customer", button("audio:" + state["pending_audio"]["token"]), state
    )
    assert "Bonjour le client" in str(replies)
    assert agreement is None and state == {}
    assert all(reply["type"] == "text" for reply in replies)
    bot.intelligence.extract.assert_not_awaited()
    assert bot.store.record_media.call_args.args[0] == "customer-audio"


@pytest.mark.parametrize("body", ["calendrier", "mes accords", "payé WT-A1B2C3D4"])
async def test_customer_audio_access_does_not_unlock_merchant_commands(bot, body):
    bot.store.is_known_customer.return_value = True
    state, _, agreement = await bot.handle("customer", message(body), {})
    assert state == {} and agreement is None
    bot.store.settle_agreement.assert_not_called()
    bot.store.calendar_for.assert_not_called()
    bot.store.list_agreements.assert_not_called()
    bot.store.set_reminders_enabled.assert_not_called()


async def test_customer_can_explicitly_reenable_and_needs_per_agreement_consent(bot):
    bot.store.is_known_customer.return_value = True
    await bot.handle("customer", message("STOP"), {})
    _, replies, _ = await bot.handle("customer", message("rappels oui"), {})
    bot.store.set_reminders_enabled.assert_called_with("customer", True)
    bot.store.set_customer_consent.assert_not_called()
    assert "ACCEPTER" in str(replies)


async def test_consent_with_global_stop_does_not_claim_reminders_are_active(bot):
    bot.store.reminders_enabled.return_value = False
    _, replies, _ = await bot.handle("customer", message("ACCEPTER WT-A1B2C3D4"), {})
    assert "restent désactivés" in str(replies)
    bot.store.set_reminders_enabled.assert_not_called()


async def test_public_customer_can_start_own_draft_and_enable_reminders(bot):
    bot.settings.public_access = True
    bot.store.is_known_customer.return_value = True
    state, _replies, agreement = await bot.handle('new-public-user', button('new'), {})
    assert state['mode'] == 'draft' and agreement is None
    await bot.handle('new-public-user', message('rappels oui'), {})
    bot.store.set_reminders_enabled.assert_called_once_with('new-public-user', True)
