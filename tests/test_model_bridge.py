import asyncio
import json
import time

import httpx
import pytest
from fastapi import FastAPI, HTTPException

from bot_wumtaaba.clients import MediaError
from bot_wumtaaba.model_bridge import ModelBridge, install_bridge
from bot_wumtaaba.store import Store

SECRET = "test-mac-connector-key-32-characters-minimum"


@pytest.fixture
def bridge():
    store = Store(":memory:")
    bridge = ModelBridge(store, SECRET, timeout=1)
    yield bridge
    store.close()


@pytest.fixture
def app(bridge):
    app = FastAPI()
    install_bridge(app, lambda: bridge)
    return app


@pytest.fixture
def client(app):
    return httpx.AsyncClient(transport=httpx.ASGITransport(app=app), base_url="http://test")


async def test_auth_poll_updates_readiness_only_when_authorized(bridge, client):
    async with client:
        for headers in ({}, {"X-Mac-Key": "wrong"}):
            assert (await client.post("/internal/mac/jobs", headers=headers)).status_code == 401
        assert bridge.status()["mac_online"] is False
        response = await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET})
        assert response.json() == {"job": None}
        assert response.headers["cache-control"] == "no-store"
        assert bridge.status()["mac_online"] is True
        assert bridge.status()["last_seen"] is not None


def test_offline_selection_fails_closed(bridge):
    with pytest.raises(HTTPException) as error:
        bridge.select("mac")
    assert error.value.status_code == 409
    assert bridge.provider == "cloud"
    bridge.poll()
    assert bridge.select("mac")["provider"] == "mac"
    bridge._last_seen_monotonic = time.monotonic() - 21
    assert not bridge.status()["mac_online"]
    assert bridge.select("cloud")["provider"] == "cloud"


def test_provider_persists_and_switch_audited(tmp_path):
    path = tmp_path / "db.sqlite"
    store = Store(path)
    bridge = ModelBridge(store, SECRET)
    bridge.poll()
    bridge.select("mac")
    row = store.db.execute("SELECT details FROM audit_events WHERE kind='model_provider_changed'").fetchone()
    assert json.loads(row["details"])["previous"] == "cloud"
    assert SECRET not in "\n".join(store.db.iterdump())
    store.close()
    store = Store(path)
    try:
        bridge = ModelBridge(store, SECRET)
        assert bridge.status()["provider"] == "mac"
        assert bridge.status()["mac_online"] is False
    finally:
        store.close()


@pytest.mark.parametrize("secret", ["", "too-short"])
def test_unconfigured_bridge_cannot_become_online(secret):
    store = Store(":memory:")
    try:
        bridge = ModelBridge(store, secret)
        bridge.poll()
        assert not bridge.status()["mac_online"]
        with pytest.raises(HTTPException) as error:
            bridge.authenticate(secret)
        assert error.value.status_code == 503
    finally:
        store.close()


async def test_roundtrip_claim_once_and_complete(bridge, client):
    bridge.poll()
    task = asyncio.create_task(bridge.submit("/translate", {"text": "Bonjour"}))
    await asyncio.sleep(0)
    async with client:
        job = (await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET})).json()["job"]
        assert job["route"] == "/translate" and job["payload"] == {"text": "Bonjour"}
        assert job["expires_at"] > time.time()
        assert (await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET})).json()["job"] is None
        response = await client.post("/internal/mac/jobs/" + job["id"], headers={"X-Mac-Key": SECRET},
                                     json={"lease": job["lease"], "result": {"output": "Laafi"}})
        assert response.status_code == 200
        assert await task == {"output": "Laafi"}
        assert not bridge._jobs


