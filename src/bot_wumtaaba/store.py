"""SQLite : état conversationnel et files durables, commit métier atomique."""

import json
import sqlite3
import threading
import time
from pathlib import Path


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

    def enqueue(self, message_id, sender, message):
        now = time.time()
        with self.lock, self.db:
            cursor = self.db.execute(
                "INSERT OR IGNORE INTO inbox(id,sender,message,created_at,available_at) "
                "VALUES(?,?,?,?,?)",
                (message_id, sender, json.dumps(message), now, now),
            )
            return cursor.rowcount == 1

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
                    "INSERT INTO agreements VALUES(?,?,?,?)",
                    (agreement["id"], sender, json.dumps(agreement), now),
                )
            # Les sorties héritent de la date du message entrant, pour la fenêtre Meta.
            received_at = min(now, int(json.loads(row["message"]).get("timestamp", now)))
            self.db.executemany(
                "INSERT INTO outbox(recipient,content,created_at,available_at) VALUES(?,?,?,?)",
                [(sender, json.dumps(reply), received_at, now) for reply in replies],
            )
            self.db.execute("UPDATE inbox SET status='done',error=NULL WHERE id=?", (message_id,))

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

    def fail_inbox(self, message_id, error):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE inbox SET status='failed',error=? WHERE id=?",
                (str(error)[:200], message_id),
            )

    def sent_outbox(self, message_id, remote_id):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status='sent',remote_id=?,error=NULL WHERE id=?",
                (remote_id, message_id),
            )

    def fail_outbox(self, message_id, error, status="failed"):
        if status not in {"failed", "uncertain"}:
            raise ValueError("Statut de sortie invalide")
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=?,error=? WHERE id=?",
                (status, str(error)[:200], message_id),
            )

    def retry_outbox(self, message_id, error, delay=60):
        """Uniquement après refus explicite HTTP 429, jamais après un timeout."""
        with self.lock, self.db:
            self.db.execute(
                "UPDATE outbox SET status=CASE WHEN attempts>=3 THEN 'failed' "
                "ELSE 'pending' END,available_at=?,error=? WHERE id=?",
                (time.time() + delay, str(error)[:200], message_id),
            )

    def recover(self):
        with self.lock, self.db:
            self.db.execute(
                "UPDATE inbox SET status=CASE WHEN attempts>=3 THEN 'failed' "
                "ELSE 'pending' END WHERE status='processing'"
            )
            self.db.execute("UPDATE outbox SET status='uncertain' WHERE status='sending'")

    def purge(self, retention_days=30):
        cutoff = time.time() - retention_days * 86400
        with self.lock, self.db:
            self.db.execute("DELETE FROM sessions WHERE updated_at<?", (cutoff,))
            self.db.execute("DELETE FROM agreements WHERE created_at<?", (cutoff,))
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
