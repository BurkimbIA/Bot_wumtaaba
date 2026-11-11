"""Development isolation must fail before any production-style startup."""

from types import SimpleNamespace

import httpx
import pytest
from fastapi.testclient import TestClient

from bot_wumtaaba import dev
from bot_wumtaaba.config import Settings


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://x@project.supabase.co/wumtaaba_dev",
        "postgresql://x@127.0.0.1/postgres",
        "postgresql://x@localhost/wumtaaba_dev?host=remote.example",
        "postgresql:///wumtaaba_dev",
        "sqlite:///local.db",
    ],
)
def test_remote_or_wrong_database_rejected_before_store(url, monkeypatch):
    def forbidden(*args):
        pytest.fail("Store must not be constructed")

    monkeypatch.setattr(dev, "Store", forbidden)
    with pytest.raises(ValueError):
        dev.create_dev_app(Settings(_env_file=None, database_url=url, admin_password="x" * 30))


def test_local_app_serves_shared_admin_without_whatsapp_or_recovery(monkeypatch, tmp_path):
    calls = []

    class Store:
        def __init__(self, url, schema):
            calls.append((url, schema))

        def close(self):
            calls.append("closed")

        def create_admin_session(self, token_hash, credential_hash, now, expires_at):
            calls.append("admin session saved locally")

    monkeypatch.setattr(dev, "Store", Store)
    settings = Settings(
        _env_file=None,
        database_url="postgresql://user@127.0.0.1:55432/wumtaaba_dev",
        admin_password="private-dev-password-" + "x" * 24,
    )
    app = dev.create_dev_app(settings, media_root=tmp_path, mac_key="")
    with TestClient(app) as client:
        assert client.post("/webhook", json={}).status_code == 404
        assert client.post("/mobile/login", json={}).status_code == 404
        assert client.get("/healthz").json()["whatsapp_worker"] is False
        assert client.get("/admin").status_code == 200
        assert client.get("/admin/api/experiments").status_code == 401
        assert client.get("/admin", headers={"Host": "attacker.example"}).status_code == 403
        login = client.post("/admin/login", json={"password": settings.admin_password.get_secret_value()})
        assert login.status_code == 200
        assert "secure;" not in login.headers["set-cookie"].lower()
        assert app.state.environment == "development"
    assert calls[-1] == "closed"


@pytest.mark.asyncio
async def test_local_audio_crud_and_path_isolation(tmp_path):
    media = dev.LocalMedia(tmp_path)
    path = "experiments/" + "a" * 32 + ".audio"
    await media.put(path, b"audio", "audio/wav")
    assert await media.get(path) == b"audio"
    await media.verify_private(path)
    await media.delete(path)
    assert await media.get(path) is None
    for forbidden in ["../secret", "/tmp/secret", "wuumtaaba/annotations/" + "a" * 64 + ".audio"]:
        with pytest.raises(ValueError):
            await media.get(forbidden)
    annotations = dev.LocalMedia(tmp_path, annotations=True)
    key = "wuumtaaba/annotations/" + "b" * 64 + ".audio"
    await annotations.put(key, b"annotation", "audio/wav")
    assert await annotations.get(key) == b"annotation"


@pytest.mark.asyncio
async def test_mac_inference_uses_loopback_without_live_bridge():
    requests = []

    def handler(request):
        requests.append(request)
        return httpx.Response(200, json={"text": "Laafi"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        bridge = dev.LocalBridge(SimpleNamespace(), http, "dev-mac-key")
        assert await bridge.submit("/transcribe", {"audio_base64": "example"}) == {"text": "Laafi"}
    assert str(requests[0].url) == "http://127.0.0.1:8765/api/v1/transcribe"
    assert requests[0].headers["x-api-key"] == "dev-mac-key"
