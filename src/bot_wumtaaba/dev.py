"""Isolated local backoffice, serving the production UI without WhatsApp workers."""

import argparse
import json
import re
import time
import urllib.request
from contextlib import asynccontextmanager
from pathlib import Path
from urllib.parse import unquote, urlsplit

import httpx
import uvicorn
from fastapi import FastAPI, HTTPException
from fastapi.responses import JSONResponse
from pydantic import SecretStr
from pydantic_settings import BaseSettings, SettingsConfigDict

from .admin import install_admin
from .clients import MediaError
from .config import Settings
from .media import MediaStore
from .store import Store

LOOPBACK = {"127.0.0.1", "localhost", "::1"}
NATIVE = "http://127.0.0.1:8765"


def validate_database(url):
    """Fail closed before constructing Store or opening any connection."""
    parsed = urlsplit(url)
    if (
        parsed.scheme not in {"postgres", "postgresql"}
        or parsed.hostname not in LOOPBACK
        or unquote(parsed.path) != "/wumtaaba_dev"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError(
            "Le serveur de développement exige une base locale nommée wumtaaba_dev, sans options d’URL."
        )
    return url


class LocalMedia:
    def __init__(self, root, *, annotations=False):
        self.root = Path(root).resolve()
        self.annotations = annotations
        self.root.mkdir(parents=True, exist_ok=True)

    def path(self, name):
        if self.annotations:
            if not isinstance(name, str) or not re.fullmatch(
                r"wuumtaaba/annotations/[0-9a-f]{64}\.audio", name
            ):
                raise ValueError("Chemin d’annotation locale invalide")
            key = name
        else:
            key = MediaStore.object_key(name)
        path = (self.root / key).resolve()
        if not path.is_relative_to(self.root):
            raise ValueError("Chemin audio local invalide")
        return path

    async def put(self, name, data, mime):
        path = self.path(name)
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_bytes(data)
        path.chmod(0o600)

    async def get(self, name):
        path = self.path(name)
        return path.read_bytes() if path.is_file() else None

    async def delete(self, name):
        self.path(name).unlink(missing_ok=True)

    async def verify_private(self, name):
        if await self.get(name) is None:
            raise ValueError("Audio local absent")


class DevOptions(BaseSettings):
    model_config = SettingsConfigDict(env_file=".env.dev", extra="ignore")
    dev_mac_api_key: SecretStr = SecretStr("")


class LocalBridge:
    def __init__(self, store, http, key):
        self.store, self.http, self.key = store, http, key

    @property
    def mac_online(self):
        try:
            with urllib.request.urlopen(NATIVE + "/healthz", timeout=2) as response:
                return json.load(response).get("status") == "ok"
        except (OSError, ValueError):
            return False

    @property
    def provider(self):
        with self.store.lock:
            return self.store.db.execute("SELECT provider FROM app_model_config WHERE id=1").fetchone()[0]

    def status(self):
        online = self.mac_online
        return {
            "provider": self.provider,
            "mac_online": online,
            "last_seen": time.time() if online else None,
            "configured": True,
        }

    def select(self, provider):
        if provider not in {"mac", "cloud"}:
            raise HTTPException(400, "Fournisseur inconnu")
        if provider == "mac" and not self.mac_online:
            raise HTTPException(409, "Le service Mac local est hors ligne.")
        with self.store.lock, self.store.db:
            self.store.db.execute(
                "UPDATE app_model_config SET provider=%s,updated_at=%s WHERE id=1", (provider, time.time())
            )
        return self.status()

    async def submit(self, route, payload):
        paths = {
            "/transcribe": "/api/v1/transcribe",
            "/translate": "/api/v1/translate",
            "/tts": "/api/v1/tts",
            "/chat": "/openai/v1/chat/completions",
        }
        if route not in paths:
            raise MediaError("Opération locale inconnue.")
        headers = {"X-API-Key": self.key} if self.key else {}
        try:
            response = await self.http.post(NATIVE + paths[route], json=payload, headers=headers, timeout=330)
            response.raise_for_status()
            result = response.json()
            if not isinstance(result, dict) or result.get("success") is False:
                raise ValueError()
            return result
        except (httpx.HTTPError, ValueError):
            raise MediaError(
                "Le modèle local est indisponible. Vérifiez le service 8765 et sa clé privée."
            ) from None


def create_dev_app(settings=None, *, media_root="data/dev/media", mac_key=None):
    settings = settings or Settings(_env_file=".env.dev")
    validate_database(settings.database_url.get_secret_value())
    if len(settings.admin_password.get_secret_value()) < 24:
        raise ValueError("Configurez ADMIN_PASSWORD (24 caractères minimum) dans .env.dev.")
    settings = settings.model_copy(update={"admin_cookie_secure": False})
    if mac_key is None:
        mac_key = DevOptions().dev_mac_api_key.get_secret_value()

    @asynccontextmanager
    async def lifespan(app):
        store = Store(settings.database_url.get_secret_value(), settings.database_schema)
        app.state.store = store
        app.state.media = LocalMedia(media_root)
        app.state.annotation_media = LocalMedia(media_root, annotations=True)
        async with httpx.AsyncClient(follow_redirects=False) as http:
            app.state.model_bridge = LocalBridge(store, http, mac_key)
            try:
                yield
            finally:
                store.close()

    app = FastAPI(lifespan=lifespan, docs_url=None, redoc_url=None, openapi_url=None)
    app.state.environment = "development"

    @app.middleware("http")
    async def loopback_only(request, call_next):
        host = urlsplit("http://" + request.headers.get("host", "")).hostname
        if host not in LOOPBACK and host != "testserver":
            return JSONResponse({"detail": "Accès local uniquement"}, status_code=403)
        return await call_next(request)

    @app.get("/healthz")
    async def health():
        return {"status": "ok", "environment": "development", "whatsapp_worker": False}

    install_admin(app, settings)
    return app


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--host", default="127.0.0.1", choices=sorted(LOOPBACK))
    parser.add_argument("--port", type=int, default=8000)
    parser.add_argument("--reload", action="store_true")
    args = parser.parse_args()
    if args.reload:
        uvicorn.run("bot_wumtaaba.dev:create_dev_app", factory=True, reload=True, host=args.host, port=args.port)
    else:
        uvicorn.run(create_dev_app(), host=args.host, port=args.port)


if __name__ == "__main__":
    main()
