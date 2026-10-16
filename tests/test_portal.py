from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.portal import PortalConversation
from bot_wumtaaba.store import Store


def message(body):
    return {"type": "text", "text": {"body": body}}


def choose(action, *, listed=False):
    return {"type": "interactive", "interactive": {
        "list_reply" if listed else "button_reply": {"id": action}}}


@pytest.fixture
def setup(tmp_path):
    store = Store(":memory:")
    settings = Settings(_env_file=None, allowed_senders="22670000000",
                        media_directory=tmp_path / "media")
    wa, api, intelligence = AsyncMock(), AsyncMock(), AsyncMock()
    api.yaaba_available = False
    api.translate.return_value = "Ne y yibeogo"
    api.transcribe.return_value = "Mooré transcript"
    api.synthesize.return_value = (b"sound", "voice.mp3", "audio/mpeg")
    wa.upload_audio.return_value = "123456"
    wa.audio.return_value = (b"sound", "voice.ogg", "audio/ogg")
    legacy = Conversation(settings, wa, intelligence, store)
    bot = PortalConversation(settings, wa, api, store, legacy)
    yield SimpleNamespace(bot=bot, store=store, wa=wa, api=api, legacy=legacy)
    store.close()


async def test_root_and_list_navigation(setup):
    state, replies, agreement = await setup.bot.handle("22670000000", message("Bonjour"), {})
    assert state == {} and agreement is None
    assert [b["reply"]["title"] for b in replies[0]["interactive"]["action"]["buttons"]] == [
        "BurkimbIA", "Wuumtaaba"]
    state, replies, _ = await setup.bot.handle("22670000000", choose("portal:burkimbia"), state)
    assert len(replies[0]["interactive"]["action"]["sections"][0]["rows"]) == 4
    state, _, _ = await setup.bot.handle("22670000000", choose("bi:translate", listed=True), state)
    state, _, _ = await setup.bot.handle("22670000000", choose("bi:mo-fr"), state)
    state, replies, _ = await setup.bot.handle("22670000000", message("Texte mooré"), state)
    setup.api.translate.assert_awaited_once_with("Texte mooré", source="moore", target="french")
    assert "Ne y yibeogo" in str(replies)
    assert state["source"] == "moore"


async def test_old_draft_preserved_and_global_stop(setup):
    state, _, _ = await setup.bot.handle("22670000000", choose("help"), {"mode": "draft", "draft": {}})
    assert state["legacy"]["mode"] == "draft"
    state, _, _ = await setup.bot.handle("22670000000", message("STOP"),
                                         {"portal": "burkimbia", "feature": "yaaba"})
    assert state == {} and not setup.store.reminders_enabled("22670000000")
    setup.api.chat.assert_not_called()


async def test_wuumtaaba_delegates_and_root_cancels(setup):
    state, replies, _ = await setup.bot.handle("22670000000", choose("portal:wuumtaaba"), {})
    assert state["portal"] == "wuumtaaba" and "Nouvel accord" in str(replies)
    state, _, _ = await setup.bot.handle("22670000000", choose("new"), state)
    assert state["legacy"]["mode"] == "draft"
    state, _, _ = await setup.bot.handle("22670000000", message("menu"), state)
    assert state == {}
    _, _, agreement = await setup.bot.handle("22670000000", choose("confirm:old"), state)
    assert agreement is None


async def test_stale_burkimbia_selection_does_not_enter_feature(setup):
    state, _, _ = await setup.bot.handle("22670000000", choose("bi:translate"), {})
    assert state == {}
    setup.api.translate.assert_not_called()


