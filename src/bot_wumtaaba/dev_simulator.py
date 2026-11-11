"""The real WhatsApp dialogue, with a session-only carnet and model diagnostics.

Only model requests leave the laboratory. There is no Meta client, production
Store, database URL, background worker, or persistent history in this module.
"""

import asyncio
import base64
import binascii
import hashlib
import importlib
import json
import secrets
import sqlite3
import threading
import time
import unicodedata
from copy import deepcopy
from dataclasses import dataclass, field
from functools import lru_cache
from contextvars import ContextVar
from pathlib import Path
from types import SimpleNamespace
from uuid import uuid4

import httpx
from fastapi import APIRouter, Depends, HTTPException, Request
from fastapi.responses import Response, FileResponse
from pydantic import SecretStr

MAX_AUDIO_BYTES = 7 * 1024 * 1024
MAX_BODY_BYTES = MAX_AUDIO_BYTES * 4 // 3 + 64 * 1024
MAX_SESSIONS = 12
SESSION_TTL = 60 * 60
MAX_AGREEMENTS = 100
MAX_MEDIA_BYTES = 24 * 1024 * 1024
SENDER = "lab-user"


@lru_cache(maxsize=1)
def bot_modules():
    """Import the current package engine; the simulator never owns another dialogue."""
    try:
        modules = SimpleNamespace(
            conversation=importlib.import_module("bot_wumtaaba.conversation"),
            portal=importlib.import_module("bot_wumtaaba.portal"),
            clients=importlib.import_module("bot_wumtaaba.clients"),
            burkimbia=importlib.import_module("bot_wumtaaba.burkimbia"),
        )
        digest = hashlib.sha256()
        for file in sorted(Path(modules.conversation.__file__).parent.glob("*.py")):
            digest.update(file.name.encode())
            digest.update(file.read_bytes())
        modules.revision = digest.hexdigest()[:12]
        return modules
    except ImportError:
        raise HTTPException(
            503, "Le moteur du bot ne peut pas être chargé. Relancez le laboratoire."
        ) from None


def fold(value):
    return "".join(
        char for char in unicodedata.normalize("NFKD", value or "") if not unicodedata.combining(char)
    ).casefold()


class SearchDatabase:
    """SQLite only for the shared payment dialogue's owner-scoped name query."""

    def __init__(self):
        self.connection = sqlite3.connect(":memory:", check_same_thread=False, isolation_level=None)
        self.connection.row_factory = sqlite3.Row
        self.connection.create_function("search_fold", 1, fold)
        self.connection.create_function("strpos", 2, lambda text, query: (text or "").find(query) + 1)
        self.connection.execute(
            "CREATE TABLE agreements(id TEXT PRIMARY KEY, sender TEXT, reference TEXT, "
            "client_name TEXT, paid INTEGER, total INTEGER, created_at REAL)"
        )

    def execute(self, query, values=()):
        return self.connection.execute(query.replace("%s", "?"), values)

    def close(self):
        self.connection.close()


