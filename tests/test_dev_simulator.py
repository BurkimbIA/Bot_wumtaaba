"""HTTP scenarios use the shared bot; only remote model inference is replaced."""

import base64
import io
import wave

import pytest
from fastapi import FastAPI
from fastapi.testclient import TestClient

from bot_wumtaaba.dev_simulator import bot_modules, _install_engine as install
from bot_wumtaaba import dev_simulator as conversation_lab


COMPLETE = dict(client="Awa", product="Robe", total=20000, paid=5000, due_date="2099-01-10")


class Models:
    async def extract(self, text, current):
        from bot_wumtaaba.domain import Draft

        fixtures = {
            "vente complète": COMPLETE,
            "corriger avance": {**COMPLETE, "paid": 7000},
            "accord partiel": {"client": "Awa", "product": "Robe"},
            "vente passée": {**COMPLETE, "due_date": "2000-01-10"},
        }
        return Draft.model_validate(fixtures[text])

    async def transcribe(self, data, filename, mime):
        return "2000"

    async def synthesize_french(self, text):
        raise bot_modules().clients.MediaError("Lecture vocale de test indisponible.")


async def run_model(provider, task, payload):
    if task == "transcribe":
        # Different remote outputs make selection observable through the whole flow.
        text = "mooré partiel" if payload.get("model_id") == "asr-partial" else "mooré complet"
    elif task == "translate":
        text = "accord partiel" if payload["text"] == "mooré partiel" else "vente complète"
    else:
        return {"ok": False, "error": "Synthèse indisponible."}
    return {"ok": True, "text": text, "model": payload.get("model_id") or payload["model"]}


@pytest.fixture
def client():
    bot_modules()
    app = FastAPI()
    from fastapi import APIRouter

    router = APIRouter(prefix="/admin/api/simulator")
    install(
        router,
        lambda: {"GEMINI_API_KEY": "test", "GEMINI_MODEL": "gemini-test"},
        run_model,
        intelligence_factory=lambda settings, http: Models(),
    )
    app.include_router(router)
    with TestClient(app) as client:
        yield client


def start(client):
    response = client.post("/admin/api/simulator/session", json={})
    assert response.status_code == 200, response.text
    return response.json()


def send(client, session, **data):
    response = client.post("/admin/api/simulator/turn", json={"session_id": session["session_id"], **data})
    assert response.status_code == 200, response.text
    return response.json()


def button(response, prefix):
    for reply in response["replies"]:
        action = reply.get("interactive", {}).get("action", {})
        options = [item["reply"] for item in action.get("buttons", [])]
        options += [row for section in action.get("sections", []) for row in section["rows"]]
        for option in options:
            if option["id"].startswith(prefix):
                return option["id"]
    raise AssertionError(f"Missing action {prefix}: {response}")


def draft(client, session, language="french"):
    result = send(client, session, action=button(session, "new"))
    return send(client, result, action=button(result, "language:" + language + ":"))


def wav():
    output = io.BytesIO()
    with wave.open(output, "wb") as audio:
        audio.setparams((1, 2, 16000, 0, "NONE", "not compressed"))
        audio.writeframes(b"\x00\x00" * 1600)
    return base64.b64encode(output.getvalue()).decode()


def test_text_correction_confirmation_payment_and_history(client):
    session = start(client)
    entry = draft(client, session)
    first = send(client, entry, text="vente complète")
    corrected = send(client, first, text="corriger avance")
    stale = send(client, corrected, action=button(first, "confirm:"))
    assert stale["agreements"] == []
    assert stale["draft"]["paid"] == 7000
    saved = send(client, stale, action=button(corrected, "confirm:"))
    row = saved["agreements"][0]
    assert (row["paid"], row["balance"], row["client"]) == (7000, 13000, "Awa")
    assert send(client, saved, action=button(corrected, "confirm:"))["agreements"] == saved["agreements"]
    listing = send(client, saved, action="list:0")
    detail = send(client, listing, action=button(listing, "detail:"))
    payment = send(client, detail, action=button(detail, "payment:"))
    # French payment vocals keep deterministic integer parsing after ASR.
    preview = send(client, payment, audio_base64=wav())
    assert preview["agreements"][0]["paid"] == 7000
    paid = send(client, preview, action=button(preview, "payconfirm:"))
    assert paid["agreements"][0]["balance"] == 11000
    history = send(client, paid, action=button(paid, "detail:"))
    body = "\n".join(reply.get("text", {}).get("body", "") for reply in history["replies"])
    assert "7 000 F CFA" in body and "2 000 F CFA" in body
    calendar = send(client, history, text="calendrier")
    assert row["reference"] in str(calendar["replies"])


