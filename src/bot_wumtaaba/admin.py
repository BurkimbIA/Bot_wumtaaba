"""Back-office privé, sélection du fournisseur et sessions administrateur éphémères."""

import base64
import hashlib
import hmac
import json
import re
import secrets
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse

from . import admin_data


def install_admin(app, settings):
    router = APIRouter(prefix="/admin")
    sessions = {}
    attempts = deque(maxlen=30)
    cookie = "wuumtaaba_admin"
    ttl = 8 * 3600

    def authorized(request):
        token = request.cookies.get(cookie, "")
        expiry = sessions.get(hashlib.sha256(token.encode()).hexdigest(), 0)
        if not token or expiry < time.time():
            raise HTTPException(401, "Connexion administrateur requise")

    def same_origin(request):
        origin = request.headers.get("origin")
        scheme = "https" if settings.admin_cookie_secure else request.url.scheme
        expected = scheme + "://" + request.headers.get("host", "")
        if origin and origin.rstrip("/") != expected:
            raise HTTPException(403, "Origine refusée")

    @router.get("", response_class=HTMLResponse)
    @router.get("/", response_class=HTMLResponse)
    async def page():
        html = (Path(__file__).parent / "static" / "admin.html").read_text()
        hashes = ["'sha256-" + base64.b64encode(hashlib.sha256(x.encode()).digest()).decode() + "'"
                  for x in re.findall(r"<(?:script|style)>(.*?)</(?:script|style)>", html, re.DOTALL)]
        policy = ("default-src 'none'; script-src " + " ".join(hashes) +
                  "; style-src " + " ".join(hashes) + "; connect-src 'self'; media-src 'self'; "
                  "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; "
                  "base-uri 'none'; form-action 'self'")
        return HTMLResponse(html, headers={"Content-Security-Policy": policy})

    @router.post("/login")
    async def login(request: Request):
        same_origin(request)
        secret = settings.admin_password.get_secret_value()
        if len(secret) < 24:
            raise HTTPException(503, "Accès administrateur non configuré")
        now = time.time()
        while attempts and attempts[0] < now - 300:
            attempts.popleft()
        if len(attempts) >= 10:
            raise HTTPException(429, "Trop de tentatives. Réessaie dans cinq minutes.")
        attempts.append(now)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 4096:
                raise HTTPException(413, "Requête trop volumineuse")
        if "application/json" not in request.headers.get("content-type", ""):
            raise HTTPException(400, "Requête invalide")
        try:
            body = json.loads(raw)
            password = body.get("password") if isinstance(body, dict) else None
        except ValueError:
            password = None
        if not isinstance(password, str) or not hmac.compare_digest(
            hashlib.sha256(password.encode()).digest(), hashlib.sha256(secret.encode()).digest()
        ):
            raise HTTPException(401, "Code d’accès incorrect")
        for key in list(sessions):
            if sessions[key] <= now:
                del sessions[key]
        if len(sessions) >= 20:
            sessions.pop(next(iter(sessions)))
        token = secrets.token_urlsafe(32)
        sessions[hashlib.sha256(token.encode()).hexdigest()] = now + ttl
        response = JSONResponse({"ok": True})
        response.set_cookie(cookie, token, max_age=ttl, httponly=True,
                            secure=settings.admin_cookie_secure, samesite="strict", path="/admin")
        return response

    @router.post("/logout")
    async def logout(request: Request):
        same_origin(request)
        sessions.pop(hashlib.sha256(request.cookies.get(cookie, "").encode()).hexdigest(), None)
        response = JSONResponse({"ok": True})
        response.delete_cookie(cookie, path="/admin", secure=settings.admin_cookie_secure,
                               httponly=True, samesite="strict")
        return response

    def model_bridge(request):
        bridge = getattr(request.app.state, "model_bridge", None)
        if bridge is None:
            raise HTTPException(503, "Sélecteur de modèles indisponible")
        return bridge

    @router.get("/api/models")
    async def models(request: Request):
        authorized(request)
        return model_bridge(request).status()

    @router.post("/api/models")
    async def select_models(request: Request):
        authorized(request)
        same_origin(request)
        if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
            raise HTTPException(400, "Une requête JSON est requise")
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                raise HTTPException(413, "Requête trop volumineuse")
        try:
            body = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Requête JSON invalide") from None
        if (not isinstance(body, dict) or set(body) != {"provider"}
                or not isinstance(body["provider"], str) or body["provider"] not in {"cloud", "mac"}):
            raise HTTPException(400, "Choisis le fournisseur cloud ou mac")
        return model_bridge(request).select(body["provider"])

    @router.get("/api/overview")
    async def overview(request: Request):
        authorized(request)
        return admin_data.overview(request.app.state.store)

    @router.get("/api/agreements")
    async def agreements(request: Request, q: str = "", page: int = 1, sender: str = ""):
        authorized(request)
        return admin_data.agreements(request.app.state.store, q[:100], max(1, page), sender[:30])

    @router.get("/api/agreement-sellers")
    async def agreement_sellers(request: Request, q: str = "", page: int = 1):
        authorized(request)
        return admin_data.agreement_sellers(request.app.state.store, q[:100], max(1, page))

    @router.get("/api/conversations")
    async def conversations(request: Request, q: str = "", page: int = 1):
        authorized(request)
        return admin_data.conversations(request.app.state.store, q[:100], max(1, page))

    @router.get("/api/conversations/{phone}")
    async def conversation(request: Request, phone: str):
        authorized(request)
        result = admin_data.conversation(request.app.state.store, phone)
        if result is None:
            raise HTTPException(404, "Conversation introuvable")
        return result

    @router.get("/api/reminders")
    async def reminders(request: Request, status: str = "", page: int = 1):
        authorized(request)
        return admin_data.reminders(request.app.state.store, status[:50], max(1, page))

    @router.get("/api/media/{message_id:path}")
    async def media(request: Request, message_id: str):
        authorized(request)
        result = admin_data.media_file(request.app.state.store, message_id, settings.media_directory)
        if result is None:
            raise HTTPException(404, "Audio introuvable")
        path, mime = result
        return FileResponse(path, media_type=mime, content_disposition_type="inline")

    @app.middleware("http")
    async def admin_headers(request, call_next):
        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    app.include_router(router)
