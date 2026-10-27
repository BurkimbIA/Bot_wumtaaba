import base64
import json
from types import SimpleNamespace
from unittest.mock import AsyncMock

import httpx
import pytest

from bot_wumtaaba.agreement_voice import pieces, spoken_summary, verify_spoken_amounts
from bot_wumtaaba.clients import Intelligence, MediaError
from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.domain import Draft
from bot_wumtaaba.store import Store


def msg(body):
    return {"type": "text", "text": {"body": body}}


def click(action):
    return {"type": "interactive", "interactive": {"button_reply": {"id": action}}}


@pytest.fixture
def setup(tmp_path):
    settings = Settings(public_access=True, media_directory=tmp_path / "media")
    store = Store(":memory:")
    wa, ai, bia = AsyncMock(), AsyncMock(), AsyncMock()
    draft = Draft(client="Awa", product="Riz", total=30000, paid=10000, due_date="2026-12-20")
    ai.extract.return_value = draft
    ai.transcribe.return_value = "Awa achète du riz pour 30000 francs."
    ai.synthesize_french.return_value = (b"french audio", "fr.mp3", "audio/mpeg")
    bia.transcribe.return_value = "Texte mooré issu du vocal"
    bia.translate.return_value = "Awa achète du riz pour 30000 francs, avance 10000."
    bia.synthesize.return_value = (b"moore audio", "mo.mp3", "audio/mpeg")
    wa.audio.return_value = (b"voice", "voice.ogg", "audio/ogg")
    wa.upload_audio.return_value = "987654"
    yield SimpleNamespace(
        bot=Conversation(settings, wa, ai, store, bia),
        store=store,
        wa=wa,
        ai=ai,
        bia=bia,
        draft=draft,
    )
    store.close()


async def begin(s, language="moore"):
    state, replies, op = await s.bot.handle("owner", click("new"), {})
    assert op is None and "Français" in str(replies) and "Mooré" in str(replies)
    return (
        await s.bot.handle(
            "owner", click(f"language:{language}:" + state["language_choice"]), state
        )
    )[0]


async def summary(s, language="moore"):
    state = await begin(s, language)
    return (await s.bot.handle("owner", msg("Description de la vente"), state))[0]


async def test_language_choice_required_before_any_processing_and_old_buttons_expire(setup):
    s = setup
    state, _, _ = await s.bot.handle("owner", click("new"), {})
    old = state["language_choice"]
    state, _, _ = await s.bot.handle("owner", {"type": "audio", "audio": {"id": "123"}}, state)
    assert "pending_audio" not in state
    s.wa.audio.assert_not_called()
    s.ai.extract.assert_not_called()
    s.bia.transcribe.assert_not_called()
    state, _, _ = await s.bot.handle("owner", msg("mooré"), state)
    assert state["language"] == "moore"
    state, replies, _ = await s.bot.handle("owner", click("language:french:" + old), state)
    assert state["language"] == "moore" and "expiré" in str(replies)


async def test_moore_audio_consent_transcription_translation_and_extract_order(setup):
    s = setup
    state = await begin(s)
    state, replies, _ = await s.bot.handle(
        "owner", {"id": "original", "type": "audio", "audio": {"id": "123"}}, state
    )
    assert "BurkimbIA" in str(replies) and "Google Gemini" in str(replies)
    s.wa.audio.assert_not_called()
    s.bia.transcribe.assert_not_called()
    state, replies, op = await s.bot.handle(
        "owner", click("audio:" + state["pending_audio"]["token"]), state
    )
    s.bia.transcribe.assert_awaited_once_with(b"voice", "voice.ogg", "audio/ogg", language="moore")
    s.bia.translate.assert_awaited_once_with(
        "Texte mooré issu du vocal", source="moore", target="french"
    )
    s.ai.transcribe.assert_not_called()
    s.ai.extract.assert_awaited_once_with(s.bia.translate.return_value, {})
    assert "Transcription mooré" in str(replies) and "Traduction française à vérifier" in str(
        replies
    )
    assert state["confirmation"] and op is None and s.store.list_agreements("owner") == []
    state, _, op = await s.bot.handle("owner", click("confirm:" + state["confirmation"]), state)
    assert op["language"] == "moore" and op["total"] == 30000 and state == {}


async def test_refused_moore_voice_never_downloaded(setup):
    s = setup
    state = await begin(s)
    state, _, _ = await s.bot.handle("owner", {"type": "audio", "audio": {"id": "123"}}, state)
    _, _, op = await s.bot.handle(
        "owner", click("refuse:" + state["pending_audio"]["token"]), state
    )
    s.wa.audio.assert_not_called()
    s.bia.transcribe.assert_not_called()
    s.ai.extract.assert_not_called()
    assert op is None


async def test_french_voice_uses_gemini_without_translation(setup):
    s = setup
    state = await begin(s, "french")
    state, _, _ = await s.bot.handle("owner", {"type": "audio", "audio": {"id": "123"}}, state)
    state, replies, op = await s.bot.handle(
        "owner", click("audio:" + state["pending_audio"]["token"]), state
    )
    s.ai.transcribe.assert_awaited_once()
    s.bia.transcribe.assert_not_called()
    s.bia.translate.assert_not_called()
    assert state["confirmation"] and op is None and "Transcription française" in str(replies)


async def test_translation_failure_preserves_original_and_prevents_extraction(setup):
    s = setup
    state = await begin(s)
    state, _, _ = await s.bot.handle("owner", {"type": "audio", "audio": {"id": "123"}}, state)
    s.bia.translate.side_effect = MediaError("Traduction indisponible")
    state, replies, op = await s.bot.handle(
        "owner", click("audio:" + state["pending_audio"]["token"]), state
    )
    assert "Texte mooré issu du vocal" in str(replies) and "indisponible" in str(replies)
    assert "confirmation" not in state and op is None
    s.ai.extract.assert_not_called()


