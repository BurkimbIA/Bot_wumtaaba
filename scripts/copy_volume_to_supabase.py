"""Restaure un snapshot SQLite 0.7 complet et ses audios dans Supabase.

    fly ssh sftp get /app/data/bot.sqlite3 backup/bot.sqlite3 --app bot-wumtaaba
    uv run python scripts/copy_volume_to_supabase.py backup/bot.sqlite3 backup/media

Arrêter les écritures source et le bot cible avant la copie finale.
--replace-snapshot remplace les tables connues dans une transaction et vérifie chaque valeur.
Une source de version antérieure ou un schéma inconnu est refusé avant remplacement.
"""

import asyncio
import hashlib
import json
import sqlite3
import argparse
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
    "payments", "mobile_tokens", "mobile_codes",
]
IDENTITIES = {"outbox": "id", "delivery_events": "id", "audit_events": "id", "reminders": "id"}


def copy_rows(source_path, store, *, replace_snapshot=False):
    """Restore a complete 0.7 snapshot; the source and target bot must be offline.

    Replacing a previous rehearsal is explicit. All data changes, including deletions
    and queue states, are restored together; a failure rolls the target back.
    """
    source = sqlite3.connect(Path(source_path).resolve().as_uri() + "?mode=ro", uri=True)
    source.row_factory = sqlite3.Row
    try:
        source.execute("BEGIN")
        tables = {row[0] for row in source.execute(
            "SELECT name FROM sqlite_master WHERE type='table' AND name NOT LIKE 'sqlite_%'")}
        if tables - {"schema_migrations"} != set(TABLES):
            raise ValueError("Schéma source inattendu : utiliser une sauvegarde SQLite 0.7 complète")
        report = []
        with store.lock, store.db:
            columns_by_table = {}
            for table in TABLES:
                columns = [row[1] for row in source.execute(f'PRAGMA table_info("{table}")')]
                target = {row["column_name"] for row in store.db.execute(
                    "SELECT column_name FROM information_schema.columns WHERE table_schema=%s AND table_name=%s",
                    (store.db.schema, table))}
                if set(columns) != target - ({"seq"} if table == "inbox" else set()):
                    raise ValueError("Colonnes source/cible incompatibles : " + table)
                columns_by_table[table] = columns
                existing = store.db.execute(sql.SQL("SELECT COUNT(*) FROM {}").format(
                    sql.Identifier(table))).fetchone()[0]
                if existing and table != "app_model_config" and not replace_snapshot:
                    raise ValueError("Cible non vide : arrêter le bot cible puis utiliser --replace-snapshot")
            # No CASCADE: foreign keys outside the known schema must fail closed.
            store.db.execute(sql.SQL("TRUNCATE {} RESTART IDENTITY").format(
                sql.SQL(",").join(map(sql.Identifier, TABLES))))
            for table in TABLES:
                columns = columns_by_table[table]
                cursor = source.execute(f'SELECT * FROM "{table}" ORDER BY rowid')
                rows = [tuple(row[column] for column in columns) for row in cursor]
                query = sql.SQL("INSERT INTO {} ({}) VALUES ({})").format(
                    sql.Identifier(table), sql.SQL(",").join(map(sql.Identifier, columns)),
                    sql.SQL(",").join(sql.Placeholder() * len(columns)))
                store.db.connection.cursor().executemany(query, rows)
                restored = list(store.db.execute(sql.SQL("SELECT {} FROM {}").format(
                    sql.SQL(",").join(map(sql.Identifier, columns)), sql.Identifier(table))))
                if sorted(rows, key=repr) != sorted((tuple(row.values()) for row in restored), key=repr):
                    raise ValueError("Vérification des données échouée : " + table)
                report.append((table, len(rows), len(restored)))
            for table, column in IDENTITIES.items():
                store.db.execute(sql.SQL(
                    "SELECT setval(pg_get_serial_sequence({t}, {c}), COALESCE((SELECT MAX({col}) FROM {table}), 0) + 1, false)"
                ).format(t=sql.Literal(table), c=sql.Literal(column), col=sql.Identifier(column), table=sql.Identifier(table)))
        return report
    finally:
        source.close()


async def upload_media(directory, media, store):
    assets = {row["storage_path"]: row for row in store.db.execute(
        "SELECT storage_path,mime_type,sha256,metadata FROM media_assets WHERE storage_path IS NOT NULL")}
    files = sorted(path for path in Path(directory).iterdir() if path.is_file() and not path.name.startswith("."))
    by_name = {path.name: path for path in files}
    missing = set(assets) - set(by_name)
    if missing:
        raise ValueError("Audios référencés absents de l'export : " + ", ".join(sorted(missing)))
    # Validate every referenced file before uploading; preserve unreferenced outgoing files too.
    for name, asset in assets.items():
        data = by_name[name].read_bytes()
        metadata = json.loads(asset["metadata"])
        expected_digest = asset["sha256"] or metadata.get("sha256")
        expected_size = metadata.get("byte_count")
        if expected_digest and hashlib.sha256(data).hexdigest() != expected_digest:
            raise ValueError("Empreinte audio incorrecte : " + name)
        if expected_size is not None and len(data) != expected_size:
            raise ValueError("Taille audio incorrecte : " + name)
    for path in files:
        data = path.read_bytes()
        mime = assets.get(path.name, {}).get("mime_type") or "application/octet-stream"
        await media.put(path.name, data, mime)
        if await media.get(path.name) != data:
            raise ValueError("Relecture audio différente : " + path.name)
    return len(files)


async def create_bucket(settings, http):
    key = settings.supabase_api_key.get_secret_value()
    response = await http.post(f"{settings.supabase_url.rstrip('/')}/storage/v1/bucket",
                               headers={"apikey": key, "Authorization": "Bearer " + key},
                               json={"id": settings.media_bucket, "name": settings.media_bucket, "public": False})
    if response.status_code not in {200, 409} and "already exists" not in response.text:
        response.raise_for_status()


async def main(source_path, media_directory, *, replace_snapshot=False):
    settings = Settings()
    store = Store(settings.database_url.get_secret_value(), settings.database_schema)
    report = copy_rows(source_path, store, replace_snapshot=replace_snapshot)
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
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("source_path")
    parser.add_argument("media_directory")
    parser.add_argument("--replace-snapshot", action="store_true",
                        help="Remplacer une répétition précédente ; source et cible hors service")
    args = parser.parse_args()
    asyncio.run(main(args.source_path, args.media_directory, replace_snapshot=args.replace_snapshot))
