"""The volume copy against a database written by v0.7 (tests/fixtures/v0.7.sqlite3, upgraded by SQLite 0.7 commit 0071d69)."""

import importlib.util
import hashlib
import json
import sqlite3
import shutil

import pytest
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "v0.7.sqlite3"
spec = importlib.util.spec_from_file_location(
    "copy_volume", Path(__file__).parents[1] / "scripts" / "copy_volume_to_supabase.py")
copy_volume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_volume)


def test_every_v07_row_reaches_postgres_and_reads_back(store):
    report = copy_volume.copy_rows(FIXTURE, store)
    source = sqlite3.connect(FIXTURE)
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {table for table, _, _ in report} == tables - {"sqlite_sequence", "schema_migrations"}
    assert all(copied == stored for _, copied, stored in report)
    assert [a["client"] for a in store.list_agreements("22670000001")] == ["Awa"]
    assert store.calendar_for("22670000001")[0]["reference"] == "WT-ABCD0000"
    assert store.db.execute("SELECT provider FROM app_model_config").fetchone()[0] == "mac"
    assert store.db.execute("SELECT transcript FROM media_assets WHERE transcript IS NOT NULL").fetchone()[0] == "Bonjour"
    # New rows continue after the copied ids instead of colliding with them.
    largest = source.execute("SELECT MAX(id) FROM outbox").fetchone()[0]
    with store.lock, store.db:
        assert store.queue_outbox("22670000001", {"type": "text", "text": {"body": "après"}}) > largest
    store.enqueue("wamid.after", "22670000003", {"type": "text", "text": {"body": "menu"}})
    assert store.claim_inbox()["id"] == "wamid.voice"  # oldest pending message first, as on SQLite


def test_copy_twice_changes_nothing(store):
    first = copy_volume.copy_rows(FIXTURE, store)
    assert copy_volume.copy_rows(FIXTURE, store, replace_snapshot=True) == first


async def test_audio_files_go_to_the_bucket_with_their_type(store, media, tmp_path):
    copy_volume.copy_rows(FIXTURE, store)
    name = "f" * 64 + ".audio"
    (tmp_path / name).write_bytes(b"OggS voice")
    (tmp_path / ".audio-partial").write_bytes(b"temporary file of an interrupted write")
    assert await copy_volume.upload_media(tmp_path, media, store) == 1
    assert media.objects == {name: (b"OggS voice", "audio/ogg")}


def test_copy_refuses_nonempty_target_without_explicit_replacement(store):
    copy_volume.copy_rows(FIXTURE, store)
    with pytest.raises(ValueError, match="Cible non vide"):
        copy_volume.copy_rows(FIXTURE, store)


def test_copy_restores_updates_deletions_and_mobile_ledger(tmp_path, store):
    source_path = tmp_path / "snapshot.sqlite3"
    shutil.copyfile(FIXTURE, source_path)
    copy_volume.copy_rows(source_path, store)
    assert store.db.execute("SELECT COUNT(*) FROM payments").fetchone()[0] > 0
    assert store.db.execute("SELECT COUNT(*) FROM mobile_tokens").fetchone()[0] == 1
    with sqlite3.connect(source_path) as source:
        source.execute("UPDATE inbox SET status='done'")
        source.execute("UPDATE agreements SET product='Nouveau produit'")
        source.execute("DELETE FROM mobile_tokens")
        source.execute("DELETE FROM mobile_codes")
    copy_volume.copy_rows(source_path, store, replace_snapshot=True)
    assert store.db.execute("SELECT COUNT(*) FROM inbox WHERE status<>'done'").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM agreements WHERE product<>'Nouveau produit'").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM mobile_tokens").fetchone()[0] == 0
    assert store.db.execute("SELECT COUNT(*) FROM mobile_codes").fetchone()[0] == 0


@pytest.mark.parametrize("change", ["CREATE TABLE forgotten(id TEXT)", "ALTER TABLE contacts ADD COLUMN forgotten TEXT"])
def test_copy_refuses_unknown_source_schema_before_changing_target(tmp_path, store, change):
    copy_volume.copy_rows(FIXTURE, store)
    before = store.db.execute("SELECT * FROM agreements ORDER BY id").fetchall()
    source_path = tmp_path / "unknown.sqlite3"
    shutil.copyfile(FIXTURE, source_path)
    with sqlite3.connect(source_path) as source:
        source.execute(change)
    with pytest.raises(ValueError):
        copy_volume.copy_rows(source_path, store, replace_snapshot=True)
    assert store.db.execute("SELECT * FROM agreements ORDER BY id").fetchall() == before


def test_copy_rolls_back_target_when_source_data_is_invalid(tmp_path, store):
    copy_volume.copy_rows(FIXTURE, store)
    before = store.db.execute("SELECT * FROM agreements ORDER BY id").fetchall()
    source_path = tmp_path / "invalid.sqlite3"
    shutil.copyfile(FIXTURE, source_path)
    with sqlite3.connect(source_path) as source:
        source.execute("PRAGMA ignore_check_constraints=ON")
        source.execute("UPDATE payments SET amount=-1")
    import psycopg
    with pytest.raises(psycopg.errors.CheckViolation):
        copy_volume.copy_rows(source_path, store, replace_snapshot=True)
    assert store.db.execute("SELECT * FROM agreements ORDER BY id").fetchall() == before


async def test_missing_referenced_audio_blocks_copy(store, media, tmp_path):
    copy_volume.copy_rows(FIXTURE, store)
    with pytest.raises(ValueError, match="absents"):
        await copy_volume.upload_media(tmp_path, media, store)
    assert media.objects == {}


@pytest.mark.parametrize("corruption", ["hash", "size"])
async def test_corrupt_referenced_audio_blocks_copy(store, media, tmp_path, corruption):
    copy_volume.copy_rows(FIXTURE, store)
    name = "f" * 64 + ".audio"
    (tmp_path / name).write_bytes(b"broken")
    with store.db:
        store.db.execute("UPDATE media_assets SET sha256=%s,metadata=%s", (
            hashlib.sha256(b"expected").hexdigest() if corruption == "hash" else None,
            json.dumps({"byte_count": 100}) if corruption == "size" else "{}"))
    with pytest.raises(ValueError, match="incorrecte"):
        await copy_volume.upload_media(tmp_path, media, store)
    assert media.objects == {}


async def test_unreferenced_outgoing_audio_is_preserved_and_readback_checked(store, media, tmp_path):
    (tmp_path / "outgoing.mp3").write_bytes(b"outgoing")
    assert await copy_volume.upload_media(tmp_path, media, store) == 1
    assert media.objects["outgoing.mp3"][0] == b"outgoing"
    async def corrupted_get(name):
        return b"corrupted"
    media.get = corrupted_get
    with pytest.raises(ValueError, match="Relecture"):
        await copy_volume.upload_media(tmp_path, media, store)


def test_old_v05_snapshot_is_rejected_before_copy(store):
    with pytest.raises(ValueError, match="0.7"):
        copy_volume.copy_rows(FIXTURE.with_name("v0.5.sqlite3"), store)
    assert store.db.execute("SELECT COUNT(*) FROM agreements").fetchone()[0] == 0