async def test_synthesis_queues_uploaded_media(setup):
    _state, replies, agreement = await setup.bot.handle("22670000000", message("Ne y yibeogo"),
        {"portal": "burkimbia", "feature": "synthesize"})
    setup.api.synthesize.assert_awaited_once_with("Ne y yibeogo", language="moore")
    setup.wa.upload_audio.assert_awaited_once_with(b"sound", "voice.mp3", "audio/mpeg")
    assert replies[0]["audio"] == {"id": "123456"} and agreement is None
    metadata = replies[0]["_local_audio"]
    assert metadata["provider"] == "burkimbia"
    assert metadata["source_text"] == "Ne y yibeogo"
    path = setup.bot.settings.media_directory / metadata["storage_path"]
    assert path.read_bytes() == b"sound"
    assert path.stat().st_mode & 0o777 == 0o600


async def test_audio_consent_archive_and_transcript(setup):
    incoming = {"id": "voice1", "from": "22670000000", "type": "audio", "audio": {"id": "987"}}
    setup.store.enqueue("voice1", "22670000000", incoming)
    state, replies, _ = await setup.bot.handle("22670000000", incoming,
        {"portal": "burkimbia", "feature": "transcribe"})
    assert "BurkimbIA" in str(replies)
    setup.wa.audio.assert_not_called()
    setup.api.transcribe.assert_not_called()
    _, replies, _ = await setup.bot.handle("22670000000", choose("bi-audio:wrong"), state)
    assert "expiré" in str(replies)
    setup.wa.audio.assert_not_called()
    state, replies, _ = await setup.bot.handle("22670000000", choose(
        "bi-audio:" + state["pending_audio"]["token"]), state)
    assert "pending_audio" not in state and "Mooré transcript" in str(replies)
    row = setup.store.db.execute("SELECT transcript,storage_path FROM media_assets WHERE message_id='in:voice1'").fetchone()
    assert row["transcript"] == "Mooré transcript"
    assert (setup.bot.settings.media_directory / row["storage_path"]).read_bytes() == b"sound"


async def test_audio_refusal_and_switch_invalidate(setup):
    incoming = {"type": "audio", "audio": {"id": "987"}}
    state, _, _ = await setup.bot.handle("22670000000", incoming,
        {"portal": "burkimbia", "feature": "transcribe"})
    token = state["pending_audio"]["token"]
    state, _, _ = await setup.bot.handle("22670000000", choose("bi-refuse:" + token), state)
    assert "pending_audio" not in state
    setup.wa.audio.assert_not_called()
    state, _, _ = await setup.bot.handle("22670000000", choose("bi:synthesize"), state)
    await setup.bot.handle("22670000000", choose("bi-audio:" + token), state)
    setup.wa.audio.assert_not_called()


async def test_yaaba_unavailable_honestly_and_safe_provider_error(setup):
    _state, replies, _ = await setup.bot.handle("22670000000", choose("bi:yaaba"), {"portal": "burkimbia"})
    assert "pas encore disponible" in str(replies)
    setup.api.chat.assert_not_called()
    setup.api.translate.side_effect = httpx.ConnectError("SECRET endpoint failure")
    _, replies, _ = await setup.bot.handle("22670000000", message("Bonjour à tous"),
        {"portal": "burkimbia", "feature": "translate", "source": "french", "target": "moore"})
    assert "SECRET" not in str(replies) and "pas pu traiter" in str(replies)


async def test_customer_authorization_preserved(setup):
    setup.store.is_known_customer = lambda sender: sender == "22671111111"
    state, replies, _ = await setup.bot.handle("22671111111", choose("portal:wuumtaaba"), {})
    assert "ACCEPTER" in str(replies)
    state, replies, agreement = await setup.bot.handle("22671111111", choose("new"), state)
    assert agreement is None and not state["legacy"].get("draft")
    assert "ACCEPTER" in str(replies)


async def test_services_resets_pending_and_feature(setup):
    state, replies, _ = await setup.bot.handle("22670000000", message("services"),
        {"portal": "burkimbia", "feature": "transcribe",
         "pending_audio": {"token": "old", "id": "123"}})
    assert state == {"portal": "burkimbia"}
    assert replies[0]["interactive"]["type"] == "list"
    await setup.bot.handle("22670000000", choose("bi-audio:old"), state)
    setup.wa.audio.assert_not_called()


