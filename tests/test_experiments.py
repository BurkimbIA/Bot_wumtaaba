import base64
from uuid import uuid4

import httpx
import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from bot_wumtaaba.experiments import MAX_AUDIO_BYTES, install_experiments

AUTH = {"X-Admin": "yes", "Origin": "https://testserver"}


class Bucket:
    def __init__(self):
        self.objects = {}
        self.fail_put = self.fail_delete = False

    async def put(self, path, data, mime):
        if self.fail_put:
            raise RuntimeError("do-not-expose")
        self.objects[path] = (data, mime)

    async def get(self, path):
        return self.objects.get(path, (None,))[0]

    async def delete(self, path):
        assert path.startswith("experiments/")
        if self.fail_delete:
            raise httpx.HTTPStatusError(
                "unavailable", request=httpx.Request("DELETE", "https://t3.storage.dev"),
                response=httpx.Response(503),
            )
        self.objects.pop(path, None)



@pytest.fixture
def experiments(store):
    app, router, bucket = FastAPI(), APIRouter(prefix="/admin"), Bucket()
    app.state.store, app.state.media = store, bucket

    def authorized(request):
        if request.headers.get("x-admin") != "yes":
            raise HTTPException(401)

    def same_origin(request):
        if request.headers.get("origin") not in {None, "https://testserver"}:
            raise HTTPException(403)

    install_experiments(router, authorized, same_origin)
    app.include_router(router)
    with TestClient(app, base_url="https://testserver") as client:
        yield client, bucket, store


def clip(**extra):
    return {
        "id": str(uuid4()),
        "name": "Essai mooré",
        "audio_base64": base64.b64encode(b"RIFF" + bytes(96)).decode(),
        "mime": "audio/wav",
        "createdAt": 1700000000000,
        "results": [{"task": "transcribe", "output": "laafi"}],
        **extra,
    }


def test_authentication_and_origin_before_storage(experiments):
    client, bucket, store = experiments
    value = str(uuid4())
    for method, path in [
        ("GET", "/admin/api/experiments"),
        ("POST", "/admin/api/experiments"),
        ("PUT", f"/admin/api/experiments/{value}"),
        ("DELETE", f"/admin/api/experiments/{value}"),
        ("GET", f"/admin/api/experiments/{value}/audio"),
    ]:
        assert client.request(method, path, json=clip()).status_code == 401
    for method, path in [
        ("POST", "/admin/api/experiments"),
        ("PUT", f"/admin/api/experiments/{value}"),
        ("DELETE", f"/admin/api/experiments/{value}"),
    ]:
        assert (
            client.request(
                method, path, json=clip(), headers={**AUTH, "Origin": "https://evil.test"}
            ).status_code
            == 403
        )
    assert not bucket.objects
    assert store.db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 0


def test_save_list_playback_range_update_and_delete_scoped(experiments):
    client, bucket, store = experiments
    first, second = clip(), clip(name="Autre test")
    response = client.post("/admin/api/experiments", json=first, headers=AUTH)
    assert response.status_code == 200
    item = response.json()
    assert item["createdAt"] == first["createdAt"]
    assert item["results"] == first["results"]
    assert "storage_path" not in response.text and "do-not-expose" not in response.text
    client.post("/admin/api/experiments", json=second, headers=AUTH)
    rows = client.get("/admin/api/experiments", headers=AUTH).json()["items"]
    assert len(rows) == 2
    response = client.get(item["audio_url"], headers={**AUTH, "Range": "bytes=0-3"})
    assert response.status_code == 206 and response.content == b"RIFF"
    assert response.headers["content-type"] == "audio/wav"
    new_results = [{"task": "transcribe", "output": "nouveau"}]
    response = client.put(
        "/admin/api/experiments/" + first["id"],
        json={"name": "Renommé", "results": new_results},
        headers=AUTH,
    )
    assert response.json()["name"] == "Renommé" and response.json()["results"] == new_results
    assert response.json()["createdAt"] == first["createdAt"]
    assert client.delete("/admin/api/experiments/" + first["id"], headers=AUTH).status_code == 200
    assert len(bucket.objects) == 1
    assert client.get(item["audio_url"], headers=AUTH).status_code == 404
    assert client.get("/admin/api/experiments", headers=AUTH).json()["items"][0]["id"] == second["id"]
    assert (
        store.db.execute("SELECT relrowsecurity FROM pg_class WHERE oid='experiments'::regclass").fetchone()[
            0
        ]
        is True
    )


