"""Postgres : état conversationnel et files durables, commit métier atomique."""

import json
import re
import threading
import time
from importlib.resources import files

import psycopg
from psycopg import sql

from .migrations import contact, log_message, normalize_agreement
from .mobile import apply_mobile_operation

SCHEMA = files(__package__).joinpath("schema.sql").read_text()


class Row(dict):
    """Keeps the sqlite3.Row surface the code was written against: row["name"] and row[0]."""

    def __getitem__(self, key):
        return list(self.values())[key] if isinstance(key, int) else super().__getitem__(key)


def _rows(cursor):
    names = [column.name for column in cursor.description or ()]
    return lambda values: Row(zip(names, values))


class Database:
    """One Postgres connection, sqlite3-like: execute(), and `with db:` is one transaction."""

    def __init__(self, url, schema):
        if not re.fullmatch(r"[a-z_][a-z0-9_]{0,62}", schema):
            raise ValueError("Nom de schéma invalide")
        self.url, self.schema = url, schema
        self.connection = None
        self._transactions = []

    def _connect(self):
        # A dropped connection (idle timeout, Supabase restart) is reopened on next use.
        if self.connection is None or self.connection.closed or self.connection.broken:
            # prepare_threshold=None keeps the code usable behind a transaction pooler.
            self.connection = psycopg.connect(
                self.url, autocommit=True, row_factory=_rows, prepare_threshold=None, connect_timeout=10
            )
            # Supabase may default to 0, which rounds float timestamps on text reads.
            # Preserve round-trip precision for durable state and snapshot verification.
            self.connection.execute("SET extra_float_digits=3")
            name = sql.Identifier(self.schema)
            self.connection.execute(sql.SQL("CREATE SCHEMA IF NOT EXISTS {}").format(name))
            self.connection.execute(sql.SQL("SET search_path TO {}").format(name))
        return self.connection

    def execute(self, query, params=None):
        return self._connect().execute(query, params)

    def __enter__(self):
        transaction = self._connect().transaction()
        transaction.__enter__()
        self._transactions.append(transaction)
        return self

    def __exit__(self, *exc):
        return self._transactions.pop().__exit__(*exc)

    def close(self):
        if self.connection is not None:
            self.connection.close()


