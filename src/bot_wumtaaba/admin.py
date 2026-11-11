"""Back-office privé, sélection du fournisseur et sessions administrateur persistantes."""

import base64
import hashlib
import hmac
import json
import os
import re
import secrets
import tempfile
import time
from collections import deque
from pathlib import Path

from fastapi import APIRouter, HTTPException, Request
from fastapi.responses import FileResponse, HTMLResponse, JSONResponse, RedirectResponse
from starlette.background import BackgroundTask

from . import admin_data
from .admin_annotations import install_admin_annotations
from .experiments import install_experiments
from .check_inference import install_check_inference


def install_admin(app, settings):
    router = APIRouter(prefix="/admin")
    attempts = deque(maxlen=30)
    cookie = "wuumtaaba_admin"
    ttl = 30 * 24 * 3600
    credential_hash = hashlib.sha256(settings.admin_password.get_secret_value().encode()).hexdigest()

    def authorized(request):
        token = request.cookies.get(cookie, "")
        if not token or not request.app.state.store.admin_session_valid(
            hashlib.sha256(token.encode()).hexdigest(), credential_hash, time.time()
        ):
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
        policy = ("default-src 'none'; script-src 'self' " + " ".join(hashes) +
                  "; style-src 'self' " + " ".join(hashes) + "; connect-src 'self'; media-src 'self'; "
                  "img-src 'self' data:; font-src 'self'; frame-ancestors 'none'; "
                  "base-uri 'none'; form-action 'self'")
        return HTMLResponse(html, headers={"Content-Security-Policy": policy})

    @router.get("/assets/{asset}")
    async def shared_asset(asset: str):
        types = {"admin-shell.js": "text/javascript", "admin-shell.css": "text/css"}
        if asset not in types:
            raise HTTPException(404, "Fichier introuvable")
        return FileResponse(Path(__file__).parent / "static" / asset, media_type=types[asset])

    @router.get("/api/environment")
    async def environment(request: Request):
        authorized(request)
        return {"environment": getattr(request.app.state, "environment", "production")}

    @router.get("/check")
    async def check_page(request: Request):
        try:
            authorized(request)
        except HTTPException:
            return RedirectResponse("/admin?next=check", status_code=303)
        return FileResponse(Path(__file__).parent / "static" / "check.html", headers={
            "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
            "connect-src 'self'; media-src 'self' blob:; img-src 'self' data:; worker-src 'self'; "
            "font-src 'self'; frame-ancestors 'none'; base-uri 'none'; form-action 'self'",
        })

    @router.get("/check/assets/{asset}")
    async def check_asset(request: Request, asset: str):
        authorized(request)
        types = {"check.js": "text/javascript", "check.css": "text/css",
                 "check-library.js": "text/javascript", "check-recording-worklet.js": "text/javascript"}
        if asset not in types:
            raise HTTPException(404, "Fichier introuvable")
        return FileResponse(Path(__file__).parent / "static" / asset, media_type=types[asset])

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
        token = secrets.token_urlsafe(32)
        request.app.state.store.create_admin_session(
            hashlib.sha256(token.encode()).hexdigest(), credential_hash, now, now + ttl
        )
        response = JSONResponse({"ok": True})
        response.set_cookie(cookie, token, max_age=ttl, httponly=True,
                            secure=settings.admin_cookie_secure, samesite="strict", path="/admin")
        return response

    @router.post("/logout")
    async def logout(request: Request):
        same_origin(request)
        request.app.state.store.delete_admin_session(
            hashlib.sha256(request.cookies.get(cookie, "").encode()).hexdigest()
        )
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
        result = admin_data.media_file(request.app.state.store, message_id)
        data = result and await request.app.state.media.get(result[0])
        if not data:
            raise HTTPException(404, "Audio introuvable")
        # A private temporary file lets FileResponse answer Range requests (Safari needs them).
        descriptor, path = tempfile.mkstemp(prefix="wuumtaaba-admin-")
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        return FileResponse(path, media_type=result[1], content_disposition_type="inline",
                            background=BackgroundTask(os.unlink, path))

    @app.middleware("http")
    async def admin_headers(request, call_next):
        response = await call_next(request)
        if request.url.path == "/admin" or request.url.path.startswith("/admin/"):
            response.headers["Cache-Control"] = "no-store"
            response.headers["X-Content-Type-Options"] = "nosniff"
            response.headers["Referrer-Policy"] = "no-referrer"
            response.headers["X-Frame-Options"] = "DENY"
        return response

    install_admin_annotations(router, settings, authorized)
    install_experiments(router, authorized, same_origin)
    install_check_inference(router, settings, authorized, same_origin)
    from .dev_simulator import install_simulator
    install_simulator(router, settings, authorized, same_origin)
    app.include_router(router)
