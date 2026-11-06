"""Shared write helpers: contact, message log and agreement calendar rows."""

import json
from datetime import datetime, timedelta
from zoneinfo import ZoneInfo


def contact(db, sender, timestamp, inbound=False):
    db.execute("INSERT INTO contacts(id,created_at) VALUES(%s,%s) ON CONFLICT DO NOTHING", (sender, timestamp))
    db.execute(
        "INSERT INTO conversations(id,contact_id,last_inbound_at,created_at) VALUES(%s,%s,%s,%s) "
        "ON CONFLICT(id) DO UPDATE SET last_inbound_at=CASE WHEN excluded.last_inbound_at "
        "IS NULL THEN conversations.last_inbound_at ELSE GREATEST(COALESCE(conversations.last_inbound_at,0),excluded.last_inbound_at) END",
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
        "INSERT INTO messages(id,conversation_id,direction,type,text,payload,"
        "source_message_id,outbox_id,remote_id,status,occurred_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT DO NOTHING",
        (identifier, sender, direction, kind, body, json.dumps(payload), source,
         outbox_id, remote_id, status, timestamp),
    )
    if kind in {"audio", "image", "video", "document", "sticker"}:
        media = payload.get(kind, {})
        db.execute(
            "INSERT INTO media_assets(message_id,media_id,mime_type,sha256,metadata,updated_at) VALUES(%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
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
        "UPDATE agreements SET reference=%s,client_phone=%s,contact_id=%s,conversation_id=%s,"
        "client_name=%s,product=%s,total=%s,paid=%s,due_date=%s WHERE id=%s",
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
        "INSERT INTO calendar_events(id,agreement_id,owner_sender,customer_phone,starts_at,timezone,status) VALUES(%s,%s,%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
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
            enabled = db.execute("SELECT reminders_enabled FROM contacts WHERE id=%s", (recipient,)).fetchone()[0]
            db.execute(
                "INSERT INTO reminders(event_id,recipient,role,scheduled_at,status) VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (identifier, recipient, role, when, status if enabled else "stopped"),
            )
