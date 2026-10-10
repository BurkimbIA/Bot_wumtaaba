"""HTTP rapide + file persistante. Une instance et un worker pour ce pilote."""

import asyncio
import hashlib
import hmac
import json
import logging
import time
from contextlib import asynccontextmanager, suppress

import httpx
from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import PlainTextResponse

from .clients import Intelligence, MediaError, WhatsApp
from .config import Settings
from .conversation import Conversation
from .domain import text
from .reminders import can_send_outbox, queue_due_reminders
from .store import Store

logger = logging.getLogger("wuumtaaba")
MAX_WEBHOOK_BYTES = 1_000_000


def signature_valid(body: bytes, signature: str, secret: str) -> bool:
    expected = "sha256=" + hmac.new(secret.encode(), body, hashlib.sha256).hexdigest()
    return bool(secret) and hmac.compare_digest(expected, signature)


def incoming_values(payload: dict, phone_id: str):
    if payload.get("object") != "whatsapp_business_account":
        return
    for entry in payload.get("entry", []):
        for change in entry.get("changes", []):
            if change.get("field") != "messages":
                continue
            value = change.get("value", {})
            if value.get("metadata", {}).get("phone_number_id") != phone_id:
                continue
            yield value


def incoming_messages(payload: dict, phone_id: str):
    for value in incoming_values(payload, phone_id):
        for message in value.get("messages", []):
            # Conserver les contenus utiles, pas les URLs temporaires des médias.
            if not isinstance(message, dict):
                continue
            minimal = {
                key: message[key]
                for key in ("id", "from", "timestamp", "type", "text", "interactive", "button",
                            "context", "reaction", "location", "contacts", "errors")
                if key in message
            }
            for kind in ("audio", "image", "video", "document", "sticker"):
                media = message.get(kind)
                if isinstance(media, dict):
                    minimal[kind] = {k: media[k] for k in (
                        "id", "mime_type", "sha256", "voice", "caption", "filename"
                    ) if k in media}
            yield minimal


async def process_once(store, conversation, whatsapp, *, settings=None):
    """Les erreurs d'envoi ambiguës ne sont pas réessayées automatiquement."""
    job = store.claim_inbox()
    if job and time.time() - int(job["message"].get("timestamp", job["created_at"])) > 23 * 3600:
        store.fail_inbox(job["id"], "expired")
        job = None
    if job:
        try:
            state = store.get_session(job["sender"])
            state, replies, agreement = await conversation.handle(
                job["sender"], job["message"], state
            )
            store.finish_inbox(job["id"], job["sender"], state, replies, agreement)
        except (MediaError, ValueError) as exc:
            # Pas de détail provider ni de message utilisateur dans les logs.
            logger.warning("message_rejected kind=%s", type(exc).__name__)
            store.finish_inbox(
                job["id"],
                job["sender"],
                store.get_session(job["sender"]),
                [
                    text(
                        "Je n’ai pas pu traiter ce message. Envoie un vocal plus court "
                        "ou écris les informations. Ton accord n’a pas été enregistré."
                    )
                ],
            )
        except Exception as exc:  # noqa: BLE001 — frontière worker, logs sans données
            logger.error("message_failed kind=%s", type(exc).__name__)
            if job["attempts"] >= 3:
                state = store.get_session(job["sender"])
                state.pop("confirmation", None)
                store.finish_inbox(
                    job["id"],
                    job["sender"],
                    state,
                    [
                        text(
                            "Le service est momentanément indisponible. Réessaie dans quelques minutes. "
                            "Aucun nouvel accord n’a été enregistré pour ce message."
                        )
                    ],
                )
            else:
                store.retry_inbox(job["id"], type(exc).__name__)
    outgoing = store.claim_outbox()
    if outgoing and outgoing.get("category") == "reminder" and (
            (settings is not None and not settings.reminder_enabled)
            or not can_send_outbox(store, outgoing)):
        store.fail_outbox(outgoing["id"], "reminder_not_authorized")
        outgoing = None
    if (outgoing and outgoing.get("category") != "reminder"
            and time.time() - outgoing["created_at"] > 23 * 3600):
        store.fail_outbox(outgoing["id"], "expired")
        outgoing = None
    if outgoing:
        try:
            remote_id = await whatsapp.send(outgoing["recipient"], outgoing["content"])
            store.sent_outbox(outgoing["id"], remote_id)
        except httpx.HTTPStatusError as exc:
            if exc.response.status_code == 429:
                store.retry_outbox(outgoing["id"], "rate_limited")
            else:
                store.fail_outbox(outgoing["id"], f"http_{exc.response.status_code}")
            logger.warning("send_rejected status=%s", exc.response.status_code)
        except Exception as exc:  # noqa: BLE001 — frontière worker, logs sans données
            # Une réponse perdue ne permet pas de savoir si Meta a déjà envoyé le message.
            store.fail_outbox(outgoing["id"], type(exc).__name__, status="uncertain")
            logger.error("send_uncertain kind=%s", type(exc).__name__)
    return bool(job or outgoing)


