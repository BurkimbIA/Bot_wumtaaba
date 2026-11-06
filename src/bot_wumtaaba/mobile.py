"""Owner-scoped read API. Pairing is proven by an inbound WhatsApp message."""

import hashlib
import re
import secrets
import time
from collections import deque

from fastapi import APIRouter, HTTPException, Query, Request, Response

CLIENT_KEY = "CASE WHEN COALESCE(a.client_phone,'')<>'' THEN 'phone:'||a.client_phone ELSE 'agreement:'||a.id END"


def digest(value):
    return hashlib.sha256(value.encode()).hexdigest()


def pairing_operation(sender):
    code = secrets.token_hex(6).upper()
    return {
        "_operation": "mobile_pair",
        "hash": digest(code),
        "sender": sender,
        "expires_at": time.time() + 300,
    }, code


def apply_mobile_operation(db, sender, operation):
    if operation["_operation"] == "mobile_pair":
        db.execute(
            "DELETE FROM mobile_codes WHERE sender=%s OR expires_at<=%s", (sender, time.time())
        )
        db.execute(
            "INSERT INTO mobile_codes VALUES(%s,%s,%s)",
            (operation["hash"], sender, operation["expires_at"]),
        )
    else:
        db.execute("DELETE FROM mobile_codes WHERE sender=%s", (sender,))
        db.execute("DELETE FROM mobile_tokens WHERE sender=%s", (sender,))


