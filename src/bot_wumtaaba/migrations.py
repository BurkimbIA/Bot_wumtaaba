"""Versioned, transactional SQLite migration and historical audit backfill."""

import json
import sqlite3
import time
from datetime import datetime, timedelta
from pathlib import Path
from zoneinfo import ZoneInfo

DDL = [
    "CREATE TABLE contacts(id TEXT PRIMARY KEY,reminders_enabled INTEGER NOT NULL DEFAULT 1,created_at REAL NOT NULL)",
    "CREATE TABLE conversations(id TEXT PRIMARY KEY,contact_id TEXT NOT NULL REFERENCES contacts(id),last_inbound_at REAL,created_at REAL NOT NULL)",
    "CREATE TABLE messages(id TEXT PRIMARY KEY,conversation_id TEXT NOT NULL REFERENCES conversations(id),direction TEXT NOT NULL,type TEXT NOT NULL,text TEXT,payload TEXT NOT NULL,source_message_id TEXT,outbox_id INTEGER UNIQUE,remote_id TEXT,status TEXT NOT NULL,occurred_at REAL NOT NULL)",
    "CREATE INDEX messages_conversation ON messages(conversation_id,occurred_at)",
    "CREATE TABLE media_assets(message_id TEXT PRIMARY KEY REFERENCES messages(id),media_id TEXT,mime_type TEXT,sha256 TEXT,storage_path TEXT,transcript TEXT,metadata TEXT NOT NULL DEFAULT '{}',updated_at REAL NOT NULL)",
    "CREATE TABLE delivery_events(id INTEGER PRIMARY KEY,remote_id TEXT NOT NULL,status TEXT NOT NULL,occurred_at REAL NOT NULL,payload TEXT NOT NULL,UNIQUE(remote_id,status,occurred_at))",
    "CREATE TABLE audit_events(id INTEGER PRIMARY KEY,kind TEXT NOT NULL,entity_id TEXT,details TEXT NOT NULL,created_at REAL NOT NULL)",
    "CREATE TABLE calendar_events(id TEXT PRIMARY KEY,agreement_id TEXT NOT NULL UNIQUE REFERENCES agreements(id),owner_sender TEXT NOT NULL,customer_phone TEXT,starts_at REAL NOT NULL,timezone TEXT NOT NULL,status TEXT NOT NULL DEFAULT 'open')",
    "CREATE TABLE reminders(id INTEGER PRIMARY KEY,event_id TEXT NOT NULL REFERENCES calendar_events(id),recipient TEXT NOT NULL,role TEXT NOT NULL,scheduled_at REAL NOT NULL,status TEXT NOT NULL,payload TEXT,outbox_id INTEGER UNIQUE,UNIQUE(event_id,recipient,scheduled_at))",
    "CREATE INDEX reminders_due ON reminders(status,scheduled_at)",
    "CREATE TABLE customer_consents(agreement_id TEXT NOT NULL REFERENCES agreements(id),phone TEXT NOT NULL,granted INTEGER NOT NULL,updated_at REAL NOT NULL,source_message_id TEXT,PRIMARY KEY(agreement_id,phone))",
]


def contact(db, sender, timestamp, inbound=False):
    db.execute("INSERT OR IGNORE INTO contacts(id,created_at) VALUES(?,?)", (sender, timestamp))
    db.execute(
        "INSERT INTO conversations(id,contact_id,last_inbound_at,created_at) VALUES(?,?,?,?) "
        "ON CONFLICT(id) DO UPDATE SET last_inbound_at=CASE WHEN excluded.last_inbound_at "
        "IS NULL THEN conversations.last_inbound_at ELSE MAX(COALESCE(conversations.last_inbound_at,0),excluded.last_inbound_at) END",
        (sender, sender, timestamp if inbound else None, timestamp),
    )


def log_message(db, identifier, sender, payload, direction, timestamp, status, source=None,
                outbox_id=None, remote_id=None):
    contact(db, sender, timestamp, direction == "inbound")
    kind = payload.get("type", "unknown")
    body = payload.get("text", {}).get("body")
    if kind == "interactive":
        interactive = payload.get("interactive", {})
        body = interactive.get("body", {}).get("text")
        response = interactive.get("button_reply", interactive.get("list_reply", {}))
        body = body or response.get("title")
    db.execute(
        "INSERT OR IGNORE INTO messages(id,conversation_id,direction,type,text,payload,"
        "source_message_id,outbox_id,remote_id,status,occurred_at) VALUES(?,?,?,?,?,?,?,?,?,?,?)",
        (identifier, sender, direction, kind, body, json.dumps(payload), source,
         outbox_id, remote_id, status, timestamp),
    )
    if direction == "inbound" and kind in {"audio", "image", "video", "document", "sticker"}:
        media = payload.get(kind, {})
        db.execute(
            "INSERT OR IGNORE INTO media_assets(message_id,media_id,mime_type,sha256,metadata,updated_at) VALUES(?,?,?,?,?,?)",
            (identifier, media.get("id"), media.get("mime_type"), media.get("sha256"), json.dumps(media), timestamp),
        )