class Store:
    def __init__(self, url, schema="wuumtaaba"):
        self.lock = threading.RLock()
        self.db = Database(url, schema)
        with self.lock, self.db:
            self.db.execute(SCHEMA)

    def enqueue(self, message_id, sender, message):
        now = time.time()
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT INTO inbox(id,sender,message,created_at,available_at) "
                "VALUES(%s,%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (message_id, sender, json.dumps(message), now, now),
            )
            inserted = cursor.rowcount == 1
            if inserted:
                log_message(self.db, "in:" + message_id, sender, message, "inbound",
                            float(message.get("timestamp", now)), "pending")
            return inserted

    def has_pending_work(self):
        """Queued replies or messages: the bot must not stop before they are handled."""
        with self.lock:
            return self.db.execute(
                "SELECT EXISTS(SELECT 1 FROM inbox WHERE status IN ('pending','processing')) "
                "OR EXISTS(SELECT 1 FROM outbox WHERE status IN ('pending','sending'))"
            ).fetchone()[0]

    def count_recent(self, sender, seconds=3600):
        with self.lock:
            return self.db.execute(
                "SELECT COUNT(*) FROM inbox WHERE sender=%s AND created_at>%s",
                (sender, time.time() - seconds),
            ).fetchone()[0]

    def get_session(self, sender):
        with self.lock:
            row = self.db.execute("SELECT state FROM sessions WHERE sender=%s", (sender,)).fetchone()
            return json.loads(row[0]) if row else {}

    def list_agreements(self, sender, offset=0, limit=5):
        with self.lock:
            rows = self.db.execute(
                "SELECT id,data,created_at FROM agreements WHERE sender=%s "
                "ORDER BY created_at DESC,id DESC LIMIT %s OFFSET %s",
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
            order = "seq" if table == "inbox" else "id"
            # Serialises claims across processes, as BEGIN IMMEDIATE did on SQLite.
            self.db.execute("SELECT pg_advisory_xact_lock(hashtext('wuumtaaba:queue'))")
            row = self.db.execute(
                f"SELECT job.* FROM {table} job WHERE job.status='pending' AND job.available_at<=%s "
                f"AND NOT EXISTS (SELECT 1 FROM {table} earlier WHERE earlier.{owner}=job.{owner} "
                f"AND earlier.status IN {blockers} AND earlier.{order}<job.{order}) "
                f"ORDER BY job.created_at,job.{order} LIMIT 1",
                (time.time(),),
            ).fetchone()
            if row is None:
                return None
            self.db.execute(
                f"UPDATE {table} SET status=%s,attempts=attempts+1 WHERE id=%s",
                (processing, row["id"]),
            )
            self.db.execute("UPDATE messages SET status=%s WHERE id=%s",
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
                "SELECT status,message FROM inbox WHERE id=%s AND sender=%s", (message_id, sender)
            ).fetchone()
            if not row or row[0] != "processing":
                raise ValueError("Inbox non réservée ou déjà terminée")
            self.db.execute(
                "INSERT INTO sessions VALUES(%s,%s,%s) ON CONFLICT(sender) "
                "DO UPDATE SET state=excluded.state,updated_at=excluded.updated_at",
                (sender, json.dumps(state), now),
            )
            if agreement and agreement.get("_operation") in {"mobile_pair", "mobile_revoke"}:
                apply_mobile_operation(self.db, sender, agreement)
            elif agreement and agreement.get("_operation") == "payment":
                self._apply_payment(sender, agreement, message_id)
            elif agreement:
                self.db.execute(
                    "INSERT INTO agreements(id,sender,data,created_at) VALUES(%s,%s,%s,%s)",
                    (agreement["id"], sender, json.dumps(agreement), now),
                )
                normalize_agreement(self.db, agreement["id"], sender, agreement, now, now)
                if agreement.get("paid", 0) > 0:
                    self.db.execute("INSERT INTO payments VALUES(%s,%s,%s,%s,%s,%s,%s)", (
                        "advance:" + agreement["id"], agreement["id"], agreement["paid"],
                        "initial", sender, None, now))
                self._audit("agreement_created", agreement["id"], {"sender": sender})
            # Les sorties héritent de la date du message entrant, pour la fenêtre Meta.
            received_at = min(now, int(json.loads(row["message"]).get("timestamp", now)))
            for reply in replies:
                self.queue_outbox(sender, reply, message_id, received_at)
            self.db.execute("UPDATE inbox SET status='done',error=NULL WHERE id=%s", (message_id,))
            self.db.execute("UPDATE messages SET status='done' WHERE id=%s", ("in:" + message_id,))

    def retry_inbox(self, message_id, error):
        with self.lock, self.db:
            row = self.db.execute("SELECT attempts FROM inbox WHERE id=%s", (message_id,)).fetchone()
            if row:
                self.db.execute(
                    "UPDATE inbox SET status=%s,available_at=%s,error=%s WHERE id=%s",
                    (
                        "failed" if row[0] >= 3 else "pending",
                        time.time() + 2 ** row[0],
                        str(error)[:200],
                        message_id,
                    ),
                )

                self.db.execute("UPDATE messages SET status=(SELECT status FROM inbox WHERE id=%s) WHERE id=%s", (message_id, "in:" + message_id))
                self._audit("inbox_retry", message_id, {"error": str(error)[:200]})

    def fail_inbox(self, message_id, error):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE inbox SET status='failed',error=%s WHERE id=%s",
                (str(error)[:200], message_id),
            )

            self.db.execute("UPDATE messages SET status='failed' WHERE id=%s", ("in:" + message_id,))
            self._audit("inbox_failed", message_id, {"error": str(error)[:200]})

    def sent_outbox(self, message_id, remote_id):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status='sent',remote_id=%s,error=NULL WHERE id=%s",
                (remote_id, message_id),
            )

            self._sync_outbox(message_id)

    def fail_outbox(self, message_id, error, status="failed"):
        if status not in {"failed", "uncertain"}:
            raise ValueError("Statut de sortie invalide")
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=%s,error=%s WHERE id=%s",
                (status, str(error)[:200], message_id),
            )

            self._sync_outbox(message_id)

    def retry_outbox(self, message_id, error, delay=60):
        """Uniquement après refus explicite HTTP 429, jamais après un timeout."""
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=CASE WHEN attempts>=3 THEN 'failed' "
                "ELSE 'pending' END,available_at=%s,error=%s WHERE id=%s",
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
            self.db.execute("DELETE FROM sessions WHERE updated_at<%s", (cutoff,))
            # Agreements, audit and calendar are durable business records.
            self.db.execute(
                "DELETE FROM inbox WHERE created_at<%s AND status IN ('done','failed')", (cutoff,)
            )
            self.db.execute(
                "DELETE FROM outbox WHERE created_at<%s AND status IN ('sent','failed','uncertain')",
                (cutoff,),
            )

    def close(self):
        with self.lock:
            self.db.close()

    def _audit(self, kind, entity_id, details):
        self.db.execute(
            "INSERT INTO audit_events(kind,entity_id,details,created_at) VALUES(%s,%s,%s,%s)",
            (kind, str(entity_id) if entity_id is not None else None, json.dumps(details), time.time()),
        )

    def audit(self, kind, entity_id=None, details=None):
        with self.lock, self.db:
            self._audit(kind, entity_id, details or {})

    def queue_outbox(self, recipient, content, source_message_id=None, created_at=None):
        """Caller owns lock/transaction: used atomically by inbox and reminder workers."""
        now = time.time()
        content = dict(content)
        local_audio = content.pop("_local_audio", None)
        cursor = self.db.execute(
            "INSERT INTO outbox(recipient,content,created_at,available_at) VALUES(%s,%s,%s,%s) RETURNING id",
            (recipient, json.dumps(content), now if created_at is None else created_at, now),
        )
        identifier = cursor.fetchone()[0]
        log_message(self.db, "out:" + str(identifier), recipient, content, "outbound", now,
                    "pending", source=source_message_id, outbox_id=identifier)
        if local_audio and content.get("type") == "audio":
            self.db.execute(
                "UPDATE media_assets SET mime_type=%s,sha256=%s,storage_path=%s,metadata=%s,updated_at=%s "
                "WHERE message_id=%s",
                (local_audio.get("mime_type"), local_audio.get("sha256"),
                 local_audio.get("storage_path"), json.dumps(local_audio), now,
                 "out:" + str(identifier)),
            )
        return identifier

    def _sync_outbox(self, identifier):
        row = self.db.execute("SELECT status,remote_id,error FROM outbox WHERE id=%s", (identifier,)).fetchone()
        if row:
            self.db.execute("UPDATE messages SET status=%s,remote_id=%s WHERE outbox_id=%s", (row[0], row[1], identifier))
            if row[1]:
                self._apply_delivery_status(row[1])
            if row[0] in {"sent", "uncertain", "failed"}:
                self.db.execute("UPDATE reminders SET status=%s WHERE outbox_id=%s AND status='queued'", (row[0], identifier))
            self._audit("outbox_status", identifier, dict(row))

    def record_media(self, message_id, metadata=None, transcript=None):
        identifier = message_id if message_id.startswith("in:") else "in:" + message_id
        metadata = metadata or {}
        with self.lock, self.db:
            row = self.db.execute("SELECT * FROM media_assets WHERE message_id=%s", (identifier,)).fetchone()
            if not row:
                return False
            merged = {**json.loads(row["metadata"]), **metadata}
            self.db.execute(
                "UPDATE media_assets SET media_id=%s,mime_type=%s,sha256=%s,storage_path=%s,transcript=%s,metadata=%s,updated_at=%s WHERE message_id=%s",
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
                "INSERT INTO delivery_events(remote_id,status,occurred_at,payload) VALUES(%s,%s,%s,%s) ON CONFLICT DO NOTHING",
                (remote_id, state, timestamp, json.dumps(status)),
            )
            if cursor.rowcount:
                self._apply_delivery_status(remote_id)
            return cursor.rowcount == 1

    def _apply_delivery_status(self, remote_id):
        latest = self.db.execute(
            "SELECT status FROM delivery_events WHERE remote_id=%s ORDER BY occurred_at DESC,"
            "CASE status WHEN 'read' THEN 3 WHEN 'delivered' THEN 2 ELSE 1 END DESC LIMIT 1",
            (remote_id,),
        ).fetchone()
        if latest:
            self.db.execute("UPDATE messages SET status=%s WHERE remote_id=%s", (latest[0], remote_id))

    def calendar_for(self, sender, start=None, end=None):
        with self.lock:
            rows = self.db.execute(
                "SELECT e.*,a.reference,a.client_name AS client,a.product,a.total,a.paid,a.due_date "
                "FROM calendar_events e JOIN agreements a ON a.id=e.agreement_id "
                "WHERE e.owner_sender=%s AND (%s::float8 IS NULL OR e.starts_at>=%s) "
                "AND (%s::float8 IS NULL OR e.starts_at<=%s) ORDER BY e.starts_at,e.id",
                (sender, start, start, end, end),
            ).fetchall()
            return [dict(row) for row in rows]

    def set_customer_consent(self, sender, agreement_reference, granted, source_message_id=None):
        reference = agreement_reference.upper()
        if not reference.startswith("WT-"):
            reference = "WT-" + reference
        with self.lock, self.db:
            rows = self.db.execute("SELECT id FROM agreements WHERE client_phone=%s AND reference=%s", (sender, reference)).fetchall()
            if len(rows) != 1:
                return False
            identifier = rows[0][0]
            # A consent without a verifiable inbound is never usable by the scheduler.
            if source_message_id is None:
                candidate = self.db.execute("SELECT id FROM inbox WHERE sender=%s AND status='processing' ORDER BY created_at DESC LIMIT 1", (sender,)).fetchone()
                source_message_id = candidate[0] if candidate else None
            if granted and not source_message_id:
                return False
            if granted and source_message_id:
                proof = self.db.execute("SELECT 1 FROM inbox WHERE id=%s AND sender=%s", (source_message_id, sender)).fetchone()
                if not proof:
                    return False
            self.db.execute(
                "INSERT INTO customer_consents(agreement_id,phone,granted,updated_at,source_message_id) VALUES(%s,%s,%s,%s,%s) "
                "ON CONFLICT(agreement_id,phone) DO UPDATE SET granted=excluded.granted,updated_at=excluded.updated_at,source_message_id=excluded.source_message_id",
                (identifier, sender, int(granted), time.time(), source_message_id),
            )
            if granted:
                self._resume_future_reminders(event_id=identifier)
            else:
                self.db.execute(
                    "UPDATE reminders SET status=CASE WHEN outbox_id IS NULL THEN 'pending_consent' "
                    "ELSE 'cancelled_consent' END WHERE event_id=%s "
                    "AND status IN('pending','pending_consent','blocked_template','queued')",
                    (identifier,),
                )
            self._audit("customer_consent", identifier, {"phone": sender, "granted": granted, "source_message_id": source_message_id})
            return True

    def reminders_enabled(self, sender):
        with self.lock:
            row = self.db.execute("SELECT reminders_enabled FROM contacts WHERE id=%s", (sender,)).fetchone()
            return row is not None and row[0] == 1

    def set_reminders_enabled(self, sender, enabled):
        with self.lock, self.db:
            contact(self.db, sender, time.time())
            self.db.execute("UPDATE contacts SET reminders_enabled=%s WHERE id=%s", (int(enabled), sender))
            if not enabled:
                self.db.execute("UPDATE reminders SET status='stopped' WHERE recipient=%s AND status IN('pending','pending_consent','blocked_template','queued')", (sender,))
                self.db.execute("UPDATE customer_consents SET granted=0,updated_at=%s WHERE phone=%s", (time.time(), sender))
                self.db.execute(
                    "UPDATE reminders SET status=CASE WHEN outbox_id IS NULL THEN 'pending_consent' "
                    "ELSE 'cancelled_consent' END WHERE event_id IN "
                    "(SELECT agreement_id FROM customer_consents WHERE phone=%s) "
                    "AND status IN('pending','pending_consent','blocked_template','queued')",
                    (sender,),
                )
            else:
                self.db.execute(
                    "UPDATE reminders SET status='pending_consent' WHERE recipient=%s "
                    "AND status='stopped' AND outbox_id IS NULL AND scheduled_at>%s "
                    "AND event_id IN (SELECT id FROM calendar_events WHERE status='open')",
                    (sender, time.time()),
                )
                self._resume_future_reminders(recipient=sender)
            self._audit("reminder_preference", sender, {"enabled": enabled})

    def _resume_future_reminders(self, event_id=None, recipient=None):
        self.db.execute(
            "UPDATE reminders SET status='pending' WHERE status IN('pending_consent','stopped') "
            "AND outbox_id IS NULL AND scheduled_at>%s "
            "AND (%s::text IS NULL OR event_id=%s) AND (%s::text IS NULL OR recipient=%s) "
            "AND recipient IN(SELECT id FROM contacts WHERE reminders_enabled=1) "
            "AND event_id IN(SELECT e.id FROM calendar_events e "
            "JOIN agreements a ON a.id=e.agreement_id "
            "JOIN customer_consents c ON c.agreement_id=a.id AND c.phone=a.client_phone "
            "WHERE e.status='open' AND a.paid<a.total AND e.customer_phone=a.client_phone "
            "AND c.granted=1 AND c.source_message_id IS NOT NULL)",
            (time.time(), event_id, event_id, recipient, recipient),
        )

    def get_agreement(self, sender, reference):
        with self.lock:
            rows = self.db.execute(
                "SELECT a.*,COALESCE(e.status,'open') AS status FROM agreements a "
                "LEFT JOIN calendar_events e ON e.agreement_id=a.id "
                "WHERE a.sender=%s AND (a.reference=%s OR a.id=%s)",
                (sender, reference.upper(), reference),
            ).fetchall()
            if len(rows) != 1:
                return None
            row = rows[0]
            return {**json.loads(row["data"]), "id": row["id"], "reference": row["reference"],
                    "paid": row["paid"], "total": row["total"],
                    "balance": max(0, row["total"] - row["paid"]), "status": row["status"],
                    "created_at": row["created_at"]}

    def payment_history(self, sender, reference, offset=0, limit=20):
        with self.lock:
            agreement = self.get_agreement(sender, reference)
            if not agreement:
                return []
            return [dict(row) for row in self.db.execute(
                "SELECT id,amount,kind,actor,created_at FROM payments WHERE agreement_id=%s "
                "ORDER BY created_at,id LIMIT %s OFFSET %s", (agreement["id"], limit, offset))]

    def _apply_payment(self, sender, operation, source_message_id=None):
        """Caller owns the transaction: ledger + balance + inbox + replies commit together."""
        self.db.execute("SELECT pg_advisory_xact_lock(hashtext('wuumtaaba:payments'))")
        existing = self.db.execute("SELECT * FROM payments WHERE id=%s", (operation["id"],)).fetchone()
        if existing:
            if (existing["actor"] != sender or existing["agreement_id"] != operation["agreement_id"]
                    or existing["amount"] != operation["amount"]):
                raise ValueError("Identifiant de paiement déjà utilisé")
            return
        row = self.get_agreement(sender, operation["agreement_id"])
        amount = operation["amount"]
        if (row is None or row["status"] not in {"open", "paid", "settled"}
                or type(amount) is not int or amount <= 0 or amount > row["balance"]):
            raise ValueError("Paiement invalide ou supérieur au solde")
        if row["paid"] != operation["expected_paid"]:
            raise ValueError("Solde modifié : préparer un nouveau paiement")
        self.db.execute("INSERT INTO payments VALUES(%s,%s,%s,%s,%s,%s,%s)", (
            operation["id"], row["id"], amount, operation.get("kind", "payment"),
            sender, source_message_id, time.time()))
        paid = row["paid"] + amount
        data = json.loads(self.db.execute("SELECT data FROM agreements WHERE id=%s", (row["id"],)).fetchone()[0])
        data["paid"] = paid
        self.db.execute("UPDATE agreements SET paid=%s,data=%s WHERE id=%s", (paid, json.dumps(data), row["id"]))
        if paid == row["total"]:
            self.db.execute("UPDATE calendar_events SET status='settled' WHERE agreement_id=%s", (row["id"],))
            self.db.execute("UPDATE reminders SET status='cancelled' WHERE event_id=%s AND status IN('pending','pending_consent','blocked_template','queued','stopped')", (row["id"],))
        # Refresh unsent reminder content in the same transaction as the balance.
        for reminder in self.db.execute(
            "SELECT r.id,r.outbox_id,o.content FROM reminders r JOIN outbox o ON o.id=r.outbox_id "
            "WHERE r.event_id=%s AND r.status='queued' AND o.status='pending'", (row["id"],)).fetchall():
            payload = json.loads(reminder["content"])
            balance = row["total"] - paid
            if payload.get("type") == "text":
                payload["text"]["body"] = (f"Rappel pour l’accord {row['reference']} : solde {balance} F CFA, "
                    f"échéance {row['due_date']}. Envoyez STOP pour arrêter les rappels.")
            elif payload.get("type") == "template":
                payload["template"]["components"][0]["parameters"][1]["text"] = str(balance)
            self.db.execute("UPDATE outbox SET content=%s WHERE id=%s", (json.dumps(payload), reminder["outbox_id"]))
            self.db.execute("UPDATE reminders SET payload=%s WHERE id=%s", (json.dumps(payload), reminder["id"]))
            self.db.execute("UPDATE messages SET payload=%s,text=%s WHERE outbox_id=%s",
                            (json.dumps(payload), payload.get("text", {}).get("body"), reminder["outbox_id"]))
        self._audit("payment_recorded", row["id"], {"payment_id": operation["id"], "amount": amount, "paid": paid, "sender": sender})

    def settle_agreement(self, sender, reference):
        from uuid import uuid4
        with self.lock, self.db:
            row = self.get_agreement(sender, reference if reference.upper().startswith("WT-") else "WT-" + reference.upper())
            if row is None:
                return False
            if row["balance"]:
                self._apply_payment(sender, {"id": uuid4().hex, "agreement_id": row["id"],
                    "amount": row["balance"], "expected_paid": row["paid"], "kind": "settlement"})
            return True

    def is_known_customer(self, sender):
        with self.lock:
            return self.db.execute("SELECT 1 FROM agreements WHERE client_phone=%s LIMIT 1", (sender,)).fetchone() is not None

    def archive_message(self, message, status="ignored", reason="not_processed"):
        identifier = message.get("id")
        sender = message.get("from")
        if not identifier or not sender:
            return False
        with self.lock, self.db:
            exists = self.db.execute("SELECT 1 FROM messages WHERE id=%s", ("in:" + identifier,)).fetchone()
            if exists:
                return False
            try:
                timestamp = float(message.get("timestamp", time.time()))
            except (TypeError, ValueError):
                timestamp = time.time()
            log_message(self.db, "in:" + identifier, sender, message, "inbound", timestamp, status)
            self._audit("inbound_archived", identifier, {"reason": reason})
            return True
