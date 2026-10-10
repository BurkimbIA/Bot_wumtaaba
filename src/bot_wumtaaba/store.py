"""SQLite : état conversationnel et files durables, commit métier atomique."""

import json
import sqlite3
import threading
import time
from pathlib import Path

from .migrations import contact, log_message, migrate, normalize_agreement


class Store:
    def __init__(self, path):
        if str(path) != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        self.lock = threading.RLock()
        self.db = sqlite3.connect(str(path), check_same_thread=False)
        self.db.row_factory = sqlite3.Row
        self.db.executescript("""
            PRAGMA journal_mode=WAL;
            CREATE TABLE IF NOT EXISTS sessions (
                sender TEXT PRIMARY KEY, state TEXT NOT NULL, updated_at REAL NOT NULL);
            CREATE TABLE IF NOT EXISTS agreements (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, data TEXT NOT NULL,
                created_at REAL NOT NULL);
            CREATE INDEX IF NOT EXISTS agreement_owner ON agreements(sender, created_at);
            CREATE TABLE IF NOT EXISTS inbox (
                id TEXT PRIMARY KEY, sender TEXT NOT NULL, message TEXT NOT NULL,
                status TEXT NOT NULL DEFAULT 'pending', attempts INTEGER NOT NULL DEFAULT 0,
                created_at REAL NOT NULL, available_at REAL NOT NULL, error TEXT);
            CREATE TABLE IF NOT EXISTS outbox (
                id INTEGER PRIMARY KEY AUTOINCREMENT, recipient TEXT NOT NULL,
                content TEXT NOT NULL, status TEXT NOT NULL DEFAULT 'pending',
                attempts INTEGER NOT NULL DEFAULT 0, created_at REAL NOT NULL,
                available_at REAL NOT NULL, remote_id TEXT, error TEXT);
        """)

        migrate(self.db, path)
        self.db.execute("PRAGMA foreign_keys=ON")

    def enqueue(self, message_id, sender, message):
        now = time.time()
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO inbox(id,sender,message,created_at,available_at) "
                "VALUES(?,?,?,?,?)",
                (message_id, sender, json.dumps(message), now, now),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                log_message(self.db, "in:" + message_id, sender, message, "inbound",
                            float(message.get("timestamp", now)), "pending")
            return inserted

    def count_recent(self, sender, seconds=3600):
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM inbox WHERE sender=? AND created_at>?",
                (sender, time.time() - seconds),
            ).fetchone()[0]

    def get_session(self, sender):
        with self.lock:
            row = self.db.execute("SELECT state FROM sessions WHERE sender=?", (sender,)).fetchone()
            return json.loads(row[0]) if row else {}

    def list_agreements(self, sender, offset=0, limit=5):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,data,created_at FROM agreements WHERE sender=? "
                "ORDER BY created_at DESC,id DESC LIMIT ? OFFSET ?",
                (sender, limit, offset),
            )
            return [
                {**json.loads(row["data"]), "id": row["id"], "created_at": row["created_at"]}
                for row in rows
            ]

    def _claim(self, table, processing):
        with self.lock, self.db:
            owner = "sender" if table == "inbox" else "recipient"
            blockers = "('pending','processing')" if table == "inbox" else "('pending','sending')"
            row = self.db.execute(
                f"SELECT job.* FROM {table} job WHERE job.status='pending' AND job.available_at<=? "
                f"AND NOT EXISTS (SELECT 1 FROM {table} earlier WHERE earlier.{owner}=job.{owner} "
                f"AND earlier.status IN {blockers} AND earlier.rowid<job.rowid) "
                "ORDER BY job.created_at,job.rowid LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                f"UPDATE {table} SET status=?,attempts=attempts+1 WHERE id=?",
                (processing, row["id"]),
            )
            self.db.execute("UPDATE messages SET status=? WHERE id=?",
                            (processing, ("in:" if table == "inbox" else "out:") + str(row["id"])))
            result = dict(row)
            result["attempts"] += 1
            result["status"] = processing
            field = "message" if table == "inbox" else "content"
            result[field] = json.loads(result[field])
            return result

    def claim_inbox(self):
        return self._claim("inbox", "processing")

    def claim_outbox(self):
        return self._claim("outbox", "sending")

    def finish_inbox(self, message_id, sender, state, replies, agreement=None):
        now = time.time()
        with self.lock, self.db:
            row = self.db.execute(
                "SELECT status,message FROM inbox WHERE id=? AND sender=?", (message_id, sender)
            ).fetchone()
            if not row or row[0] != "processing":
                raise ValueError("Inbox non réservée ou déjà terminée")
            self.db.execute(
                "INSERT INTO sessions VALUES(?,?,?) ON CONFLICT(sender) "
                "DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at",
                (sender, json.dumps(state), now),
            )
            if agreement:
                self.db.execute(
                    "INSERT INTO agreements(id,sender,data,created_at) VALUES(?,?,?,?)",
                    (agreement["id"], sender, json.dumps(agreement), now),
                )
                normalize_agreement(self.db, agreement["id"], sender, agreement, now, now)
                self._audit("agreement_created", agreement["id"], {"sender": sender})
            # Les sorties héritent de la date du message entrant, pour la fenêtre Meta.
            received_at = min(now, int(json.loads(row["message"]).get("timestamp", now)))
            for reply in replies:
                self.queue_outbox(sender, reply, message_id, received_at)
            self.db.execute("UPDATE inbox SET status='done',error=NULL WHERE id=?", (message_id,))
            self.db.execute("UPDATE messages SET status='done' WHERE id=?", ("in:" + message_id,))

    def retry_inbox(self, message_id, error):
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM inbox WHERE id=?", (message_id,)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE inbox SET status=?,available_at=?,error=? WHERE id=?",
                    (
                        "failed" if row[0] >= 3 else "pending",
                        time.time() + 2 ** row[0],
                        str(error)[:200],
                        message_id,
                    ),
                )

                self.db.execute("UPDATE messages SET status=(SELECT status FROM inbox WHERE id=?) WHERE id=?", (message_id, "in:" + message_id))
                self._audit("inbox_retry", message_id, {"error": str(error)[:200]})

    def fail_inbox(self, message_id, error):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE inbox SET status='failed',error=? WHERE id=?",
                (str(error)[:200], message_id),
            )

            self.db.execute("UPDATE messages SET status='failed' WHERE id=?", ("in:" + message_id,))
            self._audit("inbox_failed", message_id, {"error": str(error)[:200]})

    def sent_outbox(self, message_id, remote_id):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status='sent',remote_id=?,error=NULL WHERE id=?",
                (remote_id, message_id),
            )

            self._sync_outbox(message_id)

    def fail_outbox(self, message_id, error, status="failed"):
        if status not in {"failed", "uncertain"}:
            raise ValueError("Statut de sortie invalide")
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=?,error=? WHERE id=?",
                (status, str(error)[:200], message_id),
            )

            self._sync_outbox(message_id)

    def retry_outbox(self, message_id, error, delay=60):
        """Uniquement après refus explicite HTTP 429, jamais après un timeout."""
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=CASE WHEN attempts>=3 THEN 'failed' "
                "ELSE 'pending' END,available_at=?,error=? WHERE id=?",
                (time.time() + delay, str(error)[:200], message_id),
            )

            self._sync_outbox(message_id)

    def recover(self):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE inbox SET status=CASE WHEN attempts>=3 THEN 'failed' "
                "ELSE 'pending' END WHERE status='processing'"
            )
            self.db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")
            for row in self.db.execute("SELECT id FROM outbox").fetchall():
                self._sync_outbox(row[0])
            self.db.execute("UPDATE messages SET status=(SELECT status FROM inbox WHERE 'in:'||inbox.id=messages.id) WHERE direction='inbound' AND EXISTS(SELECT 1 FROM inbox WHERE 'in:'||inbox.id=messages.id)")

    def purge(self, retention_days=0):
        if retention_days <= 0:
            return
        cutoff = time.time() - retention_days * 86400
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE updated_at<?", (cutoff,))
            # Agreements, audit and calendar are durable business records.
            self.db.execute(
                "DELETE FROM inbox WHERE created_at<? AND status IN ('done','failed')", (cutoff,)
            )
            self.db.execute(
                "DELETE FROM outbox WHERE created_at<? AND status IN ('sent','failed','uncertain')",
                (cutoff,),
            )

    def close(self):
        with self.lock:
            self.db.close()

    def _audit(self, kind, entity_id, details):
        self.db.execute(
            "INSERT INTO audit_events(kind,entity_id,details,created_at) VALUES(?,?,?,?)",
            (kind, str(entity_id) if entity_id is not None else None, json.dumps(details), time.time()),
        )

    def audit(self, kind, entity_id=None, details=None):
        with self.lock, self.db:
            self._audit(kind, entity_id, details or {})

    def queue_outbox(self, recipient, content, source_message_id=None, created_at=None):
        """Caller owns lock/transaction: used atomically by inbox and reminder workers."""
        now = time.time()
        cursor = self.db.execute(
            "INSERT INTO outbox(recipient,content,created_at,available_at) VALUES(?,?,?,?)",
            (recipient, json.dumps(content), now if created_at is None else created_at, now),
        )
        identifier = cursor.lastrowid
        log_message(self.db, "out:" + str(identifier), recipient, content, "outbound", now,
                    "pending", source=source_message_id, outbox_id=identifier)
        return identifier

    def _sync_outbox(self, identifier):
        row = self.db.execute("SELECT status,remote_id,error FROM outbox WHERE id=?", (identifier,)).fetchone()
        if row:
            self.db.execute("UPDATE messages SET status=?,remote_id=? WHERE outbox_id=?", (row[0], row[1], identifier))
            if row[1]:
                self._apply_delivery_status(row[1])
            if row[0] in {"sent", "uncertain", "failed"}:
                self.db.execute("UPDATE reminders SET status=? WHERE outbox_id=? AND status='queued'", (row[0], identifier))
            self._audit("outbox_status", identifier, dict(row))

    def record_media(self, message_id, metadata=None, transcript=None):
        identifier = message_id if message_id.startswith("in:") else "in:" + message_id
        metadata = metadata or {}
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM media_assets WHERE message_id=?", (identifier,)).fetchone()
            if not row:
                return False
            merged = {**json.loads(row["metadata"]), **metadata}
            self.db.execute(
                "UPDATE media_assets SET media_id=?,mime_type=?,sha256=?,storage_path=?,transcript=?,metadata=?,updated_at=? WHERE message_id=?",
                (metadata.get("media_id", row["media_id"]), metadata.get("mime_type", row["mime_type"]),
                 metadata.get("sha256", row["sha256"]), metadata.get("relative_path", metadata.get("storage_path", row["storage_path"])),
                 transcript if transcript is not None else row["transcript"], json.dumps(merged), time.time(), identifier),
            )
            self._audit("media_updated", identifier, {"status": merged.get("status"), "transcribed": transcript is not None})
            return True

    def record_delivery_status(self, status):
        remote_id = status.get("id")
        state = status.get("status")
        if not remote_id or state not in {"sent", "delivered", "read", "failed", "deleted"}:
            return False
        try:
            timestamp = float(status.get("timestamp", time.time()))
        except (TypeError, ValueError):
            return False
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO delivery_events(remote_id,status,occurred_at,payload) VALUES(?,?,?,?)",
                (remote_id, state, timestamp, json.dumps(status)),
            )
            if cursor.rowcount:
                self._apply_delivery_status(remote_id)
            return cursor.rowcount == 1

    def _apply_delivery_status(self, remote_id):
        latest = self.db.execute(
            "SELECT status FROM delivery_events WHERE remote_id=? ORDER BY occurred_at DESC,"
            "CASE status WHEN 'read' THEN 3 WHEN 'delivered' THEN 2 ELSE 1 END DESC LIMIT 1",
            (remote_id,),
        ).fetchone()
        if latest:
            self.db.execute("UPDATE messages SET status=? WHERE remote_id=?", (latest[0], remote_id))

    def calendar_for(self, sender, start=None, end=None):
        with self.lock:
            rows = self.db.execute(
                "SELECT e.*,a.reference,a.client_name AS client,a.product,a.total,a.paid,a.due_date "
                "FROM calendar_events e JOIN agreements a ON a.id=e.agreement_id "
                "WHERE e.owner_sender=? AND (? IS NULL OR e.starts_at>=?) "
                "AND (? IS NULL OR e.starts_at<=?) ORDER BY e.starts_at,e.id",
                (sender, start, start, end, end),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_customer_consent(self, sender, agreement_reference, granted, source_message_id=None):
        reference = agreement_reference.upper()
        if not reference.startswith("WT-"):
            reference = "WT-" + reference
        with self.lock, self.db:
            rows = self.db.execute("SELECT id FROM agreements WHERE client_phone=? AND reference=?", (sender, reference)).fetchall()
            if len(rows) != 1:
                return False
            identifier = rows[0][0]
            # A consent without a verifiable inbound is never usable by the scheduler.
            if source_message_id is None:
                candidate = self.db.execute("SELECT id FROM inbox WHERE sender=? AND status='processing' ORDER BY created_at DESC LIMIT 1", (sender,)).fetchone()
                source_message_id = candidate[0] if candidate else None
            if granted and not source_message_id:
                return False
            if granted and source_message_id:
                proof = self.db.execute("SELECT 1 FROM inbox WHERE id=? AND sender=?", (source_message_id, sender)).fetchone()
                if not proof:
                    return False
            self.db.execute(
                "INSERT INTO customer_consents(agreement_id,phone,granted,updated_at,source_message_id) VALUES(?,?,?,?,?) "
                "ON CONFLICT(agreement_id,phone) DO UPDATE SET granted=excluded.granted,updated_at=excluded.updated_at,source_message_id=excluded.source_message_id",
                (identifier, sender, int(granted), time.time(), source_message_id),
            )
            if granted:
                self._resume_future_reminders(event_id=identifier)
            else:
                self.db.execute(
                    "UPDATE reminders SET status=CASE WHEN outbox_id IS NULL THEN 'pending_consent' "
                    "ELSE 'cancelled_consent' END WHERE event_id=? "
                    "AND status IN('pending','pending_consent','blocked_template','queued')",
                    (identifier,),
                )
            self._audit("customer_consent", identifier, {"phone": sender, "granted": granted, "source_message_id": source_message_id})
            return True

    def reminders_enabled(self, sender):
        with self.lock:
            row = self.db.execute("SELECT reminders_enabled FROM contacts WHERE id=?", (sender,)).fetchone()
            return row is not None and row[0] == 1

    def set_reminders_enabled(self, sender, enabled):
        with self.lock, self.db:
            contact(self.db, sender, time.time())
            self.db.execute("UPDATE contacts SET reminders_enabled=? WHERE id=?", (int(enabled), sender))
            if not enabled:
                self.db.execute("UPDATE reminders SET status='stopped' WHERE recipient=? AND status IN('pending','pending_consent','blocked_template','queued')", (sender,))
                self.db.execute("UPDATE customer_consents SET granted=0,updated_at=? WHERE phone=?", (time.time(), sender))
                self.db.execute(
                    "UPDATE reminders SET status=CASE WHEN outbox_id IS NULL THEN 'pending_consent' "
                    "ELSE 'cancelled_consent' END WHERE event_id IN "
                    "(SELECT agreement_id FROM customer_consents WHERE phone=?) "
                    "AND status IN('pending','pending_consent','blocked_template','queued')",
                    (sender,),
                )
            else:
                self.db.execute(
                    "UPDATE reminders SET status='pending_consent' WHERE recipient=? "
                    "AND status='stopped' AND outbox_id IS NULL AND scheduled_at>? "
                    "AND event_id IN (SELECT id FROM calendar_events WHERE status='open')",
                    (sender, time.time()),
                )
                self._resume_future_reminders(recipient=sender)
            self._audit("reminder_preference", sender, {"enabled": enabled})

    def _resume_future_reminders(self, event_id=None, recipient=None):
        self.db.execute(
            "UPDATE reminders SET status='pending' WHERE status IN('pending_consent','stopped') "
            "AND outbox_id IS NULL AND scheduled_at>? "
            "AND (? IS NULL OR event_id=?) AND (? IS NULL OR recipient=?) "
            "AND recipient IN(SELECT id FROM contacts WHERE reminders_enabled=1) "
            "AND event_id IN(SELECT e.id FROM calendar_events e "
            "JOIN agreements a ON a.id=e.agreement_id "
            "JOIN customer_consents c ON c.agreement_id=a.id AND c.phone=a.client_phone "
            "WHERE e.status='open' AND a.paid<a.total AND e.customer_phone=a.client_phone "
            "AND c.granted=1 AND c.source_message_id IS NOT NULL)",
            (time.time(), event_id, event_id, recipient, recipient),
        )

    def settle_agreement(self, sender, reference):
        reference = reference.upper()
        if not reference.startswith("WT-"):
            reference = "WT-" + reference
        with self.lock, self.db:
            rows = self.db.execute("SELECT id FROM agreements WHERE sender=? AND reference=?", (sender, reference)).fetchall()
            if len(rows) != 1:
                return False
            identifier = rows[0][0]
            self.db.execute("UPDATE calendar_events SET status='settled' WHERE agreement_id=?", (identifier,))
            self.db.execute("UPDATE reminders SET status='cancelled' WHERE event_id=? AND status IN('pending','pending_consent','blocked_template','queued','stopped')", (identifier,))
            self._audit("agreement_settled", identifier, {"sender": sender})
            return True

    def is_known_customer(self, sender):
        with self.lock:
            return self.db.execute("SELECT 1 FROM agreements WHERE client_phone=? LIMIT 1", (sender,)).fetchone() is not None

    def archive_message(self, message, status="ignored", reason="not_processed"):
        identifier = message.get("id")
        sender = message.get("from")
        if not identifier or not sender:
            return False
        with self.lock, self.db:
            exists = self.db.execute("SELECT 1 FROM messages WHERE id=?", ("in:" + identifier,)).fetchone()
            if exists:
                return False
            try:
                timestamp = float(message.get("timestamp", time.time()))
            except (TypeError, ValueError):
                timestamp = time.time()
            log_message(self.db, "in:" + identifier, sender, message, "inbound", timestamp, status)
            self._audit("inbound_archived", identifier, {"reason": reason})
            return True