class MemoryStore:
    def __init__(self):
        self.lock = threading.RLock()
        self.db = SearchDatabase()
        self.agreements = {}
        self.payments = {}
        self.enabled = True
        self.consents = {}

    def close(self):
        self.db.close()

    def is_known_customer(self, sender):
        return any(row.get("client_phone") == sender for row in self.agreements.values())

    def reminders_enabled(self, sender):
        return self.enabled

    def set_reminders_enabled(self, sender, enabled):
        self.enabled = enabled

    def set_customer_consent(self, sender, reference, granted):
        row = next(
            (
                row
                for row in self.agreements.values()
                if row["reference"] == reference and row.get("client_phone") == sender
            ),
            None,
        )
        if row is None:
            return False
        self.consents[row["id"]] = granted
        return True

    def list_agreements(self, sender, offset=0, limit=6):
        rows = sorted(self.agreements.values(), key=lambda row: (row["created_at"], row["id"]), reverse=True)
        return deepcopy(rows[offset : offset + limit])

    def get_agreement(self, sender, reference):
        rows = [
            row
            for row in self.agreements.values()
            if row["id"] == reference or row["reference"] == reference.upper()
        ]
        if len(rows) != 1:
            return None
        row = deepcopy(rows[0])
        balance = (
            max(0, row["total"] - row["paid"])
            if row.get("total") is not None and row.get("paid") is not None
            else None
        )
        row["balance"] = balance
        row["status"] = "incomplete" if balance is None else row.get("status", "open")
        return row

    def payment_history(self, sender, reference, offset=0, limit=20):
        row = self.get_agreement(sender, reference)
        return deepcopy(self.payments.get(row["id"], [])[offset : offset + limit]) if row else []

    def calendar_for(self, sender):
        rows = [
            self.get_agreement(sender, row["id"])
            for row in self.agreements.values()
            if row.get("due_date") and row.get("total") is not None and row.get("paid") is not None
        ]
        return sorted(rows, key=lambda row: (row["due_date"], row["id"]))

    def record_media(self, *args, **kwargs):
        # The shared engine's archive call writes bytes to MemoryMedia, never disk.
        pass

    def apply(self, operation):
        if not operation:
            return
        kind = operation.get("_operation")
        if kind in {"mobile_pair", "mobile_revoke"}:
            # A displayed pairing code is simulated and cannot open the real mobile API.
            return
        if kind == "payment":
            self.apply_payment(operation)
            return
        if len(self.agreements) >= MAX_AGREEMENTS:
            raise HTTPException(409, "Carnet de test plein. Réinitialisez la simulation.")
        row = deepcopy(operation)
        row["created_at"] = time.time()
        row["reference"] = "WT-" + row["id"][:8].upper()
        row["status"] = "paid" if row.get("due_date") and row.get("paid") == row.get("total") else "open"
        self.agreements[row["id"]] = row
        self.payments[row["id"]] = []
        if row.get("paid") is not None and row["paid"] > 0:
            self.payments[row["id"]].append(
                {
                    "id": "advance:" + row["id"],
                    "amount": row["paid"],
                    "kind": "initial",
                    "created_at": row["created_at"],
                }
            )
        self.db.execute(
            "INSERT INTO agreements VALUES(?,?,?,?,?,?,?)",
            (
                row["id"],
                SENDER,
                row["reference"],
                row.get("client"),
                row.get("paid"),
                row.get("total"),
                row["created_at"],
            ),
        )

    def apply_payment(self, operation):
        row = self.get_agreement(SENDER, operation["agreement_id"])
        if not row or row["balance"] is None:
            raise HTTPException(409, "Ce solde ne permet pas d’ajouter un paiement.")
        history = self.payments[row["id"]]
        if any(item["id"] == operation["id"] for item in history):
            return
        amount = operation["amount"]
        if (
            row["paid"] != operation["expected_paid"]
            or type(amount) is not int
            or not 0 < amount <= row["balance"]
        ):
            raise HTTPException(409, "Le solde a changé. Recommencez le paiement.")
        self.agreements[row["id"]]["paid"] += amount
        if row.get("due_date") and amount == row["balance"]:
            self.agreements[row["id"]]["status"] = "settled"
        history.append(
            {
                "id": operation["id"],
                "amount": amount,
                "kind": operation.get("kind", "payment"),
                "created_at": time.time(),
            }
        )
        self.db.execute("UPDATE agreements SET paid=? WHERE id=?", (row["paid"] + amount, row["id"]))

    def settle_agreement(self, sender, reference):
        row = self.get_agreement(sender, reference)
        if not row or row["balance"] is None:
            return False
        if row["balance"]:
            self.apply_payment(
                {
                    "id": uuid4().hex,
                    "agreement_id": row["id"],
                    "amount": row["balance"],
                    "expected_paid": row["paid"],
                    "kind": "settlement",
                }
            )
        return True


class MemoryMedia:
    def __init__(self):
        self.objects = {}

    async def put(self, name, data, mime):
        previous = len(self.objects.get(name, (b"", ""))[0])
        if sum(len(value[0]) for value in self.objects.values()) - previous + len(data) > MAX_MEDIA_BYTES:
            raise ValueError("Session audio pleine")
        self.objects[name] = (data, mime)


class LabWhatsApp:
    def __init__(self, media):
        self.media = media
        self.incoming = {}

    async def audio(self, identifier):
        return self.incoming[identifier]

    async def upload_audio(self, data, filename, mime):
        identifier = secrets.token_hex(16)
        await self.media.put(identifier, data, mime)
        return identifier


