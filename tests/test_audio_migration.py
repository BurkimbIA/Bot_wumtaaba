"""Verified migration preserves source data and database logical paths."""

import importlib.util
from pathlib import Path
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

spec = importlib.util.spec_from_file_location(
    "audio_migration", Path(__file__).resolve().parents[1] / "scripts/migrate_audio_to_tigris.py"
)
migration = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migration)


class FakeTigris:
    def __init__(self, objects=None, *, corrupt=False, public=False):
        self.objects = dict(objects or {})
        self.puts = []
        self.private_checks = []
        self.corrupt, self.public = corrupt, public

    async def get(self, key):
        return self.objects.get(key)

    async def put(self, key, data, mime):
        self.puts.append((key, data, mime))
        self.objects[key] = b"corrupt" if self.corrupt else data

    async def verify_private(self, key):
        self.private_checks.append(key)
        if self.public:
            raise ValueError("public")


def settings():
    return SimpleNamespace(
        supabase_url="https://example.supabase.co",
        supabase_api_key=SecretStr("source-secret"),
        media_bucket="wuumtaaba-media",
    )


def rows(name="a.audio", size=3):
    return [{"name": name, "metadata": {"size": size}}]


async def test_dry_run_does_not_read_audio_or_need_credentials():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: pytest.fail("network"))) as http:
        result = await migration.migrate_audio(None, rows(), http)
    assert result["mode"] == "read_only_plan"
    assert result["objects"] == 1 and result["source_metadata_bytes"] == 3
    assert result["verified_objects"] == 0
    assert not result["source_deletes"] and not result["database_writes"]


async def test_apply_copies_agreement_and_experiment_keys_then_is_idempotent():
    requests = []

    def source(request):
        requests.append(request)
        assert request.method == "GET"
        assert request.headers["authorization"] == "Bearer source-secret"
        return httpx.Response(200, content=b"ogg", headers={"content-type": "audio/ogg"})

    store = FakeTigris()
    sources = rows("a" * 64 + ".audio") + rows("experiments/" + "b" * 32 + ".audio")
    async with httpx.AsyncClient(transport=httpx.MockTransport(source)) as http:
        first = await migration.migrate_audio(settings(), sources, http, store, apply=True)
        second = await migration.migrate_audio(settings(), sources, http, store, apply=True)
    assert first["verified_objects"] == second["verified_objects"] == 2
    assert first["verified_bytes"] == 6
    assert len(store.puts) == 2 and len(store.private_checks) == 4
    assert {key for key, _, _ in store.puts} == {"wuumtaaba/media/" + row["name"] for row in sources}
    assert all(request.url.host == "example.supabase.co" for request in requests)


@pytest.mark.parametrize("options,match", [({"corrupt": True}, "round-trip"), ({"public": True}, "public")])
async def test_failed_hash_or_privacy_check_does_not_report_success(options, match):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"ogg"))) as http:
        with pytest.raises(ValueError, match=match):
            await migration.migrate_audio(settings(), rows(), http, FakeTigris(**options), apply=True)


async def test_conflicting_destination_is_never_overwritten():
    store = FakeTigris({"wuumtaaba/media/a.audio": b"different"})
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"ogg"))) as http:
        with pytest.raises(ValueError, match="no overwrite"):
            await migration.migrate_audio(settings(), rows(), http, store, apply=True)
    assert not store.puts


@pytest.mark.parametrize("status", [302, 403, 404])
async def test_source_errors_do_not_upload_or_follow_redirects(status):
    requests = []

    def source(request):
        requests.append(request)
        return httpx.Response(status, headers={"location": "https://elsewhere.invalid/"})

    store = FakeTigris()
    async with httpx.AsyncClient(transport=httpx.MockTransport(source), follow_redirects=True) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await migration.migrate_audio(settings(), rows(), http, store, apply=True)
    assert len(requests) == 1 and not store.puts


async def test_source_size_mismatch_stops_before_upload():
    store = FakeTigris()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"short"))) as http:
        with pytest.raises(ValueError, match="differs from inventory"):
            await migration.migrate_audio(settings(), rows(), http, store, apply=True)
    assert not store.puts


@pytest.mark.parametrize("name", ["../a.audio", "x/../a", "/a.audio", "x%2Fa", "x?key=secret"])
async def test_unsafe_inventory_keys_rejected_even_in_dry_run(name):
    with pytest.raises(ValueError, match="object key"):
        await migration.migrate_audio(None, rows(name), None)


async def test_source_stream_bound_stops_before_upload(monkeypatch):
    monkeypatch.setattr(migration, "MAX_AUDIO_BYTES", 4)
    store = FakeTigris()
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200, content=b"12345"))) as http:
        with pytest.raises(ValueError, match="exceeds size limit"):
            await migration.migrate_audio(settings(), [{"name": "a.audio"}], http, store, apply=True)
    assert not store.puts
