import json

import pytest
from fastapi import APIRouter, FastAPI, HTTPException
from fastapi.testclient import TestClient

from bot_wumtaaba.admin_annotations import install_admin_annotations
from bot_wumtaaba.config import Settings

AUTH = {"X-Admin": "yes"}
PATH = "wuumtaaba/annotations/" + "a" * 64 + ".audio"


@pytest.fixture
def annotations(store, media):
    app, router = FastAPI(), APIRouter(prefix="/admin")
    app.state.store, app.state.annotation_media = store, media

    def authorized(request):
        if request.headers.get("x-admin") != "yes":
            raise HTTPException(401)

    install_admin_annotations(router, Settings(_env_file=None), authorized)
    app.include_router(router)
    with TestClient(app) as client:
        yield client, store, media


def seed(store, identifier="002", sample=None):
    if sample is None:
        sample = json.dumps(
            {
                "id": "clip",
                "dataset": "example/data",
                "private_key": "do-not-expose",
                "proposals": {"A": {"text": "Laafi", "model_id": "example/model", "secret": "do-not-expose"}},
            }
        )
    store.db.execute(
        "INSERT INTO annotation_tasks(task_id,sample,audio_path,audio_sha256,audio_mime,"
        "audio_size_bytes,created_at) VALUES(%s,%s,%s,%s,%s,%s,%s)",
        (identifier, sample, PATH, "a" * 64, "audio/flac", 20, 1),
    )


def test_auth_projection_detail_and_missing_task(annotations):
    client, store, _ = annotations
    seed(store)
    for path in ("/admin/api/annotations", "/admin/api/annotations/002", "/admin/api/annotations/002/audio"):
        assert client.get(path).status_code == 401
    listing = client.get("/admin/api/annotations", headers=AUTH).json()
    assert listing["total"] == 1
    assert listing["items"][0]["task_id"] == "002"
    assert listing["items"][0]["assignment_count"] == 0
    result = client.get("/admin/api/annotations/002", headers=AUTH)
    assert result.status_code == 200
    assert result.json()["task"]["sample"]["proposals"]["A"]["text"] == "Laafi"
    assert result.json()["task"]["audio_url"] == "/admin/api/annotations/002/audio"
    for private in ("do-not-expose", "storage_path", "audio_path", "audio_bucket", "audio_sha256"):
        assert private not in result.text
    assert client.get("/admin/api/annotations/missing", headers=AUTH).status_code == 404
    assert client.post("/admin/api/annotations/002", headers=AUTH, json={}).status_code == 405


def test_search_is_literal_and_pages_are_bounded(annotations):
    client, store, _ = annotations
    for i in range(27):
        seed(store, str(i))
    seed(store, "task_percent%")
    data = client.get("/admin/api/annotations?page=999", headers=AUTH).json()
    assert data["page"] == 2 and data["pages"] == 2 and data["total"] == 28
    assert len(data["items"]) == 3
    data = client.get("/admin/api/annotations", params={"q": "%"}, headers=AUTH).json()
    assert data["total"] == 1 and data["items"][0]["task_id"] == "task_percent%"


def test_corrupt_sample_and_rating_metadata_are_safe(annotations):
    client, store, _ = annotations
    seed(store, sample='["not an object"]')
    store.db.execute(
        "INSERT INTO annotation_assignments VALUES(%s,%s,%s,%s,%s)", ("22600000000", "002", 1, 1, "completed")
    )
    store.db.execute(
        "INSERT INTO annotation_events VALUES(%s,%s,%s,%s,%s)", ("event", "22600000000", "002", "{}", 1)
    )
    store.db.execute(
        "INSERT INTO annotation_responses VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
        (
            "22600000000",
            "002",
            "A",
            '{"A":true,"B":9,"secret":"hidden"}',
            None,
            "{}",
            "completed",
            "event",
            1,
            2,
        ),
    )
    result = client.get("/admin/api/annotations/002", headers=AUTH).json()
    assert result["task"]["sample"] == {"proposals": {}}
    assert result["responses"][0]["ratings"] == {}
    assert result["assignments"][0]["status"] == "completed"


def test_audio_is_private_range_capable_and_rejects_corrupt_paths(annotations):
    client, store, media = annotations
    seed(store)
    data = b"fLaC" + bytes(96)
    media.objects[PATH] = (data, "audio/flac")
    result = client.get("/admin/api/annotations/002/audio", headers={**AUTH, "Range": "bytes=0-15"})
    assert result.status_code == 206 and result.content == data[:16]
    assert result.headers["content-type"] == "audio/flac"
    store.db.execute("UPDATE annotation_tasks SET audio_path=%s WHERE task_id='002'", ("../private",))
    assert client.get("/admin/api/annotations/002/audio", headers=AUTH).status_code == 404
