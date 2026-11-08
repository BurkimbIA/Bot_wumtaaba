"""HTTP boundaries for isolated backoffice model experiments; never live inference."""

import base64
from types import SimpleNamespace
from unittest.mock import AsyncMock

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient
from pydantic import SecretStr

from bot_wumtaaba import check_inference
from bot_wumtaaba.clients import MediaError
from bot_wumtaaba.config import Settings
from bot_wumtaaba.domain import Draft

HEADERS = {"Origin": "https://testserver"}
AUDIO = base64.b64encode(b"saved audio bytes").decode()


def setup(monkeypatch):
    settings = Settings(
        _env_file=None,
        burkimbia_api_key=SecretStr(""),
        runpod_api_key=SecretStr("runpod-test-secret"),
        runpod_transcription_endpoint_id="h1powcx3dv6iy6",
    )
    app, router = FastAPI(), APIRouter(prefix="/admin")
    bridge = SimpleNamespace(
        mac_online=True, provider="cloud", submit=AsyncMock(return_value={"text": "Laafi"})
    )
    app.state.model_bridge = bridge

    def authorized(request):
        if request.cookies.get("admin") != "test-session":
            raise HTTPException(401, "Connexion requise")

    def same_origin(request):
        if request.headers.get("origin") != "https://testserver":
            raise HTTPException(403, "Origine refusée")

    check_inference.install_check_inference(router, settings, authorized, same_origin)
    app.include_router(router)
    return app, bridge


@pytest.fixture
def client(monkeypatch):
    app, bridge = setup(monkeypatch)
    with TestClient(app, base_url="https://testserver") as browser:
        browser.cookies.set("admin", "test-session")
        yield browser, bridge


def test_auth_origin_catalog_and_status(client):
    browser, bridge = client
    result = browser.get("/admin/api/check/models")
    assert result.status_code == 200
    assert result.json()["availability_verified"] is False
    assert any(item["id"] == "burkimbia/wav2vec-mos" for item in result.json()["transcription"])
    assert result.json()["cloud_transcription"][0]["id"] == "bia-transcription-v1"
    assert result.json()["cloud_transcription"][0]["available"] is True
    assert result.json()["cloud_translation"] == []
    assert "NLLB absent" in result.json()["cloud_note"]
    assert browser.get("/admin/api/check/status").json() == {
        "mac": {"status": "ok"},
        "cloud_configured": True,
    }
    assert (
        browser.post("/admin/api/check/run/mac/transcribe", json={"audio_base64": AUDIO}).status_code == 403
    )
    browser.cookies.clear()
    for route in ("models", "status"):
        assert browser.get("/admin/api/check/" + route).status_code == 401
    for route in ("run/mac/transcribe", "extract"):
        assert browser.post("/admin/api/check/" + route, json={}, headers=HEADERS).status_code == 401
    bridge.submit.assert_not_awaited()


def test_mac_selection_forwards_checkpoint_without_changing_live_provider(client):
    browser, bridge = client
    selected = "burkimbia/wav2vec-mos"
    response = browser.post(
        "/admin/api/check/run/mac/transcribe",
        json={
            "audio_base64": AUDIO,
            "model_id": selected,
        },
        headers=HEADERS,
    )
    assert response.status_code == 200 and response.json()["ok"] is True
    assert response.json()["model"] == selected
    route, payload = bridge.submit.call_args.args
    assert route == "/transcribe" and payload["model_id"] == selected
    assert payload["audio_base64"] == AUDIO and payload["language"] == "moore"
    assert bridge.provider == "cloud"
    bridge.submit.return_value = {"output": "Bonjour"}
    response = browser.post(
        "/admin/api/check/run/mac/translate",
        json={
            "text": "Laafi",
            "source": "moore",
            "model_id": "burkimbia/yaaba-4B",
        },
        headers=HEADERS,
    )
    assert response.json()["text"] == "Bonjour"
    assert bridge.submit.call_args.args[1]["src_lang"] == "moore"
    assert bridge.submit.call_args.args[1]["tgt_lang"] == "french"


