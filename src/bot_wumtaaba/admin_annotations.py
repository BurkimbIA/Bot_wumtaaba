"""Read-only, authenticated annotation inspection; no assignments or messages are sent."""

import json
import os
import re
import tempfile
from urllib.parse import quote

import httpx
from fastapi import HTTPException, Request
from fastapi.responses import FileResponse
from starlette.background import BackgroundTask

from .admin_data import _page, _search
from .tigris import TigrisAudioStore

AUDIO_PATH = re.compile(r"wuumtaaba/annotations/[0-9a-f]{64}\.audio")
MIMES = {"audio/flac", "audio/x-flac", "audio/wav", "audio/x-wav", "audio/ogg", "audio/mpeg", "audio/mp4"}


def _object(value):
    try:
        parsed = json.loads(value)
        return parsed if isinstance(parsed, dict) else {}
    except (ValueError, TypeError):
        return {}


def _sample(value):
    """Project display fields only: unknown provider metadata never reaches the browser."""
    source = _object(value)
    sample = {
        key: source[key]
        for key in ("id", "dataset", "revision", "sha256")
        if isinstance(source.get(key), str)
    }
    proposals = source.get("proposals")
    sample["proposals"] = {}
    if isinstance(proposals, dict):
        for label in ("A", "B"):
            proposal = proposals.get(label)
            if isinstance(proposal, dict):
                sample["proposals"][label] = {
                    key: proposal[key] for key in ("text", "model_id") if isinstance(proposal.get(key), str)
                }
    return sample


def _identifier(value):
    if not isinstance(value, str) or not re.fullmatch(r"[A-Za-z0-9_.:-]{1,120}", value):
        raise HTTPException(404, "Annotation introuvable.")
    return value


def install_admin_annotations(router, settings, authorized):
    def task_row(request, task_id):
        task_id = _identifier(task_id)
        store = request.app.state.store
        with store.lock:
            row = store.db.execute("SELECT * FROM annotation_tasks WHERE task_id=%s", (task_id,)).fetchone()
        if not row:
            raise HTTPException(404, "Annotation introuvable.")
        return row

    @router.get("/api/annotations")
    async def annotations(request: Request, q: str = "", page: int = 1):
        authorized(request)
        where = " WHERE t.task_id LIKE %s ESCAPE '\\'"
        return _page(
            request.app.state.store,
            "SELECT t.task_id,t.kind,t.enabled,t.created_at,"
            "(SELECT COUNT(*) FROM annotation_assignments a WHERE a.task_id=t.task_id) assignment_count,"
            "(SELECT COUNT(*) FROM annotation_responses r WHERE r.task_id=t.task_id) response_count "
            "FROM annotation_tasks t" + where + " ORDER BY t.created_at DESC,t.task_id",
            "SELECT COUNT(*) FROM annotation_tasks t" + where,
            (_search(q),),
            page,
        )

    @router.get("/api/annotations/{task_id}")
    async def annotation(request: Request, task_id: str):
        authorized(request)
        row = task_row(request, task_id)
        task = {
            key: row[key]
            for key in ("task_id", "kind", "enabled", "created_at", "audio_mime", "audio_size_bytes")
        }
        task["sample"] = _sample(row["sample"])
        task["audio_url"] = "/admin/api/annotations/" + quote(row["task_id"], safe="") + "/audio"
        store = request.app.state.store
        with store.lock:
            assignments = [
                dict(item)
                for item in store.db.execute(
                    "SELECT recipient,assigned_at,status FROM annotation_assignments "
                    "WHERE task_id=%s ORDER BY assigned_at,recipient",
                    (row["task_id"],),
                )
            ]
            responses = [
                dict(item)
                for item in store.db.execute(
                    "SELECT recipient,preference,ratings,correction,status,created_at,updated_at "
                    "FROM annotation_responses WHERE task_id=%s ORDER BY updated_at DESC,recipient",
                    (row["task_id"],),
                )
            ]
        for response in responses:
            ratings = _object(response["ratings"])
            response["ratings"] = {
                label: ratings[label]
                for label in ("A", "B")
                if type(ratings.get(label)) is int and 1 <= ratings[label] <= 5
            }
        return {"task": task, "assignments": assignments, "responses": responses}

    @router.get("/api/annotations/{task_id}/audio")
    async def annotation_audio(request: Request, task_id: str):
        authorized(request)
        row = task_row(request, task_id)
        path = row["audio_path"]
        if not isinstance(path, str) or not AUDIO_PATH.fullmatch(path) or row["audio_mime"] not in MIMES:
            raise HTTPException(404, "Audio d’annotation introuvable.")
        try:
            media = getattr(request.app.state, "annotation_media", None)
            if media is not None:
                data = await media.get(path)
            else:
                async with httpx.AsyncClient(timeout=30, follow_redirects=False) as http:
                    data = await TigrisAudioStore(settings, http).get(path)
        except (httpx.HTTPError, ValueError):
            raise HTTPException(502, "Audio temporairement indisponible.") from None
        if not data or len(data) > 25_000_000:
            raise HTTPException(404, "Audio d’annotation introuvable.")
        descriptor, filename = tempfile.mkstemp(prefix="wuumtaaba-annotation-")
        with os.fdopen(descriptor, "wb") as output:
            output.write(data)
        return FileResponse(
            filename,
            media_type=row["audio_mime"],
            content_disposition_type="inline",
            background=BackgroundTask(os.unlink, filename),
        )