def install_mobile(app):
    router = APIRouter(prefix="/mobile/v1")
    # Global bound prevents rotating source addresses from bypassing the code-exchange budget.
    attempts = deque(maxlen=60)

    def owner(request):
        value = request.headers.get("authorization", "")
        if not value.startswith("Bearer ") or len(value) > 200:
            raise HTTPException(401, "Reconnecte ton application depuis WhatsApp.")
        store = request.app.state.store
        with store.lock:
            row = store.db.execute(
                "SELECT sender FROM mobile_tokens WHERE hash=%s AND expires_at>%s",
                (digest(value[7:]), time.time()),
            ).fetchone()
        if not row:
            raise HTTPException(401, "Session expirée. Demande un nouveau code dans WhatsApp.")
        return row[0]

    @router.post("/session")
    async def exchange(request: Request):
        now = time.time()
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if len(attempts) >= 30:
            raise HTTPException(429, "Trop de tentatives. Réessaie dans cinq minutes.")
        attempts.append(now)
        raw = bytearray()
        async for part in request.stream():
            raw.extend(part)
            if len(raw) > 256:
                raise HTTPException(413, "Requête trop volumineuse")
        import json

        try:
            body = json.loads(raw)
            code = body["code"].strip().upper()
            if not re.fullmatch("[A-F0-9]{12}", code):
                raise ValueError()
        except (ValueError, TypeError, KeyError, AttributeError):
            raise HTTPException(401, "Code invalide ou expiré.") from None
        store = request.app.state.store
        token = secrets.token_urlsafe(32)
        with store.lock, store.db:
            row = store.db.execute(
                "SELECT sender FROM mobile_codes WHERE hash=%s AND expires_at>%s FOR UPDATE", (digest(code), now)
            ).fetchone()
            if not row:
                raise HTTPException(401, "Code invalide ou expiré.")
            store.db.execute("DELETE FROM mobile_codes WHERE hash=%s", (digest(code),))
            store.db.execute("DELETE FROM mobile_tokens WHERE expires_at<=%s", (now,))
            # Keep at most five devices per merchant.
            old = store.db.execute(
                "SELECT hash FROM mobile_tokens WHERE sender=%s ORDER BY expires_at DESC OFFSET 4",
                (row[0],),
            ).fetchall()
            for expired in old:
                store.db.execute("DELETE FROM mobile_tokens WHERE hash=%s", (expired[0],))
            store.db.execute(
                "INSERT INTO mobile_tokens VALUES(%s,%s,%s)", (digest(token), row[0], now + 30 * 86400)
            )
        return {"token": token, "sender": row[0], "expires_at": now + 30 * 86400}

    @router.delete("/session", status_code=204)
    async def logout(request: Request):
        owner(request)
        store = request.app.state.store
        with store.lock, store.db:
            store.db.execute(
                "DELETE FROM mobile_tokens WHERE hash=%s",
                (digest(request.headers["authorization"][7:]),),
            )
        return Response(status_code=204)

    @router.get("/summary")
    async def summary(request: Request):
        sender = owner(request)
        store = request.app.state.store
        with store.lock:
            row = store.db.execute(
                f"SELECT COUNT(*) AS agreements,COUNT(DISTINCT {CLIENT_KEY}) AS clients,"
                "COALESCE(SUM(total),0) AS total,COALESCE(SUM(paid),0) AS paid,"
                "COALESCE(SUM(GREATEST(total-paid,0)),0) AS balance,"
                "COALESCE(SUM(CASE WHEN total>paid AND due_date<CURRENT_DATE::text THEN 1 ELSE 0 END),0) AS overdue "
                "FROM agreements a WHERE sender=%s",
                (sender,),
            ).fetchone()
        return {**dict(row), "sender": sender, "updated_at": time.time()}

    @router.get("/clients")
    async def clients(
        request: Request, q: str = Query("", max_length=100), page: int = Query(1, ge=1)
    ):
        sender = owner(request)
        store = request.app.state.store
        with store.lock:
            rows = store.db.execute(
                f"SELECT {CLIENT_KEY} AS id,MAX(a.client_name) AS name,"
                "MAX(a.client_phone) AS phone,COUNT(*) AS agreements,SUM(a.total) AS total,"
                "SUM(a.paid) AS paid,SUM(GREATEST(a.total-a.paid,0)) AS balance "
                "FROM agreements a WHERE sender=%s GROUP BY 1 "
                "HAVING MAX(CASE WHEN strpos(search_fold(COALESCE(client_name,'')),search_fold(%s))>0 "
                "OR strpos(COALESCE(client_phone,''),%s)>0 THEN 1 ELSE 0 END)=1 "
                "ORDER BY balance DESC,id LIMIT 26 OFFSET %s",
                (sender, q, q, (page - 1) * 25),
            ).fetchall()
        return {"items": [dict(row) for row in rows[:25]], "has_more": len(rows) > 25, "page": page}

    @router.get("/agreements")
    async def agreements(
        request: Request,
        q: str = Query("", max_length=100),
        status: str = Query("all", pattern="^(all|open|settled|overdue)$"),
        client: str = Query("", max_length=150),
        page: int = Query(1, ge=1),
    ):
        sender = owner(request)
        store = request.app.state.store
        with store.lock:
            rows = store.db.execute(
                f"SELECT a.id,a.reference,a.client_name AS client,a.client_phone,"
                "a.product,a.total,a.paid,GREATEST(a.total-a.paid,0) AS balance,a.due_date,a.created_at,"
                f"{CLIENT_KEY} AS client_id FROM agreements a WHERE a.sender=%s "
                "AND (strpos(search_fold(COALESCE(a.client_name,'')||' '||COALESCE(a.product,'')||' '||COALESCE(a.reference,'')),search_fold(%s))>0) "
                f"AND (%s='' OR {CLIENT_KEY}=%s) "
                "AND (%s='all' OR (%s='open' AND a.total>a.paid) OR (%s='settled' AND a.total=a.paid) "
                "OR (%s='overdue' AND a.total>a.paid AND a.due_date<CURRENT_DATE::text)) "
                "ORDER BY a.created_at DESC,a.id LIMIT 26 OFFSET %s",
                (sender, q, client, client, status, status, status, status, (page - 1) * 25),
            ).fetchall()
        return {"items": [dict(row) for row in rows[:25]], "has_more": len(rows) > 25, "page": page}

    @router.get("/agreements/{identifier}")
    async def agreement(request: Request, identifier: str, page: int = Query(1, ge=1)):
        sender = owner(request)
        store = request.app.state.store
        with store.lock:
            row = store.get_agreement(sender, identifier)
            if not row:
                raise HTTPException(404, "Accord introuvable")
            history = store.payment_history(sender, identifier, offset=(page - 1) * 25, limit=26)
        return {**row, "payments": history[:25], "has_more": len(history) > 25, "page": page}

    @app.middleware("http")
    async def mobile_privacy(request, call_next):
        response = await call_next(request)
        if request.url.path.startswith("/mobile/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["Pragma"] = "no-cache"
        return response

    app.include_router(router)