@dataclass
class Session:
    identifier: str
    state: dict = field(default_factory=dict)
    store: MemoryStore = field(default_factory=MemoryStore)
    media: MemoryMedia = field(default_factory=MemoryMedia)
    lock: asyncio.Lock = field(default_factory=asyncio.Lock)
    last_seen: float = field(default_factory=time.monotonic)


async def read_body(request):
    raw = bytearray()
    async for part in request.stream():
        raw.extend(part)
        if len(raw) > MAX_BODY_BYTES:
            raise HTTPException(413, "Requête trop volumineuse (audio : 7 Mo maximum).")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, TypeError):
        raise HTTPException(422, "Données de conversation invalides.") from None


async def trace(stages, task, model, value, work):
    start = time.perf_counter()
    stage = {"task": task, "model": model, "input": value}
    stages.append(stage)
    try:
        result = await work()
        stage["output"] = result.model_dump(mode="json") if hasattr(result, "model_dump") else result
        return result
    except Exception:
        stage["error"] = "Le modèle n’a pas pu produire de résultat."
        raise
    finally:
        stage["seconds"] = round(time.perf_counter() - start, 3)


def _install_engine(app, config, runner, intelligence_factory=None):
    sessions = {}

    def prune():
        now = time.monotonic()
        for identifier, session in list(sessions.items()):
            if now - session.last_seen > SESSION_TTL and not session.lock.locked():
                session.store.close()
                del sessions[identifier]

    def find(body):
        prune()
        identifier = body.get("session_id")
        if not isinstance(identifier, str) or identifier not in sessions:
            raise HTTPException(410, "Cette simulation a expiré. Démarrez une nouvelle conversation.")
        session = sessions[identifier]
        session.last_seen = time.monotonic()
        return session

    async def turn(session, body, message):
        modules = bot_modules()
        stages = []
        keys = config()
        settings = SimpleNamespace(
            public_access=True,
            senders={SENDER},
            retention_days=0,
            support_contact="Le laboratoire Wuumtaaba",
            gemini_api_key=SecretStr(keys["GEMINI_API_KEY"]),
            extraction_model=keys["GEMINI_MODEL"],
            transcription_model="gemini-3.8-flash",
            french_tts_model="gemini-3.1-flash-tts-preview",
            max_audio_bytes=MAX_AUDIO_BYTES,
            burkimbia_api_url="https://bia-backend.fly.dev/api/v1",
            burkimbia_translation_model="bia-translation-v1-fast",
        )
        provider = body.get("provider", "mac")
        if not isinstance(provider, str) or provider not in {"mac", "cloud"}:
            raise HTTPException(422, "Fournisseur inconnu.")
        for key in ("transcription_model", "translation_model"):
            if body.get(key) is not None and (not isinstance(body[key], str) or len(body[key]) > 200):
                raise HTTPException(422, "Choix de modèle invalide.")

        class LabBurkimbIA(modules.burkimbia.BurkimbIA):
            async def _request(self, route, payload):
                task = {"/transcribe": "transcribe", "/translate": "translate", "/tts": "tts"}[route]
                request = {"model": "bia-translation-v1-fast"}
                if task == "transcribe":
                    request["audio_base64"] = payload["audio_base64"]
                    if provider == "mac" and body.get("transcription_model"):
                        request["model_id"] = body["transcription_model"]
                    elif provider == "cloud" and body.get("transcription_model") not in {
                        None,
                        "",
                        "bia-transcription-v1",
                    }:
                        raise modules.clients.MediaError("Modèle cloud de transcription inconnu.")
                    diagnostic = "Vocal reçu dans la simulation"
                else:
                    request["text"] = payload["text"]
                    diagnostic = payload["text"]
                if task == "translate":
                    request["source"] = payload["src_lang"]
                    if payload["src_lang"] == "moore" and body.get("translation_model"):
                        request["model_id" if provider == "mac" else "model"] = body["translation_model"]
                model = request.get("model_id") or payload.get("model") or ""

                async def invoke():
                    try:
                        result = await runner(provider, task, request)
                    except HTTPException:
                        raise modules.clients.MediaError(
                            "Choix de modèle indisponible ou entrée refusée."
                        ) from None
                    if not result.get("ok"):
                        raise modules.clients.MediaError(result.get("error") or "Modèle indisponible.")
                    return result

                result = await trace(stages, task, model, diagnostic, invoke)
                stages[-1]["model"] = result.get("model") or model
                stages[-1]["output"] = result.get("text") if task != "tts" else "Audio produit"
                return {
                    "output" if task == "translate" else "text": result.get("text"),
                    "wav": result.get("wav"),
                }

        async with httpx.AsyncClient() as http:
            real = (
                intelligence_factory(settings, http)
                if intelligence_factory
                else modules.clients.Intelligence(settings, http)
            )

            class TracedIntelligence:
                async def extract(self, text, current):
                    return await trace(
                        stages,
                        "extract",
                        settings.extraction_model,
                        {"text": text, "draft": current},
                        lambda: real.extract(text, current),
                    )

                async def transcribe(self, data, filename, mime):
                    return await trace(
                        stages,
                        "transcribe",
                        settings.transcription_model,
                        "Vocal français reçu",
                        lambda: real.transcribe(data, filename, mime),
                    )

                async def synthesize_french(self, text):
                    result = await trace(
                        stages, "tts", settings.french_tts_model, text, lambda: real.synthesize_french(text)
                    )
                    stages[-1]["output"] = "Audio produit"
                    return result

            whatsapp = LabWhatsApp(session.media)
            if message["type"] == "audio":
                try:
                    encoded = body["audio_base64"]
                    if not isinstance(encoded, str):
                        raise ValueError()
                    data = base64.b64decode(encoded, validate=True)
                    if not 0 < len(data) <= MAX_AUDIO_BYTES:
                        raise ValueError()
                    mime = body.get("mime", "audio/wav")
                    if mime not in {
                        "audio/wav",
                        "audio/ogg",
                        "audio/mpeg",
                        "audio/webm",
                        "audio/flac",
                        "audio/mp4",
                    }:
                        raise ValueError()
                    whatsapp.incoming[message["audio"]["id"]] = (data, "vocal", mime)
                except (ValueError, TypeError, binascii.Error):
                    raise HTTPException(422, "Vocal invalide (7 Mo maximum).") from None
            bia = LabBurkimbIA(settings, http)
            conversation = modules.conversation.Conversation(
                settings, whatsapp, TracedIntelligence(), session.store, session.media, bia
            )
            portal = modules.portal.PortalConversation(
                settings, whatsapp, bia, session.store, conversation, session.media
            )
            state, replies, operation = await portal.handle(SENDER, message, session.state)
            session.store.apply(operation)
            session.state = state
        for reply in replies:
            reply.pop("_local_audio", None)
            if reply.get("type") == "audio":
                url = f"/admin/api/simulator/audio/{session.identifier}/{reply['audio']['id']}"
                reply["url"] = url
                reply["audio"]["url"] = url
        state = session.state.get("legacy", {})
        return {
            "session_id": session.identifier,
            "replies": replies,
            "state": state,
            "draft": state.get("draft"),
            "agreements": [
                session.store.get_agreement(SENDER, row["id"])
                for row in session.store.list_agreements(SENDER, limit=MAX_AGREEMENTS)
            ],
            "stages": stages,
            "engine": {
                "name": "Wuumtaaba WhatsApp",
                "source": "Bot_wumtaaba — moteur partagé",
                "revision": modules.revision,
                "simulation": True,
            },
        }

    @app.post("/session")
    async def start_session(request: Request):
        await read_body(request)
        prune()
        if len(sessions) >= MAX_SESSIONS:
            raise HTTPException(429, "Trop de conversations de test. Réessayez plus tard.")
        session = Session(secrets.token_urlsafe(24))
        sessions[session.identifier] = session
        try:
            async with session.lock:
                return await turn(session, {}, {"type": "text", "text": {"body": "menu"}})
        except Exception:
            session.store.close()
            sessions.pop(session.identifier, None)
            raise

    @app.post("/turn")
    async def conversation_turn(request: Request):
        body = await read_body(request)
        session = find(body)
        inputs = [name for name in ("text", "action", "audio_base64") if name in body]
        if len(inputs) != 1:
            raise HTTPException(422, "Envoyez un texte, un bouton ou un vocal à la fois.")
        kind = inputs[0]
        value = body[kind]
        if kind != "audio_base64" and (
            not isinstance(value, str) or not value.strip() or len(value) > (8000 if kind == "text" else 200)
        ):
            raise HTTPException(422, "Message ou bouton invalide.")
        message = {"id": "lab-" + uuid4().hex}
        if kind == "text":
            message.update(type="text", text={"body": value})
        elif kind == "action":
            if value.startswith(("ann:", "annpreview:")):
                raise HTTPException(422, "Les annotations se consultent dans leur page dédiée.")
            message.update(type="interactive", interactive={"button_reply": {"id": value}})
        else:
            message.update(type="audio", audio={"id": str(uuid4().int)})
        async with session.lock:
            return await turn(session, body, message)

    @app.post("/reset")
    async def reset_session(request: Request):
        session = find(await read_body(request))
        async with session.lock:
            session.store.close()
            session.store = MemoryStore()
            session.media = MemoryMedia()
            session.state = {}
            return await turn(session, {}, {"type": "text", "text": {"body": "menu"}})

    @app.get("/audio/{identifier}/{audio_id}")
    async def get_audio(identifier: str, audio_id: str):
        session = find({"session_id": identifier})
        item = session.media.objects.get(audio_id)
        if item is None:
            raise HTTPException(404, "Audio de test introuvable.")
        return Response(item[0], media_type=item[1], headers={"Cache-Control": "no-store"})