@pytest.mark.parametrize(
    "change",
    [
        {"id": "../messages"},
        {"name": ""},
        {"name": "x" * 201},
        {"mime": "text/html"},
        {"mime": []},
        {"audio_base64": "invalid"},
        {"audio_base64": ""},
        {"results": {}},
        {"results": [{}] * 4},
        {"results": [{"output": "x" * (128 * 1024)}]},
        {"createdAt": True},
        {"createdAt": 10**999},
        {"storage_path": "private-user-audio"},
    ],
)
def test_invalid_input_has_no_side_effect(experiments, change):
    client, bucket, store = experiments
    response = client.post("/admin/api/experiments", json=clip(**change), headers=AUTH)
    assert response.status_code == 422
    assert not bucket.objects
    assert store.db.execute("SELECT count(*) FROM experiments").fetchone()[0] == 0


def test_upload_limits_and_json(experiments):
    client, bucket, _ = experiments
    assert (
        client.post(
            "/admin/api/experiments", content="{}", headers={**AUTH, "Content-Type": "text/plain"}
        ).status_code
        == 400
    )
    assert (
        client.post(
            "/admin/api/experiments", content="{", headers={**AUTH, "Content-Type": "application/json"}
        ).status_code
        == 400
    )
    body = clip(audio_base64=base64.b64encode(bytes(MAX_AUDIO_BYTES + 1)).decode())
    assert client.post("/admin/api/experiments", json=body, headers=AUTH).status_code == 422
    assert not bucket.objects


def test_failed_upload_and_failed_database_replacement_preserve_previous_audio(experiments, monkeypatch):
    client, bucket, store = experiments
    body = clip()
    client.post("/admin/api/experiments", json=body, headers=AUTH)
    previous = dict(bucket.objects)
    bucket.fail_put = True
    response = client.post("/admin/api/experiments", json=clip(), headers=AUTH)
    assert response.status_code == 503 and "do-not-expose" not in response.text
    assert bucket.objects == previous
    bucket.fail_put = False
    execute = store.db.execute

    def fail_insert(query, params=None):
        if query.startswith("INSERT INTO experiments"):
            raise RuntimeError("db connection secret")
        return execute(query, params)

    monkeypatch.setattr(store.db, "execute", fail_insert)
    response = client.post("/admin/api/experiments", json={**body, "name": "replacement"}, headers=AUTH)
    assert response.status_code == 503
    assert "db connection secret" not in response.text
    assert bucket.objects == previous
    assert execute("SELECT name FROM experiments WHERE id=%s", (body["id"],)).fetchone()[0] == body["name"]


def test_failed_storage_delete_keeps_row_and_database_failure_restores_audio(experiments, monkeypatch):
    client, bucket, store = experiments
    body = clip()
    client.post("/admin/api/experiments", json=body, headers=AUTH)
    previous = dict(bucket.objects)
    bucket.fail_delete = True
    path = "/admin/api/experiments/" + body["id"]
    assert client.delete(path, headers=AUTH).status_code == 503
    assert bucket.objects == previous and client.get(path + "/audio", headers=AUTH).status_code == 200
    bucket.fail_delete = False
    execute = store.db.execute

    def fail_delete(query, params=None):
        if query.startswith("DELETE FROM experiments"):
            raise RuntimeError("db secret")
        return execute(query, params)

    monkeypatch.setattr(store.db, "execute", fail_delete)
    response = client.delete(path, headers=AUTH)
    assert response.status_code == 503 and "db secret" not in response.text
    assert bucket.objects == previous and client.get(path + "/audio", headers=AUTH).status_code == 200


def test_replacement_cleans_old_object_and_preserves_created_at(experiments):
    client, bucket, _ = experiments
    body = clip()
    client.post("/admin/api/experiments", json=body, headers=AUTH)
    previous = set(bucket.objects)
    response = client.post(
        "/admin/api/experiments", json={**body, "name": "Remplacé", "createdAt": 1750000000000}, headers=AUTH
    )
    assert response.status_code == 200
    assert response.json()["createdAt"] == body["createdAt"]
    assert len(bucket.objects) == 1 and not set(bucket.objects) & previous
