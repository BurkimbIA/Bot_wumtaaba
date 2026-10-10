"""File durable de rappels ; aucun envoi réseau dans l'ordonnanceur."""

import json
import re
import time

from .domain import text

WINDOW = 23 * 3600
MAX_LATENESS = 24 * 3600

_JOIN = """
SELECT r.*, e.status AS event_status, e.customer_phone, e.owner_sender,
       a.reference, a.total, a.paid, a.due_date, a.client_phone,
       c.reminders_enabled, v.last_inbound_at,
       COALESCE(k.granted,0) AS consent, k.source_message_id AS consent_source
FROM reminders r
JOIN calendar_events e ON e.id=r.event_id
JOIN agreements a ON a.id=e.agreement_id
LEFT JOIN contacts c ON c.id=r.recipient
LEFT JOIN conversations v ON v.id=r.recipient
LEFT JOIN customer_consents k ON k.agreement_id=a.id AND k.phone=a.client_phone
"""


def _reason(row, now):
    if now - row["scheduled_at"] >= MAX_LATENESS:
        return "missed"
    if row["scheduled_at"] > now:
        return "not_due"
    if row["event_status"] != "open" or row["total"] is None or row["paid"] is None:
        return "cancelled"
    if row["paid"] >= row["total"]:
        return "paid"
    if row["reminders_enabled"] != 1:
        return "stopped"
    if row["role"] not in {"merchant", "customer"}:
        return "cancelled"
    if (not row["client_phone"] or row["client_phone"] != row["customer_phone"]
            or row["consent"] != 1 or not row["consent_source"]):
        return "pending_consent"
    expected = row["owner_sender"] if row["role"] == "merchant" else row["client_phone"]
    if row["recipient"] != expected:
        return "cancelled"
    return None



def _pending_optout(store, row):
    """Un retrait reçu prend effet avant les messages coûteux encore en traitement."""
    records = store.db.execute(
        "SELECT sender,message FROM inbox WHERE sender IN (?,?) "
        "AND status IN ('pending','processing')",
        (row["recipient"], row["client_phone"]),
    ).fetchall()
    for record in records:
        message = json.loads(record["message"])
        if message.get("type") != "text":
            continue
        body = message.get("text", {}).get("body", "")
        if not isinstance(body, str):
            continue
        command = body.strip().casefold()
        if command == "stop":
            return True
        refusal = re.fullmatch(r"refuser\s+(?:wt-)?([a-f0-9]{8})", command)
        if (record["sender"] == row["client_phone"] and refusal
                and "WT-" + refusal[1].upper() == row["reference"]):
            return True
    return False


def _within_window(row, now):
    stamp = row["last_inbound_at"]
    return stamp is not None and 0 <= now - stamp < WINDOW


def _payload(row, settings, now):
    balance = str(row["total"] - row["paid"])
    reference = row["reference"]
    due = row["due_date"]
    if _within_window(row, now):
        return text(
            f"Rappel pour l’accord {reference} : solde {balance} F CFA, échéance {due}. "
            "Envoyez STOP pour arrêter les rappels."
        )
    if not settings.reminder_template_name:
        return None
    return {
        "type": "template",
        "template": {
            "name": settings.reminder_template_name,
            "language": {"code": settings.reminder_template_language},
            "components": [{"type": "body", "parameters": [
                {"type": "text", "text": value} for value in (reference, balance, due)
            ]}],
        },
    }


def queue_due_reminders(store, settings, now=None):
    """Réévalue consentement/template, puis inscrit rappel+sortie dans un seul commit."""
    if not settings.reminder_enabled:
        return 0
    now = time.time() if now is None else now
    queued = 0
    with store.lock, store.db:
        # BEGIN IMMEDIATE protège également contre deux connexions/processus concurrents.
        store.db.execute("BEGIN IMMEDIATE")
        rows = store.db.execute(
            _JOIN + " WHERE r.status IN ('pending','pending_consent','blocked_template') "
            "AND r.outbox_id IS NULL AND r.scheduled_at<=? ORDER BY r.scheduled_at,r.id",
            (now,),
        ).fetchall()
        for row in rows:
            if _pending_optout(store, row):
                continue
            reason = _reason(row, now)
            payload = None if reason else _payload(row, settings, now)
            if reason or payload is None:
                status = reason or "blocked_template"
                store.db.execute("UPDATE reminders SET status=? WHERE id=?", (status, row["id"]))
                if status != row["status"]:
                    store._audit("reminder_status", row["id"], {"status": status})
                continue
            outbox_id = store.queue_outbox(row["recipient"], payload, created_at=now)
            store.db.execute(
                "UPDATE outbox SET category='reminder',reminder_id=? WHERE id=?",
                (row["id"], outbox_id),
            )
            store.db.execute(
                "UPDATE reminders SET status='queued',payload=?,outbox_id=? WHERE id=?",
                (json.dumps(payload), outbox_id, row["id"]),
            )
            store._audit("reminder_queued", row["id"], {"outbox_id": outbox_id})
            queued += 1
    return queued


def can_send_outbox(store, outgoing, now=None):
    """Dernier contrôle juste avant l'envoi (STOP/révocation/paiement/fenêtre Meta)."""
    if outgoing.get("category") != "reminder":
        return True
    now = time.time() if now is None else now
    with store.lock, store.db:
        row = store.db.execute(_JOIN + " WHERE r.id=?", (outgoing.get("reminder_id"),)).fetchone()
        if row is None or row["outbox_id"] != outgoing["id"] or row["status"] != "queued":
            return False
        reason = "cancelled_consent" if _pending_optout(store, row) else _reason(row, now)
        if not reason and outgoing["content"].get("type") == "text" and not _within_window(row, now):
            reason = "missed_window"
        if reason:
            # Une sortie déjà créée ne peut jamais être réinsérée après révocation.
            status = "cancelled_consent" if reason == "pending_consent" else reason
            store.db.execute("UPDATE reminders SET status=? WHERE id=?", (status, row["id"]))
            store._audit("reminder_status", row["id"], {"status": status})
            return False
        return True
