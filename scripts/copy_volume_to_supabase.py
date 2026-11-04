"""Copie le SQLite et les audios du volume Fly vers Supabase, sans rien effacer.

    fly ssh sftp get /app/data/bot.sqlite3 backup/bot.sqlite3 --app bot-wumtaaba
    uv run python scripts/copy_volume_to_supabase.py backup/bot.sqlite3 backup/media

Relancer la copie est sans risque : une ligne déjà présente est ignorée, un audio est réécrit à l'identique.
Le script s'arrête en erreur si une table compte moins de lignes dans Postgres que dans SQLite.
"""

import asyncio
import sqlite3
import sys
from pathlib import Path

import httpx
from psycopg import sql

from bot_wumtaaba.config import Settings
from bot_wumtaaba.media import MediaStore
from bot_wumtaaba.store import Store

# Parents before children, for the foreign keys.
TABLES = [
    "contacts", "conversations", "sessions", "agreements", "inbox", "outbox", "messages", "media_assets",
    "delivery_events", "audit_events", "calendar_events", "reminders", "customer_consents", "app_model_config",
]
IDENTITIES = {"outbox": "id", "delivery_events": "id", "audit_events": "id", "reminders": "id"}


def copy_rows(source_path, store):
    """Returns (table, rows in SQLite, rows in Postgres) for every table both sides have."""
    source = sqlite3.connect(f"file:{source_path}?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    report = []
    with store.lock, store.db:
        for table in TABLES:
            if not source.execute("SELECT 1 FROM sqlite_master WHERE type='table' AND name=?", (table,)).fetchone():
                continue
            target = {row["column_name"] for row in store.db.execute(
                "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                (store.db.schema, table))}
            # rowid order keeps the inbox seq in arrival order.
            cursor = source.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
            columns = [column[0] for column in cursor.description if column[0] in target]
            if table == "app_model_config":
                store.db.execute("DELETE FROM app_model_config")  # the copied choice replaces the default
            query = sql.SQL("INSERT INTO {} ({}) VALUES ({}) ON CONFLICT DO NOTHING").format(
                sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                sql.SQL(",").join(sql.Placeholder() * len(columns)))
            store.db.connection.cursor().executemany(query, [[row[c] for c in columns] for row in cursor])
            copied = source.execute(f'SELECT COUNT(*) FROM "{table}"').fetchone()[0]
            stored = store.db.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(sql.Identifier(table))).fetchone()[0]
            report.append((table, copied, stored))
        # Copied ids were explicit: move each sequence past them.
        for table, column in IDENTITIES.items():
            store.db.execute(sql.SQL(
                "SELECT setval(pg_get_serial_sequence({t}, {c}), COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)"
            ).format(t=sql.Literal(table), c=sql.Literal(column), col=sql.Identifier(column), table=sql.Identifier(table)))
    source.close()
    return report


async def upload_media(directory, media, store):
    mimes = {row["storage_path"]: row["mime_type"] for row in store.db.execute(
        "SELECT storage_path,mime_type FROM media_assets WHERE storage_path IS NOT NULL")}
    files = sorted(path for path in Path(directory).iterdir() if path.is_file() and not path.name.startswith("."))
    for path in files:
        await media.put(path.name, path.read_bytes(), mimes.get(path.name) or "application/octet-stream")
    return len(files)


async def create_bucket(settings, http):
    key = settings.supabase_api_key.get_secret_value()
    response = await http.post(f"{settings.supabase_url.rstrip('/')}/storage/v1/bucket",
                               headers={"apikey": key, "Authorization": "Bearer " + key},
                               json={"id": settings.media_bucket, "name": settings.media_bucket, "public": False})
    if response.status_code not in {200, 409} and "already exists" not in response.text:
        response.raise_for_status()


async def main(source_path, media_directory):
    settings = Settings()
    store = Store(settings.database_url.get_secret_value(), settings.database_schema)
    report = copy_rows(source_path, store)
    for table, copied, stored in report:
        print(f"{table:20} SQLite {copied:6}  Postgres {stored:6}")
    async with httpx.AsyncClient(timeout=60) as http:
        await create_bucket(settings, http)
        count = await upload_media(media_directory, MediaStore(settings, http), store)
    print(f"{count} audio(s) copied to the private bucket {settings.media_bucket}")
    store.close()
    missing = [table for table, copied, stored in report if stored < copied]
    if missing:
        raise SystemExit("Rows missing in Postgres: " + ", ".join(missing))


if __name__ == "__main__":
    if len(sys.argv) != 3:
        raise SystemExit(__doc__)
    asyncio.run(main(sys.argv[1], sys.argv[2]))