def test_moore_selected_models_partial_agreement_and_reset_isolation(client):
    session = start(client)
    other = start(client)
    entry = draft(client, session, "moore")
    preview = send(
        client,
        entry,
        audio_base64=wav(),
        provider="mac",
        transcription_model="asr-partial",
        translation_model="translation-test",
    )
    assert preview["draft"]["client"] == "Awa"
    assert preview["draft"]["total"] is None and preview["draft"]["paid"] is None
    assert [(stage["task"], stage["model"]) for stage in preview["stages"]] == [
        ("transcribe", "asr-partial"),
        ("translate", "translation-test"),
        ("extract", "gemini-test"),
    ]
    assert [stage["output"] for stage in preview["stages"][:2]] == ["mooré partiel", "accord partiel"]
    saved = send(client, preview, action=button(preview, "confirm:"))
    row = saved["agreements"][0]
    assert row["balance"] is None and row["due_date"] is None
    denied = send(client, saved, action="payment:" + row["id"])
    assert denied["state"] == {} and denied["agreements"][0]["paid"] is None
    assert send(client, other, action="list:0")["agreements"] == []
    reset = client.post("/admin/api/simulator/reset", json={"session_id": session["session_id"]}).json()
    assert reset["agreements"] == [] and reset["draft"] is None
    assert send(client, reset, action=button(preview, "confirm:"))["agreements"] == []
    assert button(reset, "new") == "new"


def test_payment_name_search_and_cancel(client):
    preview = send(client, draft(client, start(client)), text="vente complète")
    saved = send(client, preview, action=button(preview, "confirm:"))
    choice = send(client, saved, action="payment")
    found = send(client, choice, text="awa")
    amount = send(client, found, action=button(found, "payment:"))
    preview = send(client, amount, text="1000")
    cancelled = send(client, preview, action="paycancel")
    stale = send(client, cancelled, action=button(preview, "payconfirm:"))
    assert stale["agreements"][0]["paid"] == 5000


def test_calendar_keeps_past_and_settled_events_like_shared_store(client):
    preview = send(client, draft(client, start(client)), text="vente passée")
    saved = send(client, preview, action=button(preview, "confirm:"))
    row = saved["agreements"][0]
    calendar = send(client, saved, text="calendrier")
    assert "2000-01-10" in str(calendar["replies"])
    settled = send(client, calendar, text="réglé " + row["reference"])
    assert settled["agreements"][0]["balance"] == 0
    assert settled["agreements"][0]["status"] == "settled"
    history = send(client, settled, action="detail:" + row["id"])
    assert "Règlement déclaré : 15 000 F CFA" in str(history["replies"])
    calendar = send(client, settled, text="calendrier")
    assert "2000-01-10" in str(calendar["replies"]) and "settled" in str(calendar["replies"])


@pytest.mark.parametrize(
    "payload",
    [
        {"text": "a", "action": "new"},
        {"action": {}},
        {"audio_base64": "not-base64"},
        {"audio_base64": wav(), "mime": "application/pdf"},
        {"text": "a", "provider": "other"},
        {"text": "a", "provider": {}},
    ],
)
def test_invalid_turn_is_rejected_without_losing_session(client, payload):
    session = start(client)
    response = client.post("/admin/api/simulator/turn", json={"session_id": session["session_id"], **payload})
    assert response.status_code == 422
    assert send(client, session, action="list:0")["agreements"] == []


def test_unknown_session_and_audio_are_not_available(client):
    assert (
        client.post("/admin/api/simulator/turn", json={"session_id": "missing", "text": "menu"}).status_code
        == 410
    )
    session = start(client)
    assert client.get(f"/admin/api/simulator/audio/{session['session_id']}/missing").status_code == 404
    assert session["engine"]["simulation"] is True
    assert len(session["engine"]["revision"]) == 12


def test_readback_model_failure_preserves_draft_and_confirmation(client):
    preview = send(client, draft(client, start(client)), text="vente complète")
    failed = send(client, preview, action=button(preview, "read:"))
    assert failed["draft"] == preview["draft"] and failed["agreements"] == []
    assert failed["stages"][0]["task"] == "tts" and failed["stages"][0]["error"]
    saved = send(client, failed, action=button(preview, "confirm:"))
    assert saved["agreements"][0]["balance"] == 15000


def test_session_capacity_and_expiration_are_explicit(client, monkeypatch):
    monkeypatch.setattr(conversation_lab, "MAX_SESSIONS", 1)
    session = start(client)
    assert client.post("/admin/api/simulator/session", json={}).status_code == 429
    monkeypatch.setattr(conversation_lab, "SESSION_TTL", -1)
    response = client.post(
        "/admin/api/simulator/turn", json={"session_id": session["session_id"], "text": "menu"}
    )
    assert response.status_code == 410
    fresh = start(client)
    assert fresh["session_id"] != session["session_id"] and fresh["agreements"] == []


