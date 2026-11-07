"""Private, bounded RunPod serverless jobs; no platform-token proxy or GPU configuration."""

import asyncio
import json
import re
from contextlib import suppress

import httpx

from .clients import MediaError

API_BASE = "https://api.runpod.ai/v2"
MAX_RESPONSE_BYTES = 24 * 1024 * 1024
POLL_SECONDS = 1.0
_ENDPOINT = re.compile(r"[a-z0-9]{1,64}")
_JOB = re.compile(r"[A-Za-z0-9_-]{1,128}")


class RunPodClient:
    def __init__(self, settings, http):
        self.http = http
        base = getattr(settings, "runpod_api_base_url", API_BASE).rstrip("/")
        if base != API_BASE:
            raise ValueError("RunPod requires its fixed HTTPS API URL.")
        self.key = settings.runpod_api_key.get_secret_value()
        self.timeout = settings.runpod_timeout_seconds
        if not isinstance(self.timeout, (int, float)) or not 0 < self.timeout <= 600:
            raise ValueError("RunPod timeout must be between 0 and 600 seconds.")

    @staticmethod
    def endpoint(value):
        if not isinstance(value, str) or not _ENDPOINT.fullmatch(value):
            raise MediaError("Endpoint RunPod invalide ou non configuré.")
        return value

    async def _request(self, method, path, payload=None, timeout=None):
        if not self.key:
            raise MediaError("La clé RunPod n’est pas configurée sur le serveur.")
        try:
            async with self.http.stream(
                method,
                API_BASE + path,
                headers={"Authorization": "Bearer " + self.key},
                json=payload,
                timeout=timeout or self.timeout,
                follow_redirects=False,
            ) as response:
                if response.status_code in (401, 403):
                    raise MediaError("La clé RunPod est refusée pour cet endpoint.")
                if response.status_code == 404:
                    raise MediaError("Endpoint ou tâche RunPod introuvable.")
                if response.status_code == 429:
                    raise MediaError(
                        "La limite de requêtes ou le quota RunPod est atteint. Réessaie plus tard."
                    )
                if not 200 <= response.status_code < 300:
                    raise MediaError("Le service RunPod est momentanément indisponible.")
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > MAX_RESPONSE_BYTES:
                        raise MediaError("La réponse RunPod est trop volumineuse.")
                result = json.loads(data)
                if not isinstance(result, dict):
                    raise ValueError()
                return result
        except httpx.TimeoutException:
            raise MediaError("RunPod n’a pas répondu à temps. Réessaie.") from None
        except (httpx.HTTPError, ValueError) as error:
            if isinstance(error, MediaError):
                raise
            raise MediaError("Réponse RunPod indisponible ou invalide. Réessaie.") from None

    async def health(self, endpoint):
        """Read health only; never creates jobs, changes scaling or starts a worker."""
        endpoint = self.endpoint(endpoint)
        try:
            async with asyncio.timeout(min(10, self.timeout)):
                return await self._request("GET", f"/{endpoint}/health", timeout=min(10, self.timeout))
        except TimeoutError:
            raise MediaError("RunPod n’a pas répondu à temps. Réessaie.") from None

    async def _cancel(self, endpoint, job):
        with suppress(Exception):
            await asyncio.wait_for(self._request("POST", f"/{endpoint}/cancel/{job}", timeout=3), timeout=3)

    async def infer(self, endpoint, payload):
        """Submit once, poll to a terminal state, and cancel unfinished known jobs on exit."""
        endpoint = self.endpoint(endpoint)
        if not isinstance(payload, dict):
            raise MediaError("Entrée RunPod invalide.")
        job = None
        terminal = False
        try:
            async with asyncio.timeout(self.timeout):
                result = await self._request("POST", f"/{endpoint}/run", {"input": payload})
                while True:
                    candidate = result.get("id")
                    if candidate is not None:
                        if not isinstance(candidate, str) or not _JOB.fullmatch(candidate):
                            raise MediaError("Identifiant de tâche RunPod invalide.")
                        if job is not None and candidate != job:
                            raise MediaError("Réponse de tâche RunPod incohérente.")
                        job = candidate
                    status = result.get("status")
                    if status == "COMPLETED":
                        terminal = True
                        output = result.get("output")
                        if (
                            not isinstance(output, (dict, list))
                            or not output
                            or isinstance(output, dict)
                            and (output.get("success") is False or output.get("error"))
                        ):
                            raise MediaError("Le modèle RunPod n’a pas pu produire de résultat valide.")
                        return output
                    if status in {"FAILED", "CANCELLED", "TIMED_OUT"}:
                        terminal = True
                        raise MediaError("La tâche RunPod a échoué, a été annulée ou a expiré. Réessaie.")
                    if status not in {"IN_QUEUE", "IN_PROGRESS"} or not job:
                        raise MediaError("État de tâche RunPod invalide.")
                    await asyncio.sleep(POLL_SECONDS)
                    result = await self._request("GET", f"/{endpoint}/status/{job}")
        except TimeoutError:
            raise MediaError("L’analyse RunPod a dépassé le délai prévu. Réessaie.") from None
        finally:
            # Never retry submission after a lost response: the accepted job ID may be unknown.
            # A known unfinished job is cancelled even on caller cancellation or malformed status.
            if job and not terminal:
                await self._cancel(endpoint, job)
