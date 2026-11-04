"""The volume copy against a database written by v0.5 (tests/fixtures/v0.5.sqlite3, built with main)."""

import importlib.util
import sqlite3
from pathlib import Path

FIXTURE = Path(__file__).parent / "fixtures" / "v0.5.sqlite3"
spec = importlib.util.spec_from_file_location(
    "copy_volume", Path(__file__).parents[1] / "scripts" / "copy_volume_to_supabase.py")
copy_volume = importlib.util.module_from_spec(spec)
spec.loader.exec_module(copy_volume)


def test_every_v05_row_reaches_postgres_and_reads_back(store):
    report = copy_volume.copy_rows(FIXTURE, store)
    source = sqlite3.connect(FIXTURE)
    tables = {row[0] for row in source.execute("SELECT name FROM sqlite_master WHERE type='table'")}
    assert {table for table, _, _ in report} == tables - {"sqlite_sequence", "schema_migrations"}
    assert all(copied == stored and copied > 0 for _, copied, stored in report)
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
    assert copy_volume.copy_rows(FIXTURE, store) == first


async def test_audio_files_go_to_the_bucket_with_their_type(store, media, tmp_path):
    copy_volume.copy_rows(FIXTURE, store)
    name = "f" * 64 + ".audio"
    (tmp_path / name).write_bytes(b"OggS voice")
    (tmp_path / ".audio-partial").write_bytes(b"temporary file of an interrupted write")
    assert await copy_volume.upload_media(tmp_path, media, store) == 1
    assert media.objects == {name: (b"OggS voice", "audio/ogg")}