CURRENT_REQUEST = ContextVar("simulator_request")


def install_simulator(router, settings, authorized, same_origin, intelligence_factory=None):
    """Install the isolated simulator behind admin cookies and same-origin checks."""

    async def guard(request: Request):
        authorized(request)
        if request.method != "GET":
            same_origin(request)
        token = CURRENT_REQUEST.set(request)
        try:
            yield
        finally:
            CURRENT_REQUEST.reset(token)

    @router.get("/simulator", dependencies=[Depends(guard)])
    async def simulator_page():
        return FileResponse(
            Path(__file__).parent / "static" / "simulator.html",
            headers={
                "Content-Security-Policy": "default-src 'none'; script-src 'self'; style-src 'self'; "
                "media-src 'self' blob:; connect-src 'self'; worker-src 'self'; frame-ancestors 'none'; base-uri 'none'",
            },
        )

    @router.get("/simulator/assets/{asset}", dependencies=[Depends(guard)])
    async def simulator_asset(asset: str):
        names = {
            "simulator.js": "text/javascript",
            "simulator.css": "text/css",
            "recording-worklet.js": "text/javascript",
        }
        if asset not in names:
            raise HTTPException(404, "Fichier introuvable.")
        filename = "check-recording-worklet.js" if asset == "recording-worklet.js" else asset
        return FileResponse(Path(__file__).parent / "static" / filename, media_type=names[asset])

    def config():
        return {
            "GEMINI_API_KEY": settings.gemini_api_key.get_secret_value(),
            "GEMINI_MODEL": settings.extraction_model,
        }

    async def runner(provider, task, payload):
        if provider != "mac":
            raise HTTPException(422, "Ce simulateur utilise les modèles du Mac.")
        from .check_inference import MAC_MODELS

        if task in {"transcribe", "translate"}:
            group = "transcription" if task == "transcribe" else "translation"
            selected = payload.get("model_id")
            if selected is not None and selected not in MAC_MODELS[group]:
                raise HTTPException(422, "Modèle local inconnu.")
        body = {"model": "bia-translation-v1-fast"}
        if task == "translate":
            source = payload.get("source", "moore")
            if source not in {"moore", "french"}:
                raise HTTPException(422, "Langue inconnue.")
            body.update(
                text=payload.get("text"), src_lang=source, tgt_lang="french" if source == "moore" else "moore"
            )
        elif task == "transcribe":
            body.update(
                audio_base64=payload.get("audio_base64"),
                language="moore",
                model="bia-transcription-v1",
                return_timestamps=False,
            )
        else:
            body.update(text=payload.get("text"), model="bia-tts-v1", voice_cloning=False)
        if payload.get("model_id"):
            body["model_id"] = payload["model_id"]
        started = time.perf_counter()
        result = await CURRENT_REQUEST.get().app.state.model_bridge.submit("/" + task, body)
        return {
            "ok": True,
            "text": result.get("output") if task == "translate" else result.get("text"),
            "wav": result.get("wav"),
            "model": body.get("model_id") or body["model"],
            "seconds": round(time.perf_counter() - started, 3),
        }

    api = APIRouter(prefix="/api/simulator", dependencies=[Depends(guard)])
    _install_engine(api, config, runner, intelligence_factory)
    router.include_router(api)