@pytest.mark.parametrize("environment", ["development", "production"])
def test_public_installer_is_authenticated_and_origin_guarded(environment):
    from fastapi import APIRouter, HTTPException
    from bot_wumtaaba.config import Settings
    from bot_wumtaaba.dev_simulator import install_simulator

    app = FastAPI()
    app.state.environment = environment
    router = APIRouter(prefix="/admin")

    def authorized(request):
        if request.headers.get("x-admin") != "yes":
            raise HTTPException(401)

    def origin(request):
        if request.headers.get("origin") != "http://testserver":
            raise HTTPException(403)

    install_simulator(router, Settings(_env_file=None), authorized, origin, lambda s, h: Models())
    app.include_router(router)
    with TestClient(app) as client:
        assert client.get("/admin/simulator").status_code == 401
        assert client.post("/admin/api/simulator/session", json={}).status_code == 401
        assert client.get("/admin/simulator/assets/simulator.js").status_code == 401
        assert client.get("/admin/api/simulator/audio/missing/missing").status_code == 401
        assert (
            client.post("/admin/api/simulator/session", json={}, headers={"x-admin": "yes"}).status_code
            == 403
        )
        auth = {"x-admin": "yes", "origin": "http://testserver"}
        result = client.post("/admin/api/simulator/session", json={}, headers=auth)
        assert result.status_code == 200 and result.json()["engine"]["simulation"]
        assert client.get("/admin/simulator", headers=auth).status_code == 200
        assert client.get("/admin/simulator/assets/simulator.js", headers=auth).status_code == 200
        assert client.get("/admin/simulator/assets/recording-worklet.js", headers=auth).status_code == 200
        assert client.get("/admin/simulator/assets/unknown.js", headers=auth).status_code == 404
        assert client.post("/admin/api/simulator/reset", json={"session_id": result.json()["session_id"]},
                           headers={"x-admin": "yes", "origin": "https://evil.example"}).status_code == 403
        assert client.get("/admin/api/simulator/audio/missing/missing", headers=auth).status_code == 410


@pytest.mark.parametrize("environment", ["development", "production"])
def test_native_runner_uses_selected_checkpoint_through_request_context(environment):
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    from fastapi import APIRouter
    from bot_wumtaaba.config import Settings
    from bot_wumtaaba.dev_simulator import install_simulator

    app = FastAPI()
    app.state.environment = environment
    app.state.model_bridge = SimpleNamespace(
        submit=AsyncMock(side_effect=[{"text": "mooré complet"}, {"output": "vente complète"}])
    )
    router = APIRouter(prefix="/admin")
    install_simulator(
        router, Settings(_env_file=None), lambda r: None, lambda r: None, lambda s, h: Models()
    )
    app.include_router(router)
    with TestClient(app) as client:
        session = start(client)
        entry = draft(client, session, "moore")
        result = send(
            client,
            entry,
            audio_base64=wav(),
            mime="audio/wav",
            provider="mac",
            transcription_model="burkimbia/BIA-WHISPER-LARGE-SACHI_V2",
            translation_model="burkimbia/BIA-NLLB-600M-10E-CD-CRCL",
        )
        assert result["draft"]["client"] == "Awa"
        assert len(app.state.model_bridge.submit.call_args_list) == 2
        assert (
            app.state.model_bridge.submit.call_args_list[0].args[1]["model_id"]
            == "burkimbia/BIA-WHISPER-LARGE-SACHI_V2"
        )
        assert (
            app.state.model_bridge.submit.call_args_list[1].args[1]["model_id"]
            == "burkimbia/BIA-NLLB-600M-10E-CD-CRCL"
        )


def test_production_simulator_confirms_agreement_without_real_store_writes(database, dump, monkeypatch):
    from unittest.mock import AsyncMock

    from bot_wumtaaba.config import Settings
    from bot_wumtaaba.main import create_app

    monkeypatch.setattr(bot_modules().clients, "Intelligence", lambda settings, http: Models())
    settings = Settings(
        _env_file=None, **database, whatsapp_token="test", meta_app_secret="test",
        webhook_verify_token="x" * 32, gemini_api_key="test", whatsapp_phone_number_id="123",
        public_access=True, aws_access_key_id="test", aws_secret_access_key="test",
        admin_password="test-simulator-" + "x" * 32,
    )
    with TestClient(create_app(settings, run_worker=False), base_url="https://testserver") as client:
        assert client.get("/admin/simulator").status_code == 401
        assert client.post("/admin/login", json={"password": settings.admin_password.get_secret_value()}).status_code == 200
        assert client.get("/admin/api/environment").json() == {"environment": "production"}
        assert client.get("/admin/simulator").status_code == 200
        store = client.app.state.store
        before = dump(store)
        provider = client.app.state.model_bridge.provider
        whatsapp_send = AsyncMock(side_effect=AssertionError("Simulator must not send through Meta"))
        monkeypatch.setattr(bot_modules().clients.WhatsApp, "send", whatsapp_send)
        preview = send(client, draft(client, start(client)), text="vente complète")
        saved = send(client, preview, action=button(preview, "confirm:"))
        assert saved["agreements"][0]["client"] == "Awa"
        assert saved["agreements"][0]["balance"] == 15000
        assert saved["engine"]["simulation"] is True
        assert send(client, start(client), action="list:0")["agreements"] == []
        assert dump(store) == before
        assert client.app.state.model_bridge.provider == provider
        whatsapp_send.assert_not_called()
