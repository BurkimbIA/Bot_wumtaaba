"""Admin-only experimental inference, independent of the WhatsApp model selection."""

import base64
import binascii
import json
import time

import httpx
from fastapi import HTTPException, Request

from .runpod import RunPodClient
from .clients import Intelligence, MediaError

MAX_AUDIO_BYTES = 7 * 1024 * 1024
MAX_BODY_BYTES = 4 * ((MAX_AUDIO_BYTES + 2) // 3) + 65536
MAC_MODELS = {
    "transcription": {
        "burkimbia/BIA-WHISPER-LARGE-SACHI_V2": "Whisper Large · SACHI V2",
        "burkimbia/wav2vec-mos": "wav2vec-mos · PyTorch · CPU",
    },
    "translation": {
        "burkimbia/BIA-NLLB-600M-10E-CD-CRCL": "NLLB 600M · 10E-CD-CRCL",
        "burkimbia/BIA-NLLB-1.3B-8E-CD-CRCL-RVW-DT": "NLLB 1.3B · 8E-CD-CRCL-RVW-DT",
        "burkimbia/BIA-MISTRAL-7B-SACHI_merged": "Mistral 7B · SACHI",
        "burkimbia/tengsoaba-4b": "Tengsoaba 4B · instruction de traduction",
        "burkimbia/yaaba-4B": "Yaaba 4B · instruction de traduction",
    },
}
CLOUD_MODELS = {
    "transcribe": {"bia-transcription-v1"},
    "translate": set(),
}


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


def _payload(body, provider, task):
    if set(body) - {"model", "model_id", "audio_base64", "text", "source"}:
        raise HTTPException(422, "Paramètre d’analyse inconnu.")
    alias = "bia-transcription-v1" if task == "transcribe" else "bia-translation-v1-fast"
    if provider == "cloud":
        if ("model_id" in body or not isinstance(body.get("model", alias), str)
                or body.get("model", alias) not in CLOUD_MODELS[task]):
            raise HTTPException(422, "Modèle cloud incompatible avec cette tâche.")
        model = body.get("model", alias)
    else:
        group = "transcription" if task == "transcribe" else "translation"
        model = body.get("model_id", next(iter(MAC_MODELS[group])))
        if not isinstance(model, str) or model not in MAC_MODELS[group]:
            raise HTTPException(422, "Modèle Mac incompatible avec cette tâche.")
    if task == "transcribe":
        encoded = body.get("audio_base64")
        try:
            if not isinstance(encoded, str) or len(encoded) > 4 * ((MAX_AUDIO_BYTES + 2) // 3):
                raise ValueError()
            audio = base64.b64decode(encoded, validate=True)
            if not 0 < len(audio) <= MAX_AUDIO_BYTES:
                raise ValueError()
        except (ValueError, binascii.Error):
            raise HTTPException(422, "Audio invalide : 7 Mo maximum.") from None
        payload = {"audio_base64": encoded, "model": alias,
                   "language": "moore" if provider == "mac" else "mos", "return_timestamps": False}
    else:
        text, source = body.get("text"), body.get("source", "moore")
        if not isinstance(text, str) or not 1 <= len(text.strip()) <= 512 or len(text) > 512:
            raise HTTPException(422, "Texte requis : 1 à 512 caractères.")
        if not isinstance(source, str) or source not in {"moore", "french"}:
            raise HTTPException(422, "Langue source inconnue.")
        payload = {"text": text.strip(), "src_lang": source,
                   "tgt_lang": "french" if source == "moore" else "moore", "model": alias}
    if provider == "mac":
        payload["model_id"] = model
    else:
        payload["model"] = model
    return payload, model


def runpod_text(output):
    for _ in range(4):
        if not isinstance(output, dict) or output.get("success") is False or output.get("error"):
            raise MediaError("Le modèle RunPod n’a pas pu produire de transcription valide.")
        if isinstance(output.get("text"), str):
            return output["text"]
        output = output.get("output")
    raise MediaError("Réponse de transcription RunPod invalide.")


def install_check_inference(router, settings, authorized, same_origin):
    @router.get("/api/check/models")
    async def models(request: Request):
        authorized(request)
        # Supported checkpoints, not a filesystem inspection of the remote Mac.
        return {**{group: [{"id": identifier, "label": label, "available": True, "default": i == 0}
                           for i, (identifier, label) in enumerate(items.items())]
                   for group, items in MAC_MODELS.items()},
                "cloud_transcription": [{"id": "bia-transcription-v1", "label": "Whisper Large · RunPod direct",
                    "available": bool(settings.runpod_api_key.get_secret_value() and settings.runpod_transcription_endpoint_id), "default": True}],
                "cloud_translation": [],
                "cloud_note": "Whisper utilise directement RunPod. Aucun modèle de traduction RunPod activé : NLLB absent, Mistral et Tengsoaba exclus.",
                "availability_verified": False,
                "note": "Modèles compatibles. Le Mac vérifie leur présence sans téléchargement lors de l’analyse."}

    @router.get("/api/check/status")
    async def status(request: Request):
        authorized(request)
        bridge = getattr(request.app.state, "model_bridge", None)
        return {"mac": {"status": "ok" if bridge and bridge.mac_online else "offline"},
                "cloud_configured": bool(settings.runpod_api_key.get_secret_value() and settings.runpod_transcription_endpoint_id)}

    @router.post("/api/check/run/{provider}/{task}")
    async def run(request: Request, provider: str, task: str):
        authorized(request)
        same_origin(request)
        if provider not in {"mac", "cloud"} or task not in CLOUD_MODELS:
            raise HTTPException(404, "Choix inconnu.")
        if provider == "cloud" and task == "translate":
            raise HTTPException(503, "Traduction RunPod indisponible : aucun endpoint de traduction activé. Choisissez le Mac pour une analyse complète.")
        body = await read_json(request, MAX_BODY_BYTES if task == "transcribe" else 8192)
        payload, model = _payload(body, provider, task)
        started = time.perf_counter()
        try:
            if provider == "mac":
                bridge = getattr(request.app.state, "model_bridge", None)
                if bridge is None:
                    raise MediaError("Connecteur Mac indisponible.")
                # Never select(): an experiment must not change the live bot's provider.
                result = await bridge.submit("/" + task, payload)
            else:
                async with httpx.AsyncClient(follow_redirects=False) as http:
                    output = await RunPodClient(settings, http).infer(settings.runpod_transcription_endpoint_id, {
                        "audio_base64": payload["audio_base64"], "language": "mos", "return_timestamps": False,
                    })
                    result = {"text": runpod_text(output)}
            field = "text" if task == "transcribe" else "output"
            text = result.get(field) if isinstance(result, dict) else None
            if (not isinstance(result, dict) or result.get("success") is False
                    or not isinstance(text, str) or not text.strip() or len(text) > 8000):
                raise MediaError("Réponse du modèle vide ou invalide.")
            return {"ok": True, "text": text.strip(), "model": model,
                    "seconds": round(time.perf_counter() - started, 3)}
        except MediaError as error:
            return {"ok": False, "error": str(error)[:500],
                    "seconds": round(time.perf_counter() - started, 3)}

    @router.post("/api/check/extract")
    async def extract(request: Request):
        authorized(request)
        same_origin(request)
        body = await read_json(request, 65536)
        text = body.get("text")
        if set(body) != {"text"} or not isinstance(text, str) or not 1 <= len(text.strip()) <= 8000:
            raise HTTPException(422, "Texte français requis : 1 à 8 000 caractères.")
        try:
            async with httpx.AsyncClient(follow_redirects=False) as http:
                draft = await Intelligence(settings, http).extract(text.strip(), {})
            return {"draft": draft.model_dump(mode="json"), "model": settings.extraction_model}
        except (MediaError, ValueError):
            raise HTTPException(502, "Impossible d’extraire les détails. Réessayez.") from None
