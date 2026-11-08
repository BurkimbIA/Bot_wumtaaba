"""Admin-only audio experiments stored in Postgres and private Tigris storage."""

import asyncio
import base64
import binascii
import json
import math
import os
import re
import tempfile
import time
from contextlib import suppress
from uuid import UUID, uuid4

from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

MAX_AUDIO_BYTES = 7 * 1024 * 1024
MAX_RESULTS_BYTES = 128 * 1024
MAX_BODY_BYTES = 4 * ((MAX_AUDIO_BYTES + 2) // 3) + MAX_RESULTS_BYTES + 4096
MIMES = frozenset(
    {
        "audio/ogg",
        "audio/mpeg",
        "audio/mp4",
        "audio/wav",
        "audio/x-wav",
        "audio/aac",
        "audio/amr",
        "audio/webm",
    }
)
PATH_PATTERN = re.compile(r"experiments/[0-9a-f]{32}\.audio")


def identifier(value):
    try:
        if not isinstance(value, str):
            raise ValueError()
        return str(UUID(value))
    except (ValueError, AttributeError):
        raise HTTPException(422, "Identifiant de test invalide.") from None


async def read_json(request, limit):
    if request.headers.get("content-type", "").split(";", 1)[0].strip().lower() != "application/json":
        raise HTTPException(400, "Une requête JSON est requise.")
    raw = bytearray()
    async for chunk in request.stream():
        raw.extend(chunk)
        if len(raw) > limit:
            raise HTTPException(413, "Requête trop volumineuse (audio : 7 Mo maximum).")
    try:
        body = json.loads(raw)
        if not isinstance(body, dict):
            raise ValueError()
        return body
    except (ValueError, UnicodeError):
        raise HTTPException(400, "Requête JSON invalide.") from None


def name(value):
    if not isinstance(value, str) or not 1 <= len(value.strip()) <= 200:
        raise HTTPException(422, "Nom du test requis (200 caractères maximum).")
    return value.strip()


def results(value):
    if not isinstance(value, list) or len(value) > 3 or any(not isinstance(item, dict) for item in value):
        raise HTTPException(422, "Les résultats doivent contenir au maximum trois étapes.")
    try:
        encoded = json.dumps(value, ensure_ascii=False, allow_nan=False)
        if len(encoded.encode()) > MAX_RESULTS_BYTES:
            raise ValueError()
        return encoded
    except (TypeError, ValueError, RecursionError):
        raise HTTPException(422, "Résultats invalides ou trop volumineux (128 Ko maximum).") from None


def descriptor(row):
    try:
        stages = json.loads(row["results"])
    except (ValueError, TypeError):
        stages = []
    return {
        "id": row["id"],
        "name": row["name"],
        "createdAt": row["created_at"] * 1000,
        "results": stages,
        "mime": row["mime"],
        "byte_count": row["byte_count"],
        "audio_url": "/admin/api/experiments/" + row["id"] + "/audio",
    }


def checked_path(row):
    path = row["storage_path"] if row else ""
    if not PATH_PATTERN.fullmatch(path or ""):
        raise HTTPException(404, "Audio de test introuvable.")
    return path


async def delete_object(media, path):
    if not PATH_PATTERN.fullmatch(path):
        raise ValueError("Invalid experiment object path")
    await media.delete(path)


def install_experiments(router, authorized, same_origin):
    mutations = asyncio.Lock()

    def get_row(store, value):
        with store.lock:
            return store.db.execute("SELECT * FROM experiments WHERE id=%s", (value,)).fetchone()

    @router.get("/api/experiments")
    async def list_experiments(request: Request):
        authorized(request)
        store = request.app.state.store
        with store.lock:
            rows = list(
                store.db.execute("SELECT * FROM experiments ORDER BY created_at DESC,id DESC LIMIT 100")
            )
        return {"items": [descriptor(row) for row in rows]}

    @router.post("/api/experiments")
    async def save_experiment(request: Request):
        authorized(request)
        same_origin(request)
        body = await read_json(request, MAX_BODY_BYTES)
        if set(body) - {"id", "name", "audio_base64", "mime", "results", "createdAt"}:
            raise HTTPException(422, "Champ de test inconnu.")
        value = identifier(body["id"]) if "id" in body else str(uuid4())
        title, encoded_results = name(body.get("name")), results(body.get("results", []))
        mime = body.get("mime", "")
        if not isinstance(mime, str) or mime not in MIMES:
            raise HTTPException(422, "Format audio non pris en charge.")
        try:
            encoded_audio = body["audio_base64"]
            if not isinstance(encoded_audio, str):
                raise ValueError()
            audio = base64.b64decode(encoded_audio, validate=True)
            if not 0 < len(audio) <= MAX_AUDIO_BYTES:
                raise ValueError()
        except (KeyError, ValueError, binascii.Error):
            raise HTTPException(422, "Audio invalide (7 Mo maximum).") from None
        created = body.get("createdAt", time.time() * 1000)
        if (
            type(created) not in {int, float}
            or not 0 <= created <= time.time() * 1000 + 300_000
            or not math.isfinite(created)
        ):
            raise HTTPException(422, "Date du test invalide.")
        store, media = request.app.state.store, request.app.state.media
        async with mutations:
            old = get_row(store, value)
            with store.lock:
                count = store.db.execute("SELECT COUNT(*) FROM experiments").fetchone()[0]
            if not old and count >= 100:
                raise HTTPException(
                    409, "100 tests maximum. Supprimez un test avant d’en enregistrer un nouveau."
                )
            # A new private object on every replacement keeps the previous audio intact on DB failure.
            path = "experiments/" + uuid4().hex + ".audio"
            try:
                await media.put(path, audio, mime)
            except Exception:
                raise HTTPException(503, "Impossible d’enregistrer l’audio. Réessayez.") from None
            try:
                with store.lock, store.db:
                    store.db.execute(
                        "INSERT INTO experiments(id,name,storage_path,mime,byte_count,created_at,updated_at,results) "
                        "VALUES(%s,%s,%s,%s,%s,%s,%s,%s) ON CONFLICT(id) DO UPDATE SET "
                        "name=excluded.name,storage_path=excluded.storage_path,mime=excluded.mime,"
                        "byte_count=excluded.byte_count,updated_at=excluded.updated_at,results=excluded.results",
                        (value, title, path, mime, len(audio), created / 1000, time.time(), encoded_results),
                    )
                    row = get_row(store, value)
            except Exception:
                with suppress(Exception):
                    await delete_object(media, path)
                raise HTTPException(503, "Impossible d’enregistrer le test. Réessayez.") from None
            if old and PATH_PATTERN.fullmatch(old["storage_path"] or ""):
                with suppress(Exception):
                    await delete_object(media, old["storage_path"])
            return descriptor(row)

    @router.put("/api/experiments/{experiment_id}")
    async def update_experiment(request: Request, experiment_id: str):
        authorized(request)
        same_origin(request)
        value = identifier(experiment_id)
        body = await read_json(request, MAX_RESULTS_BYTES + 4096)
        if not body or set(body) - {"name", "results"}:
            raise HTTPException(422, "Indiquez le nom ou les résultats du test.")
        title = name(body["name"]) if "name" in body else None
        encoded_results = results(body["results"]) if "results" in body else None
        store = request.app.state.store
        async with mutations:
            with store.lock, store.db:
                old = get_row(store, value)
                if not old:
                    raise HTTPException(404, "Test introuvable.")
                store.db.execute(
                    "UPDATE experiments SET name=%s,results=%s,updated_at=%s WHERE id=%s",
                    (
                        title if title is not None else old["name"],
                        encoded_results if encoded_results is not None else old["results"],
                        time.time(),
                        value,
                    ),
                )
                return descriptor(get_row(store, value))

    @router.delete("/api/experiments/{experiment_id}")
    async def remove_experiment(request: Request, experiment_id: str):
        authorized(request)
        same_origin(request)
        value = identifier(experiment_id)
        store, media = request.app.state.store, request.app.state.media
        async with mutations:
            row = get_row(store, value)
            if not row:
                raise HTTPException(404, "Test introuvable.")
            path = checked_path(row)
            try:
                previous_audio = await media.get(path)
                await delete_object(media, path)
            except Exception:
                raise HTTPException(503, "Impossible de supprimer l’audio. Réessayez.") from None
            try:
                with store.lock, store.db:
                    store.db.execute("DELETE FROM experiments WHERE id=%s", (value,))
            except Exception:
                if previous_audio:
                    with suppress(Exception):
                        await media.put(path, previous_audio, row["mime"])
                raise HTTPException(503, "Impossible de supprimer le test. Réessayez.") from None
        return {"ok": True}

    @router.get("/api/experiments/{experiment_id}/audio")
    async def experiment_audio(request: Request, experiment_id: str):
        authorized(request)
        row = get_row(request.app.state.store, identifier(experiment_id))
        path = checked_path(row)
        data = await request.app.state.media.get(path)
        if not data:
            raise HTTPException(404, "Audio de test introuvable.")
        handle, filename = tempfile.mkstemp(prefix="wuumtaaba-experiment-")
        with os.fdopen(handle, "wb") as output:
            output.write(data)
        mime = row["mime"] if row["mime"] in MIMES else "application/octet-stream"
        return FileResponse(
            filename,
            media_type=mime,
            content_disposition_type="inline",
            background=BackgroundTask(os.unlink, filename),
        )
