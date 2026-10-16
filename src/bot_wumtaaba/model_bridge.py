"""Authenticated outbound Mac worker bridge; inference payloads stay in memory."""

import asyncio
import hmac
import json
import secrets
import time
from dataclasses import dataclass
from uuid import uuid4

from fastapi import HTTPException, Request
from fastapi.responses import JSONResponse

from .clients import MediaError

ROUTES = frozenset({"/translate", "/transcribe", "/tts", "/chat"})
MAX_RESULT_BYTES = 24 * 1024 * 1024


@dataclass
class _Job:
    id: str
    route: str
    payload: dict
    future: asyncio.Future
    deadline: float
    expires_at: float
    lease: str | None = None


class ModelBridge:
    """One event loop owns all jobs; SQLite stores only the selected provider."""

    def __init__(self, store, secret, timeout=330):
        self.store = store
        self._secret = secret.get_secret_value() if hasattr(secret, "get_secret_value") else secret
        self._secret = self._secret or ""
        if not isinstance(self._secret, str):
            raise TypeError("Bridge secret must be a string")
        if timeout <= 0:
            raise ValueError("Bridge timeout must be positive")
        self.timeout = timeout
        self._last_seen = None
        self._last_seen_monotonic = None
        self._jobs: dict[str, _Job] = {}
        with store.lock, store.db:
            store.db.execute(
                "CREATE TABLE IF NOT EXISTS app_model_config ("
                "id INTEGER PRIMARY KEY CHECK(id=1), provider TEXT NOT NULL "
                "CHECK(provider IN ('cloud','mac')), updated_at REAL NOT NULL)"
            )
            store.db.execute(
                "INSERT OR IGNORE INTO app_model_config(id,provider,updated_at) VALUES(1,'cloud',?)",
                (time.time(),),
            )

    @property
    def configured(self):
        return len(self._secret) >= 32

    @property
    def provider(self):
        with self.store.lock:
            return self.store.db.execute("SELECT provider FROM app_model_config WHERE id=1").fetchone()[0]

    @property
    def mac_online(self):
        return bool(self.configured and self._last_seen_monotonic is not None
                    and time.monotonic() - self._last_seen_monotonic < 20)

    def status(self):
        return {"provider": self.provider, "mac_online": self.mac_online,
                "last_seen": self._last_seen, "configured": self.configured}

    def select(self, provider):
        if provider not in {"cloud", "mac"}:
            raise HTTPException(400, "Fournisseur inconnu")
        if provider == "mac" and not self.mac_online:
            raise HTTPException(409, "Le Mac est hors ligne. Lance le connecteur avant de le sélectionner.")
        with self.store.lock, self.store.db:
            previous = self.provider
            if previous != provider:
                now = time.time()
                self.store.db.execute("UPDATE app_model_config SET provider=?,updated_at=? WHERE id=1",
                                      (provider, now))
                self.store._audit("model_provider_changed", provider,
                                  {"previous": previous, "provider": provider, "changed_at": now})
        return self.status()

    def authenticate(self, supplied):
        if not self.configured:
            raise HTTPException(503, "Mac connector is not configured")
        if not isinstance(supplied, str) or not hmac.compare_digest(
                self._secret.encode(), supplied.encode()):
            raise HTTPException(401, "Invalid Mac connector key")

    def _prune(self):
        now = time.monotonic()
        for identifier, job in list(self._jobs.items()):
            if job.future.done() or job.deadline <= now:
                self._jobs.pop(identifier, None)
                if not job.future.done():
                    job.future.set_exception(MediaError("Le Mac n’a pas répondu à temps. Réessaie."))

    async def submit(self, route, payload):
        if route not in ROUTES or not isinstance(payload, dict):
            raise MediaError("Opération Mac non prise en charge.")
        if not self.mac_online:
            raise MediaError("Le Mac est hors ligne. Réessaie après reconnexion du connecteur.")
        self._prune()
        if len(self._jobs) >= 8:
            raise MediaError("La file du Mac est pleine. Réessaie dans quelques instants.")
        future = asyncio.get_running_loop().create_future()
        identifier = uuid4().hex
        self._jobs[identifier] = _Job(identifier, route, payload, future,
                                      time.monotonic() + self.timeout, time.time() + self.timeout)
        try:
            return await asyncio.wait_for(future, timeout=self.timeout)
        except TimeoutError:
            raise MediaError("Le Mac n’a pas répondu à temps. Réessaie.") from None
        finally:
            self._jobs.pop(identifier, None)
            if not future.done():
                future.cancel()

    def poll(self, *, heartbeat_only=False):
        self._last_seen = time.time()
        self._last_seen_monotonic = time.monotonic()
        self._prune()
        if heartbeat_only:
            return None
        for job in self._jobs.values():
            if job.lease is None:
                job.lease = secrets.token_urlsafe(32)
                return {"id": job.id, "lease": job.lease, "route": job.route,
                        "payload": job.payload, "expires_at": job.expires_at}
        return None

    def complete(self, identifier, payload):
        self._prune()
        job = self._jobs.get(identifier)
        if job is None:
            raise HTTPException(404, "Unknown or expired job")
        if (not isinstance(payload, dict) or not isinstance(payload.get("lease"), str)
                or job.lease is None
                or not hmac.compare_digest(job.lease.encode(), payload["lease"].encode())):
            raise HTTPException(409, "Invalid job lease")
        if set(payload) == {"lease", "result"} and isinstance(payload["result"], dict):
            job.future.set_result(payload["result"])
        elif set(payload) == {"lease", "error"} and payload["error"] is True:
            job.future.set_exception(MediaError("Le modèle sur le Mac n’a pas pu traiter cette demande."))
        else:
            raise HTTPException(400, "Expected result object or error=true")
        return {"status": "accepted"}


def install_bridge(app, bridge):
    """Accept an instance or a getter for a bridge created during app lifespan."""
    def resolve(request):
        current = bridge() if callable(bridge) else bridge
        current.authenticate(request.headers.get("x-mac-key"))
        return current

    @app.post("/internal/mac/jobs")
    async def poll(request: Request):
        current = resolve(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > 1024:
                raise HTTPException(413, "Poll request too large")
        try:
            body = json.loads(raw) if raw else {}
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON") from None
        if (not isinstance(body, dict) or set(body) - {"heartbeat_only"}
                or type(body.get("heartbeat_only", False)) is not bool):
            raise HTTPException(400, "Expected optional heartbeat_only boolean")
        job = current.poll(heartbeat_only=body.get("heartbeat_only", False))
        return JSONResponse({"job": job}, headers={"Cache-Control": "no-store"})

    @app.post("/internal/mac/jobs/{identifier}")
    async def complete(identifier: str, request: Request):
        current = resolve(request)
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_RESULT_BYTES:
                raise HTTPException(413, "Result too large")
        try:
            payload = json.loads(raw)
        except (ValueError, UnicodeError):
            raise HTTPException(400, "Invalid JSON") from None
        return JSONResponse(current.complete(identifier, payload), headers={"Cache-Control": "no-store"})