async def test_switch_discards_old_legacy_confirmation(setup):
    state = {"portal": "wuumtaaba", "legacy": {"mode": "draft", "confirmation": "abc",
        "draft": {"client": "Awa", "product": "Riz", "total": 10000, "paid": 5000,
                  "due_date": "2026-12-20"}}}
    state, _, _ = await setup.bot.handle("22670000000", choose("portal:burkimbia"), state)
    state, _, _ = await setup.bot.handle("22670000000", choose("portal:wuumtaaba"), state)
    state, replies, agreement = await setup.bot.handle("22670000000", choose("confirm:abc"), state)
    assert agreement is None and "expiré" in str(replies)
    assert setup.store.list_agreements("22670000000") == []


@pytest.mark.parametrize("feature", ["translate", "synthesize"])
async def test_text_limit_before_provider_call(setup, feature):
    _, replies, _ = await setup.bot.handle("22670000000", message("x" * 513),
        {"portal": "burkimbia", "feature": feature, "source": "french", "target": "moore"})
    assert "512" in str(replies)
    setup.api.translate.assert_not_called()
    setup.api.synthesize.assert_not_called()


async def test_transcription_prompt_explains_limit(setup):
    _, replies, _ = await setup.bot.handle("22670000000", choose("bi:transcribe"),
                                           {"portal": "burkimbia"})
    assert "60 secondes" in str(replies) and "7 Mo" in str(replies)


async def test_keyboard_translation_directions_and_literal_feature_word(setup):
    state, _, _ = await setup.bot.handle("22670000000", message("traduction"), {"portal": "burkimbia"})
    assert state["feature"] == "translate"
    state, _, _ = await setup.bot.handle("22670000000", message("/fr-mo"), state)
    assert state["source"] == "french" and state["target"] == "moore"
    state, _, _ = await setup.bot.handle("22670000000", message("yaaba"), state)
    setup.api.translate.assert_awaited_once_with("yaaba", source="french", target="moore")
    state, _, _ = await setup.bot.handle("22670000000", message("/mo-fr"), state)
    assert state["source"] == "moore" and state["target"] == "french"


@pytest.mark.parametrize("command,feature", [
    ("/traduction", "translate"), ("/transcription", "transcribe"), ("/voix", "synthesize"),
])
async def test_slash_commands_switch_active_feature(setup, command, feature):
    state, _, _ = await setup.bot.handle("22670000000", message(command),
        {"portal": "burkimbia", "feature": "transcribe", "pending_audio": {"token": "expired"}})
    assert state == {"portal": "burkimbia", "feature": feature}
    setup.api.transcribe.assert_not_called()


@pytest.mark.parametrize("command", ["synthèse vocale", "synthese vocale"])
async def test_natural_synthesis_alias_only_without_feature(setup, command):
    state, _, _ = await setup.bot.handle("22670000000", message(command), {"portal": "burkimbia"})
    assert state["feature"] == "synthesize"


async def test_keyboard_commands_do_not_escape_wuumtaaba(setup):
    state, _, _ = await setup.bot.handle("22670000000", message("/voix"),
                                       {"portal": "wuumtaaba", "legacy": {}})
    assert state["portal"] == "wuumtaaba"
    setup.api.synthesize.assert_not_called()


async def test_keyboard_yaaba_unavailable_honestly(setup):
    _, replies, _ = await setup.bot.handle("22670000000", message("/yaaba"), {"portal": "burkimbia"})
    assert "pas encore disponible" in str(replies)
    setup.api.chat.assert_not_called()