async def test_invalid_lease_or_result_does_not_consume_job(bridge, client):
    bridge.poll()
    task = asyncio.create_task(bridge.submit("/tts", {"text": "Laafi"}))
    await asyncio.sleep(0)
    job = bridge.poll()
    async with client:
        url = "/internal/mac/jobs/" + job["id"]
        assert (await client.post(url, json={"lease": job["lease"], "result": {}})).status_code == 401
        assert (await client.post(url, headers={"X-Mac-Key": SECRET},
                                 json={"lease": "wrong", "result": {}})).status_code == 409
        assert (await client.post(url, headers={"X-Mac-Key": SECRET},
                                 json={"lease": job["lease"], "result": {}, "error": True})).status_code == 400
        assert not task.done()
        bridge.complete(job["id"], {"lease": job["lease"], "result": {"wav": "abc"}})
        assert await task == {"wav": "abc"}


async def test_error_result_is_generic_and_never_falls_back(bridge):
    bridge.poll()
    task = asyncio.create_task(bridge.submit("/chat", {"messages": []}))
    await asyncio.sleep(0)
    job = bridge.poll()
    bridge.complete(job["id"], {"lease": job["lease"], "error": True})
    with pytest.raises(MediaError, match="Mac"):
        await task
    assert not bridge._jobs


async def test_timeout_removes_job_and_rejects_late_completion(bridge):
    bridge.timeout = 0.01
    bridge.poll()
    task = asyncio.create_task(bridge.submit("/transcribe", {"audio_base64": "YQ=="}))
    await asyncio.sleep(0)
    job = bridge.poll()
    with pytest.raises(MediaError, match="temps"):
        await task
    assert not bridge._jobs
    with pytest.raises(HTTPException) as error:
        bridge.complete(job["id"], {"lease": job["lease"], "result": {}})
    assert error.value.status_code == 404


async def test_offline_submit_fails_without_queue(bridge):
    with pytest.raises(MediaError, match="hors ligne"):
        await bridge.submit("/translate", {"text": "Hello"})
    assert not bridge._jobs


async def test_unsupported_route_rejected(bridge):
    bridge.poll()
    with pytest.raises(MediaError, match="non prise en charge"):
        await bridge.submit("https://evil.test/", {})
    assert not bridge._jobs


async def test_queue_capacity_and_cancellation_cleanup(bridge):
    bridge.poll()
    tasks = [asyncio.create_task(bridge.submit("/translate", {"text": str(i)})) for i in range(8)]
    await asyncio.sleep(0)
    try:
        with pytest.raises(MediaError, match="pleine"):
            await bridge.submit("/translate", {})
        assert len(bridge._jobs) == 8
    finally:
        for task in tasks:
            task.cancel()
        await asyncio.gather(*tasks, return_exceptions=True)
    assert not bridge._jobs


async def test_response_limit_and_invalid_json(client, monkeypatch):
    monkeypatch.setattr("bot_wumtaaba.model_bridge.MAX_RESULT_BYTES", 100)
    async with client:
        assert (await client.post("/internal/mac/jobs/no-job", headers={"X-Mac-Key": SECRET},
                                 content=b"x" * 101)).status_code == 413
        assert (await client.post("/internal/mac/jobs/no-job", headers={"X-Mac-Key": SECRET},
                                 content=b"not-json")).status_code == 400


async def test_heartbeat_does_not_claim_waiting_job(bridge, client):
    bridge.poll()
    task = asyncio.create_task(bridge.submit("/translate", {"text": "Bonjour"}))
    await asyncio.sleep(0)
    bridge._last_seen_monotonic = time.monotonic() - 21
    async with client:
        response = await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET},
                                     json={"heartbeat_only": True})
        assert response.status_code == 200 and response.json() == {"job": None}
        assert bridge.mac_online
        job = (await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET})).json()["job"]
        assert job and job["payload"]["text"] == "Bonjour"
        bridge.complete(job["id"], {"lease": job["lease"], "result": {"output": "x"}})
        await task


@pytest.mark.parametrize("body", [{"heartbeat_only": "true"}, [], {"other": True}])
async def test_invalid_heartbeat_body_rejected(bridge, client, body):
    async with client:
        response = await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET}, json=body)
        assert response.status_code == 400
        assert not bridge.mac_online