def create_app(settings: Settings | None = None, *, run_worker=True) -> FastAPI:
    settings = settings or Settings()

    @asynccontextmanager
    async def lifespan(app):
        settings.validate_live()
        store = Store(settings.database_path)
        store.recover()
        store.purge(settings.retention_days)
        app.state.store = store
        async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
            whatsapp = WhatsApp(settings, http)
            conversation = Conversation(settings, whatsapp, Intelligence(settings, http), store)

            async def worker():
                last_purge = time.monotonic()
                last_schedule = 0
                while True:
                    try:
                        if time.monotonic() - last_purge > 3600:
                            store.purge(settings.retention_days)
                            last_purge = time.monotonic()
                        if time.monotonic() - last_schedule > 30:
                            queue_due_reminders(store, settings)
                            last_schedule = time.monotonic()
                        busy = await process_once(store, conversation, whatsapp, settings=settings)
                        if not busy:
                            await asyncio.sleep(0.5)
                    except asyncio.CancelledError:
                        raise
                    except Exception as exc:  # noqa: BLE001 — frontière worker, logs sans données
                        logger.error("worker_error kind=%s", type(exc).__name__)
                        await asyncio.sleep(2)

            task = asyncio.create_task(worker()) if run_worker else None
            app.state.worker = task
            try:
                yield
            finally:
                if task:
                    task.cancel()
                    with suppress(asyncio.CancelledError):
                        await task
                store.close()

    app = FastAPI(
        title="Wuumtaaba",
        version="0.2.0",
        lifespan=lifespan,
        docs_url=None,
        redoc_url=None,
        openapi_url=None,
    )

    @app.get("/healthz")
    async def health():
        task = app.state.worker
        if task and task.done():
            raise HTTPException(503, "worker unavailable")
        return {"status": "ok", "service": "wuumtaaba", "version": "0.2.0"}

    @app.get("/webhook", response_class=PlainTextResponse)
    async def verify(request: Request):
        q = request.query_params
        if q.get("hub.mode") != "subscribe" or not hmac.compare_digest(
            q.get("hub.verify_token", ""), settings.webhook_verify_token.get_secret_value()
        ):
            raise HTTPException(403, "Verification refused")
        return q.get("hub.challenge", "")

    @app.post("/webhook")
    async def webhook(request: Request):
        raw = bytearray()
        async for chunk in request.stream():
            raw.extend(chunk)
            if len(raw) > MAX_WEBHOOK_BYTES:
                raise HTTPException(413, "Payload too large")
        if not signature_valid(
            bytes(raw),
            request.headers.get("x-hub-signature-256", ""),
            settings.meta_app_secret.get_secret_value(),
        ):
            raise HTTPException(403, "Invalid signature")
        try:
            payload = json.loads(raw)
            if not isinstance(payload, dict):
                raise TypeError()
            messages = list(incoming_messages(payload, settings.whatsapp_phone_number_id))
            statuses = [status for value in incoming_values(payload, settings.whatsapp_phone_number_id)
                        for status in value.get("statuses", []) if isinstance(status, dict)]
        except (ValueError, TypeError, AttributeError):
            raise HTTPException(400, "Invalid payload") from None
        store = app.state.store
        for status in statuses:
            store.record_delivery_status(status)
        for message in messages:
            sender, message_id = message.get("from", ""), message.get("id", "")
            if (not isinstance(sender, str) or not sender.isdigit()
                    or not isinstance(message_id, str) or not message_id):
                continue
            if sender not in settings.senders and not store.is_known_customer(sender):
                store.archive_message(message, status="ignored", reason="not_registered")
                continue
            try:
                age = time.time() - int(message.get("timestamp", 0))
            except (ValueError, TypeError):
                store.archive_message(message, status="ignored", reason="invalid_timestamp")
                continue
            if not 0 <= age < 23 * 3600:
                store.archive_message(message, status="ignored", reason="expired")
                continue
            body = message.get("text", {}).get("body", "")
            command = body.strip().casefold() if isinstance(body, str) else ""
            opt_out = command == "stop" or command.startswith("refuser ")
            if not opt_out and store.count_recent(sender) >= settings.max_messages_per_hour:
                store.archive_message(message, status="ignored", reason="rate_limited")
                continue
            store.enqueue(message_id, sender, message)
        # Accuser réception seulement après écriture durable en SQLite.
        return {"status": "accepted"}

    return app


app = create_app()