async def test_keyboard_audio_consent_latest_pending_only(setup):
    state = {"portal": "burkimbia", "feature": "transcribe"}
    state, replies, _ = await setup.bot.handle("22670000000", {"type": "audio", "audio": {"id": "111"}}, state)
    old = state["pending_audio"]["token"]
    assert "/accepter-vocal" in str(replies) and "dernier vocal reçu" in str(replies)
    state, _, _ = await setup.bot.handle("22670000000", {"type": "audio", "audio": {"id": "222"}}, state)
    state, replies, _ = await setup.bot.handle("22670000000", choose("bi-audio:" + old), state)
    assert "expiré" in str(replies)
    setup.wa.audio.assert_not_called()
    state, _, _ = await setup.bot.handle("22670000000", message("/accepter-vocal"), state)
    setup.wa.audio.assert_awaited_once_with("222")
    assert "pending_audio" not in state


async def test_keyboard_audio_refusal_does_not_download(setup):
    state = {"portal": "burkimbia", "feature": "transcribe"}
    state, _, _ = await setup.bot.handle("22670000000", {"type": "audio", "audio": {"id": "111"}}, state)
    state, _, _ = await setup.bot.handle("22670000000", message("/refuser-vocal"), state)
    assert "pending_audio" not in state
    setup.wa.audio.assert_not_called()
    setup.api.transcribe.assert_not_called()


@pytest.mark.parametrize("state", [
    {"portal": "burkimbia", "feature": "transcribe"},
    {"portal": "burkimbia", "feature": "translate", "pending_audio": {"token": "x", "id": "123"}},
    {"portal": "wuumtaaba", "legacy": {}},
    {},
])
@pytest.mark.parametrize("command", ["/accepter-vocal", "/refuser-vocal"])
async def test_keyboard_consent_outside_pending_transcription_does_nothing(setup, state, command):
    await setup.bot.handle("22670000000", message(command), state)
    setup.wa.audio.assert_not_called()
    setup.api.transcribe.assert_not_called()


@pytest.mark.parametrize("mime", ["audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm"])
async def test_audio_document_requires_consent_then_downloads(setup, mime):
    incoming = {"id": "audio-doc", "type": "document", "document": {
        "id": "987", "mime_type": mime, "filename": "audio.mp3"}}
    setup.store.enqueue("audio-doc", "22670000000", incoming)
    state, _, _ = await setup.bot.handle("22670000000", incoming,
        {"portal": "burkimbia", "feature": "transcribe"})
    assert state["pending_audio"]["id"] == "987"
    setup.wa.audio.assert_not_called()
    setup.api.transcribe.assert_not_called()
    state, replies, _ = await setup.bot.handle("22670000000", message("/accepter-vocal"), state)
    setup.wa.audio.assert_awaited_once_with("987")
    assert "Mooré transcript" in str(replies)
    row = setup.store.db.execute("SELECT transcript FROM media_assets WHERE message_id='in:audio-doc'").fetchone()
    assert row["transcript"] == "Mooré transcript"


@pytest.mark.parametrize("mime", ["application/pdf", "text/html", "application/octet-stream"])
async def test_non_audio_document_never_requests_consent_or_downloads(setup, mime):
    state, _, _ = await setup.bot.handle("22670000000", {"type": "document", "document": {
        "id": "987", "mime_type": mime, "filename": "pretend.mp3"}},
        {"portal": "burkimbia", "feature": "transcribe"})
    assert "pending_audio" not in state
    await setup.bot.handle("22670000000", message("/accepter-vocal"), state)
    setup.wa.audio.assert_not_called()
    setup.api.transcribe.assert_not_called()


async def test_document_reported_audio_still_uses_validated_download(setup):
    from bot_wumtaaba.clients import MediaError

    state, _, _ = await setup.bot.handle("22670000000", {"type": "document", "document": {
        "id": "987", "mime_type": "audio/mpeg"}},
        {"portal": "burkimbia", "feature": "transcribe"})
    setup.wa.audio.side_effect = MediaError("Format audio non pris en charge")
    state, replies, _ = await setup.bot.handle("22670000000", message("/accepter-vocal"), state)
    assert "pending_audio" not in state and "pas pu traiter" in str(replies)
    setup.api.transcribe.assert_not_called()
