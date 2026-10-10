"""Read-only, explicitly projected data for the authenticated back office."""

import math
import re
import time
from pathlib import Path

PAGE_SIZE = 25


def _search(value):
    # Literal substring search, including percent and underscore characters.
    return "%" + str(value or "")[:200].replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_") + "%"


def _page(store, select, count, params, page):
    with store.lock:
        total = store.db.execute(count, params).fetchone()[0]
        pages = max(1, math.ceil(total / PAGE_SIZE))
        try:
            page = max(1, min(int(page), pages))
        except (ValueError, TypeError, OverflowError):
            page = 1
        rows = store.db.execute(select + " LIMIT ? OFFSET ?", (*params, PAGE_SIZE, (page - 1) * PAGE_SIZE))
        return {"items": [dict(row) for row in rows], "page": page, "pages": pages, "total": total}


def overview(store):
    with store.lock:
        counts = dict(store.db.execute("""
            SELECT (SELECT COUNT(*) FROM contacts) contacts,
              (SELECT COUNT(*) FROM agreements) agreements,
              (SELECT COUNT(*) FROM calendar_events WHERE status='open') open_agreements,
              (SELECT COALESCE(SUM(MAX(COALESCE(a.total,0)-COALESCE(a.paid,0),0)),0)
                 FROM agreements a LEFT JOIN calendar_events e ON e.agreement_id=a.id
                 WHERE COALESCE(e.status,'open')='open') balance,
              (SELECT COUNT(*) FROM messages WHERE type='audio') audios,
              (SELECT COUNT(*) FROM reminders WHERE status IN
                ('pending','pending_consent','queued','blocked_template')) pending_reminders,
              (SELECT COUNT(*) FROM messages WHERE status IN ('failed','uncertain')) failed_messages
        """).fetchone())
        activity = [dict(row) for row in store.db.execute(
            "SELECT date(occurred_at,'unixepoch') day,COUNT(*) count FROM messages "
            "WHERE occurred_at>=? GROUP BY day ORDER BY day", (time.time() - 14 * 86400,)
        )]
        recent = [dict(row) for row in store.db.execute(
            "SELECT id,direction,type,status,substr(text,1,300) text,occurred_at,conversation_id "
            "FROM messages ORDER BY occurred_at DESC,id DESC LIMIT 12"
        )]
    return {"counts": counts, "activity": activity, "recent": recent}


def agreements(store, q="", page=1):
    where = " WHERE a.reference LIKE ? ESCAPE '\\' OR a.client_name LIKE ? ESCAPE '\\' OR a.client_phone LIKE ? ESCAPE '\\' OR a.sender LIKE ? ESCAPE '\\'"
    params = (_search(q),) * 4
    return _page(store,
                 "SELECT a.id,a.reference,a.client_name,a.client_phone,a.sender,a.total,a.paid,a.due_date,"
                 "COALESCE(e.status,CASE WHEN a.paid>=a.total THEN 'paid' ELSE 'open' END) status "
                 "FROM agreements a LEFT JOIN calendar_events e ON e.agreement_id=a.id" + where +
                 " ORDER BY a.created_at DESC,a.id DESC",
                 "SELECT COUNT(*) FROM agreements a" + where, params, page)


def conversations(store, q="", page=1):
    where = " WHERE c.id LIKE ? ESCAPE '\\'"
    return _page(store,
                 "SELECT c.id,c.last_inbound_at,(SELECT COUNT(*) FROM messages m WHERE m.conversation_id=c.id) message_count,"
                 "(SELECT substr(m.text,1,300) FROM messages m WHERE m.conversation_id=c.id ORDER BY m.occurred_at DESC,m.id DESC LIMIT 1) last_text "
                 "FROM conversations c" + where + " ORDER BY c.last_inbound_at DESC,c.id",
                 "SELECT COUNT(*) FROM conversations c" + where, (_search(q),), page)


def conversation(store, phone):
    if not isinstance(phone, str) or not re.fullmatch(r"[0-9]{7,15}", phone):
        return None
    with store.lock:
        if not store.db.execute("SELECT 1 FROM conversations WHERE id=?", (phone,)).fetchone():
            return None
        rows = store.db.execute(
            "SELECT m.id,m.direction,m.type,m.text,m.status,m.occurred_at,"
            "a.storage_path,a.transcript,a.mime_type,a.message_id media_id "
            "FROM messages m LEFT JOIN media_assets a ON a.message_id=m.id "
            "WHERE m.conversation_id=? ORDER BY m.occurred_at DESC,m.id DESC LIMIT 201", (phone,)
        ).fetchall()
    messages = []
    for row in reversed(rows[:200]):
        message = dict(row)
        path = message.pop("storage_path")
        transcript = message.pop("transcript")
        mime = message.pop("mime_type")
        media_id = message.pop("media_id")
        if media_id:
            message["media"] = {"available": bool(path), "transcript": transcript, "mime_type": mime}
        messages.append(message)
    return {"phone": phone, "messages": messages, "truncated": len(rows) > 200}


def reminders(store, status="", page=1):
    where = " WHERE r.status=?" if status else ""
    params = (str(status)[:50],) if status else ()
    return _page(store,
                 "SELECT r.id,a.reference,r.recipient,r.role,r.scheduled_at,r.status FROM reminders r "
                 "JOIN calendar_events e ON e.id=r.event_id JOIN agreements a ON a.id=e.agreement_id" +
                 where + " ORDER BY r.scheduled_at DESC,r.id DESC",
                 "SELECT COUNT(*) FROM reminders r" + where, params, page)


def media_file(store, message_id, media_directory):
    if not isinstance(message_id, str) or len(message_id) > 512:
        return None
    with store.lock:
        row = store.db.execute("SELECT storage_path,mime_type FROM media_assets WHERE message_id=?", (message_id,)).fetchone()
    if not row or not row["storage_path"]:
        return None
    try:
        base = Path(media_directory).resolve(strict=True)
        file = (base / row["storage_path"]).resolve(strict=True)
        if not file.is_relative_to(base) or not file.is_file():
            return None
    except (OSError, RuntimeError, ValueError):
        return None
    # A corrupted MIME value must not turn a private media download into executable HTML.
    mime = row["mime_type"] or ""
    allowed = {"audio/ogg", "audio/mpeg", "audio/mp4", "audio/wav", "audio/x-wav", "audio/aac", "audio/amr", "audio/webm"}
    return file, mime if mime in allowed else "application/octet-stream"