def test_cloud_transcription_calls_runpod_directly_with_nested_whisper_output(client, monkeypatch):
    from bot_wumtaaba.burkimbia import BurkimbIA

    browser, bridge = client
    mock = AsyncMock(
        return_value={"output": {"text": "Laafi", "success": True}, "metadata": {"model": "Whisper"}}
    )
    proxy = AsyncMock(side_effect=AssertionError("Platform proxy must not be used"))
    monkeypatch.setattr(check_inference.RunPodClient, "infer", mock)
    monkeypatch.setattr(BurkimbIA, "_request", proxy)
    response = browser.post(
        "/admin/api/check/run/cloud/transcribe",
        json={
            "audio_base64": AUDIO,
            "model": "bia-transcription-v1",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200
    assert response.json()["ok"] is True and response.json()["text"] == "Laafi"
    assert mock.call_args.args == (
        "h1powcx3dv6iy6",
        {
            "audio_base64": AUDIO,
            "language": "mos",
            "return_timestamps": False,
        },
    )
    assert "secret" not in response.text
    proxy.assert_not_awaited()
    bridge.submit.assert_not_awaited()
    assert bridge.provider == "cloud"


def test_cloud_translation_is_explicitly_unavailable_without_calling_a_model(client, monkeypatch):
    browser, bridge = client
    mock = AsyncMock()
    monkeypatch.setattr(check_inference.RunPodClient, "infer", mock)
    response = browser.post(
        "/admin/api/check/run/cloud/translate",
        json={
            "text": "Laafi",
            "source": "moore",
            "model": "bia-translation-v2",
        },
        headers=HEADERS,
    )
    assert response.status_code == 503 and "aucun endpoint" in response.json()["detail"]
    mock.assert_not_awaited()
    bridge.submit.assert_not_awaited()


@pytest.mark.parametrize(
    "output",
    [
        {"output": {"success": False, "error": "private internal provider detail"}},
        {"output": {"text": "", "success": True}},
        {"output": {"text": "wrong", "error": "private internal provider detail"}},
        {"output": {"metadata": {"model": "Whisper"}}},
    ],
)
def test_cloud_nested_model_failures_do_not_look_like_success(client, monkeypatch, output):
    browser, _ = client
    monkeypatch.setattr(check_inference.RunPodClient, "infer", AsyncMock(return_value=output))
    response = browser.post(
        "/admin/api/check/run/cloud/transcribe",
        json={
            "audio_base64": AUDIO,
            "model": "bia-transcription-v1",
        },
        headers=HEADERS,
    )
    assert response.status_code == 200 and response.json()["ok"] is False
    assert "private internal" not in response.text


@pytest.mark.parametrize(
    ("route", "body"),
    [
        ("mac/transcribe", {"audio_base64": "invalid"}),
        ("mac/transcribe", {"audio_base64": ""}),
        ("mac/transcribe", {"audio_base64": AUDIO, "model_id": "/tmp/evil"}),
        ("mac/translate", {"text": "Laafi", "model_id": "burkimbia/wav2vec-mos"}),
        ("cloud/transcribe", {"audio_base64": AUDIO, "model": "bia-translation-v2"}),
        ("cloud/transcribe", {"audio_base64": AUDIO, "model": []}),
        ("cloud/transcribe", {"audio_base64": AUDIO, "model_id": "burkimbia/wav2vec-mos"}),
        ("mac/translate", {"text": "x" * 513}),
        ("mac/translate", {"text": "Laafi", "source": []}),
    ],
)
def test_invalid_inputs_never_reach_inference(client, route, body):
    browser, bridge = client
    assert browser.post("/admin/api/check/run/" + route, json=body, headers=HEADERS).status_code == 422
    bridge.submit.assert_not_awaited()


def test_model_failures_are_usable_errors_and_result_shape_is_checked(client):
    browser, bridge = client
    bridge.submit.side_effect = MediaError("Le Mac est hors ligne.")
    response = browser.post(
        "/admin/api/check/run/mac/transcribe", json={"audio_base64": AUDIO}, headers=HEADERS
    )
    assert response.json()["ok"] is False and "hors ligne" in response.json()["error"]
    bridge.submit.side_effect = None
    bridge.submit.return_value = {"output": "wrong service"}
    response = browser.post(
        "/admin/api/check/run/mac/transcribe", json={"audio_base64": AUDIO}, headers=HEADERS
    )
    assert response.json()["ok"] is False


def test_extraction_uses_shared_gemini_without_committing_an_agreement(client, monkeypatch):
    browser, bridge = client
    mock = AsyncMock(return_value=Draft(client="Awa", total=10000))
    monkeypatch.setattr(check_inference.Intelligence, "extract", mock)
    response = browser.post(
        "/admin/api/check/extract", json={"text": "Awa achète pour 10000"}, headers=HEADERS
    )
    assert response.json()["draft"]["client"] == "Awa"
    assert response.json()["model"] == "gemini-3.8-flash"
    mock.assert_awaited_once_with("Awa achète pour 10000", {})
    assert (
        browser.post("/admin/api/check/extract", json={"text": "x" * 8001}, headers=HEADERS).status_code
        == 422
    )
    bridge.submit.assert_not_awaited()


def test_real_admin_login_protects_experimental_routes(database, monkeypatch):
    from bot_wumtaaba.main import create_app

    settings = Settings(
        _env_file=None,
        whatsapp_token="test",
        meta_app_secret="test",
        webhook_verify_token="x" * 32,
        gemini_api_key="test",
        whatsapp_phone_number_id="123",
        allowed_senders="22670000000",
        **database,
        aws_access_key_id="test-key",
        aws_secret_access_key="test-secret",
        admin_password="test-password-" + "x" * 32,
    )
    mock = AsyncMock(return_value=Draft(product="riz"))
    monkeypatch.setattr(check_inference.Intelligence, "extract", mock)
    with TestClient(create_app(settings, run_worker=False), base_url="https://testserver") as browser:
        assert browser.get("/admin/api/check/models").status_code == 401
        assert (
            browser.post("/admin/api/check/extract", json={"text": "riz"}, headers=HEADERS).status_code == 401
        )
        assert (
            browser.post(
                "/admin/login",
                json={
                    "password": settings.admin_password.get_secret_value(),
                },
                headers=HEADERS,
            ).status_code
            == 200
        )
        assert browser.get("/admin/api/check/models").status_code == 200
        response = browser.post("/admin/api/check/extract", json={"text": "riz"}, headers=HEADERS)
        assert response.status_code == 200 and response.json()["draft"]["product"] == "riz"
        assert response.headers["cache-control"] == "no-store"
        assert (
            browser.post(
                "/admin/api/check/extract",
                json={"text": "riz"},
                headers={
                    "Origin": "https://evil.example",
                },
            ).status_code
            == 403
        )
        browser.post("/admin/logout", headers=HEADERS)
        assert browser.get("/admin/api/check/models").status_code == 401


def test_platform_key_does_not_advertise_direct_runpod_as_configured():
    settings = Settings(
        _env_file=None,
        burkimbia_api_key="platform-only-secret",
        runpod_api_key="",
        runpod_transcription_endpoint_id="h1powcx3dv6iy6",
    )
    app, router = FastAPI(), APIRouter(prefix="/admin")
    app.state.model_bridge = SimpleNamespace(mac_online=False)
    check_inference.install_check_inference(router, settings, lambda request: None, lambda request: None)
    app.include_router(router)
    with TestClient(app) as browser:
        assert browser.get("/admin/api/check/status").json()["cloud_configured"] is False
        catalog = browser.get("/admin/api/check/models").json()
        assert catalog["cloud_transcription"][0]["available"] is False
        assert catalog["cloud_translation"] == []