def normalize_agreement(db, identifier, sender, data, created_at, now):
    contact(db, sender, created_at)
    phone = data.get("client_phone") or None
    if phone:
        phone = str(phone).lstrip("+")
        contact(db, phone, created_at)
    reference = "WT-" + identifier[:8].upper()
    db.execute(
        "UPDATE agreements SET reference=?,client_phone=?,contact_id=?,conversation_id=?,"
        "client_name=?,product=?,total=?,paid=?,due_date=? WHERE id=?",
        (reference, phone, phone, sender, data.get("client"), data.get("product"),
         data.get("total"), data.get("paid"), data.get("due_date"), identifier),
    )
    try:
        due = datetime.strptime(data["due_date"], "%Y-%m-%d").replace(
            hour=9, tzinfo=ZoneInfo("Africa/Ouagadougou")
        )
        paid = int(data.get("paid", 0)) >= int(data["total"])
    except (KeyError, TypeError, ValueError):
        return
    db.execute(
        "INSERT OR IGNORE INTO calendar_events(id,agreement_id,owner_sender,customer_phone,starts_at,timezone,status) VALUES(?,?,?,?,?,?,?)",
        (identifier, identifier, sender, phone, due.timestamp(), "Africa/Ouagadougou", "paid" if paid else "open"),
    )
    if paid:
        return
    for days in (1, 0):
        when = (due - timedelta(days=days)).timestamp()
        if when <= now:
            continue
        for recipient, role, status in [(sender, "merchant", "pending_consent"), (phone, "customer", "pending_consent")]:
            if not recipient:
                continue
            enabled = db.execute("SELECT reminders_enabled FROM contacts WHERE id=?", (recipient,)).fetchone()[0]
            db.execute(
                "INSERT OR IGNORE INTO reminders(event_id,recipient,role,scheduled_at,status) VALUES(?,?,?,?,?)",
                (identifier, recipient, role, when, status if enabled else "stopped"),
            )


def migrate(db, path):
    db.execute("CREATE TABLE IF NOT EXISTS schema_migrations(version INTEGER PRIMARY KEY,applied_at REAL NOT NULL)")
    db.commit()
    if db.execute("SELECT 1 FROM schema_migrations WHERE version=2").fetchone():
        return
    if str(path) != ":memory:":
        backup_path = Path(str(path) + ".pre-v2.sqlite3")
        if not backup_path.exists():
            backup = sqlite3.connect(str(backup_path))
            try:
                db.backup(backup)
            finally:
                backup.close()
            backup_path.chmod(0o600)
    now = time.time()
    with db:
        db.execute("BEGIN IMMEDIATE")
        db.execute("ALTER TABLE outbox ADD COLUMN category TEXT NOT NULL DEFAULT 'reply'")
        db.execute("ALTER TABLE outbox ADD COLUMN reminder_id INTEGER")
        db.execute("CREATE UNIQUE INDEX outbox_reminder ON outbox(reminder_id) WHERE reminder_id IS NOT NULL")
        for statement in DDL:
            db.execute(statement)
        for name, kind in [("reference", "TEXT"), ("client_phone", "TEXT"), ("contact_id", "TEXT"), ("conversation_id", "TEXT"), ("client_name", "TEXT"), ("product", "TEXT"), ("total", "INTEGER"), ("paid", "INTEGER"), ("due_date", "TEXT")]:
            db.execute(f"ALTER TABLE agreements ADD COLUMN {name} {kind}")
        for row in db.execute("SELECT * FROM inbox").fetchall():
            payload = json.loads(row["message"])
            log_message(db, "in:" + row["id"], row["sender"], payload, "inbound", float(payload.get("timestamp", row["created_at"])), row["status"])
        for row in db.execute("SELECT * FROM outbox").fetchall():
            log_message(db, "out:" + str(row["id"]), row["recipient"], json.loads(row["content"]), "outbound", row["created_at"], row["status"], outbox_id=row["id"], remote_id=row["remote_id"])
        for row in db.execute("SELECT * FROM agreements").fetchall():
            normalize_agreement(db, row["id"], row["sender"], json.loads(row["data"]), row["created_at"], now)
        db.execute("INSERT INTO schema_migrations VALUES(2,?)", (now,))
        db.execute("INSERT INTO audit_events(kind,details,created_at) VALUES('migration',?,?)", (json.dumps({"version": 2}), now))