async def test_moore_text_and_correction_failure_invalidate_previous_summary(setup):
    s = setup
    state = await summary(s)
    old = state["confirmation"]
    s.bia.translate.side_effect = MediaError("Erreur traduction")
    state, _, op = await s.bot.handle("owner", msg("Correction en mooré"), state)
    assert op is None and "confirmation" not in state
    _, replies, op = await s.bot.handle("owner", click("confirm:" + old), state)
    assert op is None and "expiré" in str(replies)


@pytest.mark.parametrize("language", ["french", "moore"])
async def test_summary_audio_is_opt_in_and_does_not_save_or_confirm(setup, language):
    s = setup
    state = await summary(s, language)
    s.ai.synthesize_french.assert_not_called()
    s.bia.synthesize.assert_not_called()
    if language == "moore":
        s.bia.translate.return_value = "Libellé mooré"
    state, replies, op = await s.bot.handle("owner", click("read:" + state["confirmation"]), state)
    assert op is None and state["confirmation"]
    assert replies[0]["audio"]["id"] == "987654"
    assert replies[0]["_local_audio"]["purpose"] == "agreement_summary"
    assert replies[0]["_local_audio"]["language"] == language
    assert s.store.list_agreements("owner") == []
    if language == "moore":
        s.bia.synthesize.assert_awaited_once()
        s.ai.synthesize_french.assert_not_called()
    else:
        s.ai.synthesize_french.assert_awaited_once()
        s.bia.synthesize.assert_not_called()
    _, _, agreement = await s.bot.handle("owner", click("confirm:" + state["confirmation"]), state)
    assert agreement["total"] == 30000


async def test_stale_readback_button_and_changed_amounts_never_generate_speech(setup):
    s = setup
    state = await summary(s)
    _, replies, _ = await s.bot.handle("owner", click("read:old"), state)
    assert "expiré" in str(replies)
    s.bia.synthesize.assert_not_called()
    s.bia.translate.return_value = "Une traduction avec seulement 90000 francs."
    _, replies, op = await s.bot.handle("owner", click("read:" + state["confirmation"]), state)
    assert "intitulés du résumé est invalide" in str(replies) and op is None
    s.bia.synthesize.assert_not_called()
    s.wa.upload_audio.assert_not_called()


async def test_tts_failure_keeps_draft_available_without_saving(setup):
    s = setup
    state = await summary(s, "french")
    s.ai.synthesize_french.side_effect = httpx.ConnectError("private provider detail")
    state, replies, op = await s.bot.handle("owner", click("read:" + state["confirmation"]), state)
    assert state["confirmation"] and op is None
    assert "private provider detail" not in str(replies)
    s.wa.upload_audio.assert_not_called()


def test_split_long_input_without_truncation_and_exact_spoken_fields(setup):
    value = " ".join(["mot"] * 600)
    result = pieces(value)
    assert len(result) > 1 and all(len(part) <= 480 for part in result)
    assert " ".join(result) == value
    body = spoken_summary(setup.draft)
    for item in ["Awa", "Riz", "30000", "10000", "20000", "20/12/2026"]:
        assert item in body
    verify_spoken_amounts("30 000, 10 000 et 20 000", setup.draft)
    with pytest.raises(MediaError):
        verify_spoken_amounts("30000, 1000 et 20000", setup.draft)


async def test_french_tts_decodes_pcm_and_converts_for_whatsapp(monkeypatch):
    from bot_wumtaaba import media

    convert = AsyncMock(return_value=(b"mp3", "fr.mp3", "audio/mpeg"))
    monkeypatch.setattr(media, "encode_whatsapp_audio", convert)

    def handler(request):
        data = json.loads(request.content)
        assert data["generationConfig"]["responseModalities"] == ["AUDIO"]
        assert "30000" in data["contents"][0]["parts"][0]["text"]
        return httpx.Response(
            200,
            json={
                "candidates": [
                    {
                        "finishReason": "STOP",
                        "content": {
                            "parts": [
                                {
                                    "inlineData": {
                                        "data": base64.b64encode(b"\0" * 4800).decode(),
                                        "mimeType": "audio/L16;codec=pcm;rate=24000",
                                    }
                                }
                            ]
                        },
                    }
                ]
            },
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = Intelligence(Settings(gemini_api_key="test"), http)
        result = await client.synthesize_french("Prix : 30000 francs CFA.")
    assert result[2] == "audio/mpeg" and convert.call_args.args[0].startswith(b"RIFF")


async def test_french_tts_rejects_text_instead_of_audio():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda r: httpx.Response(
                200,
                json={
                    "candidates": [
                        {"finishReason": "STOP", "content": {"parts": [{"text": "no audio"}]}}
                    ]
                },
            )
        )
    ) as http:
        client = Intelligence(Settings(gemini_api_key="test"), http)
        with pytest.raises(MediaError):
            await client.synthesize_french("Résumé à vérifier.")


async def test_moore_readback_protects_numbers_and_date_from_translation(setup):
    from bot_wumtaaba.agreement_voice import moore_spoken_summary

    s = setup

    # Simulate a translator that is unable to handle dates or monetary amounts.
    async def translate(value, source, target):
        assert "30000" not in value and "10000" not in value
        assert "20/12/2026" not in value
        return "libellé mooré"

    s.bia.translate.side_effect = translate
    result = await moore_spoken_summary(s.bia, s.draft)
    for exact in ["Awa", "30000", "10000", "20000", "20/12/2026"]:
        assert exact in result