@pytest.mark.parametrize("feature,route,result,expected", [
    ("translate", "/translate", {"output": "Laafi"}, "Laafi"),
    ("transcribe", "/transcribe", {"text": "Laafi"}, "Laafi"),
    ("synthesize", "/tts", {"wav": "UklGRgAAAABXQVZF"}, (b"mp3", "voice.mp3", "audio/mpeg")),
    ("chat", "/chat", {"choices": [{"message": {"content": "Laafi"}}]}, "Laafi"),
])
async def test_burkimbia_real_asgi_bridge_roundtrips(bridge, client, monkeypatch, feature, route, result, expected):
    from unittest.mock import AsyncMock

    from bot_wumtaaba.burkimbia import BurkimbIA
    from bot_wumtaaba.config import Settings

    def no_cloud(request):
        pytest.fail("A Mac request must never call the cloud")

    encoder = AsyncMock(return_value=(b"mp3", "voice.mp3", "audio/mpeg"))
    monkeypatch.setattr("bot_wumtaaba.burkimbia.encode_whatsapp_audio", encoder)
    async with client, httpx.AsyncClient(transport=httpx.MockTransport(no_cloud)) as provider_http:
        await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET}, json={"heartbeat_only": True})
        bridge.select("mac")
        provider = BurkimbIA(Settings(_env_file=None), provider_http, bridge)
        if feature == "transcribe":
            call = provider.transcribe(b"fakevoice", "voice.ogg", "audio/ogg")
        elif feature == "chat":
            call = provider.chat("Yaa laafi?", [])
        else:
            call = getattr(provider, feature)("Laafi bala")
        task = asyncio.create_task(call)
        await asyncio.sleep(0)
        job = (await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET}, json={})).json()["job"]
        assert job["route"] == route
        if feature == "transcribe":
            assert job["payload"]["language"] == "moore"
        if feature == "chat":
            assert job["payload"]["model"] == "tengsoaba-4b"
        # Payloads remain ephemeral: SQLite only stores the provider switch.
        assert "fakevoice" not in "\n".join(bridge.store.db.iterdump())
        response = await client.post("/internal/mac/jobs/" + job["id"],
                                     headers={"X-Mac-Key": SECRET},
                                     json={"lease": job["lease"], "result": result})
        assert response.status_code == 200
        assert await task == expected
        assert not bridge._jobs


async def test_burkimbia_offline_selected_mac_never_falls_back(bridge):
    from bot_wumtaaba.burkimbia import BurkimbIA
    from bot_wumtaaba.config import Settings

    def no_cloud(request):
        pytest.fail("Offline Mac must not silently use cloud")

    bridge.poll()
    bridge.select("mac")
    bridge._last_seen_monotonic = time.monotonic() - 21
    async with httpx.AsyncClient(transport=httpx.MockTransport(no_cloud)) as http:
        provider = BurkimbIA(Settings(_env_file=None, burkimbia_api_key="cloud-key"), http, bridge)
        with pytest.raises(MediaError, match="hors ligne"):
            await provider.translate("Bonjour")
        assert bridge.provider == "mac"


async def test_provider_switch_does_not_retarget_inflight_job(bridge, client):
    from bot_wumtaaba.burkimbia import BurkimbIA
    from bot_wumtaaba.config import Settings

    cloud_calls = []

    def cloud(request):
        cloud_calls.append(request)
        return httpx.Response(200, json={"output": "cloud-result"})

    bridge.poll()
    bridge.select("mac")
    async with client, httpx.AsyncClient(transport=httpx.MockTransport(cloud)) as http:
        provider = BurkimbIA(Settings(_env_file=None, burkimbia_api_key="cloud-key"), http, bridge)
        pending = asyncio.create_task(provider.translate("Bonjour"))
        await asyncio.sleep(0)
        job = (await client.post("/internal/mac/jobs", headers={"X-Mac-Key": SECRET})).json()["job"]
        bridge.select("cloud")
        await client.post("/internal/mac/jobs/" + job["id"], headers={"X-Mac-Key": SECRET},
                          json={"lease": job["lease"], "result": {"output": "mac-result"}})
        assert await pending == "mac-result"
        assert not cloud_calls
        assert await provider.translate("Bonjour") == "cloud-result"
        assert len(cloud_calls) == 1
