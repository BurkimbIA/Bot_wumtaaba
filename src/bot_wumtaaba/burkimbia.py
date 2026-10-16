"""Private server-side client for the BurkimbIA platform's native inference API."""

import base64
import binascii
import json
from urllib.parse import urlsplit

import httpx

from .clients import MediaError
from .config import Settings
from .media import encode_whatsapp_audio


class BurkimbIA:
    def __init__(self, settings: Settings, http: httpx.AsyncClient, bridge=None):
        self.s, self.http = settings, http
        self.bridge = bridge
        self.base = settings.burkimbia_api_url.rstrip("/")
        url = urlsplit(self.base)
        if (
            url.scheme != "https"
            or not url.hostname
            or url.username
            or url.password
            or url.query
            or url.fragment
        ):
            raise ValueError("BurkimbIA requires an HTTPS API URL without credentials or query.")

    async def _request(self, route: str, payload: dict) -> dict:
        if self.bridge is not None and self.bridge.provider == "mac":
            payload = dict(payload)
            if route == "/transcribe":
                payload["language"] = "moore"
            result = await self.bridge.submit(route, payload)
            if result.get("success") is False:
                raise MediaError("Le modèle sur le Mac n’a pas pu produire de résultat.")
            return result
        key = self.s.burkimbia_api_key.get_secret_value()
        if not key:
            raise MediaError("BurkimbIA n’est pas encore configuré. Réessaie plus tard.")
        try:
            async with self.http.stream(
                "POST",
                self.base + route,
                headers={"X-API-Key": key},
                json=payload,
                timeout=self.s.burkimbia_timeout_seconds,
                follow_redirects=False,
            ) as response:
                if response.status_code == 429:
                    raise MediaError("Le quota BurkimbIA est atteint. Réessaie plus tard.")
                if response.status_code in (401, 403):
                    raise MediaError("L’accès BurkimbIA doit être renouvelé par l’administrateur.")
                if response.status_code in (400, 413, 422):
                    raise MediaError(
                        "BurkimbIA ne peut pas traiter ce contenu. Essaie un texte ou un audio plus court (60 secondes maximum)."
                    )
                if not 200 <= response.status_code < 300:
                    raise MediaError(
                        "BurkimbIA est momentanément indisponible. Réessaie plus tard."
                    )
                data = bytearray()
                async for chunk in response.aiter_bytes():
                    data.extend(chunk)
                    if len(data) > 24 * 1024 * 1024:
                        raise MediaError("La réponse BurkimbIA est trop volumineuse.")
                result = json.loads(data)
                if not isinstance(result, dict) or result.get("success") is False:
                    raise MediaError("BurkimbIA n’a pas pu produire de résultat. Réessaie.")
                return result
        except httpx.TimeoutException:
            raise MediaError(
                "Le modèle BurkimbIA met trop de temps à répondre. Réessaie dans quelques instants."
            ) from None
        except (httpx.HTTPError, ValueError) as exc:
            if isinstance(exc, MediaError):
                raise
            raise MediaError("Réponse BurkimbIA indisponible ou invalide. Réessaie.") from None

    @staticmethod
    def _text(text: str) -> str:
        if not isinstance(text, str) or not text.strip() or len(text) > 512:
            raise MediaError("Envoie un texte de 1 à 512 caractères.")
        return text.strip()

    @staticmethod
    def _output(result: dict, field: str) -> str:
        text = result.get(field)
        if not isinstance(text, str) or not text.strip() or len(text) > 20000:
            raise MediaError("BurkimbIA a renvoyé un résultat vide ou invalide.")
        return text.strip()

    async def translate(self, text: str, source: str = "french", target: str = "moore") -> str:
        if {source, target} != {"french", "moore"}:
            raise MediaError("La traduction prend en charge le français et le mooré.")
        result = await self._request(
            "/translate",
            {
                "text": self._text(text),
                "src_lang": source,
                "tgt_lang": target,
                "model": self.s.burkimbia_translation_model,
            },
        )
        return self._output(result, "output")

    async def transcribe(
        self, data: bytes, filename: str, mime: str, language: str = "moore"
    ) -> str:
        if language != "moore":
            raise MediaError("Ce modèle de transcription BurkimbIA est destiné au mooré.")
        if not data or len(data) > min(self.s.max_audio_bytes, 7 * 1024 * 1024):
            raise MediaError("Envoie un vocal court, de moins de 60 secondes et 7 Mo.")
        result = await self._request(
            "/transcribe",
            {
                "audio_base64": base64.b64encode(data).decode(),
                "language": "mos",
                "model": "bia-transcription-v1",
                "return_timestamps": False,
            },
        )
        return self._output(result, "text")

    async def synthesize(self, text: str, language: str = "moore") -> tuple[bytes, str, str]:
        if language != "moore":
            raise MediaError("La synthèse vocale BurkimbIA est destinée au mooré.")
        result = await self._request(
            "/tts",
            {
                "text": self._text(text),
                "model": "bia-tts-v1",
                "voice_cloning": False,
            },
        )
        wav = result.get("wav")
        if not isinstance(wav, str) or not wav:
            raise MediaError("BurkimbIA n’a pas renvoyé de fichier audio.")
        try:
            audio = base64.b64decode(wav, validate=True)
        except (ValueError, binascii.Error):
            raise MediaError("Le fichier audio BurkimbIA est invalide.") from None
        if not audio.startswith(b"RIFF") or audio[8:12] != b"WAVE":
            raise MediaError("Le fichier audio BurkimbIA n’est pas un WAV valide.")
        return await encode_whatsapp_audio(audio, "audio/wav")

    @property
    def yaaba_available(self) -> bool:
        return self.bridge is not None and self.bridge.provider == "mac"

    async def chat(self, text: str, history: list) -> str:
        # The cloud OpenAI-compatible route is translation, not Yaaba.
        if not self.yaaba_available:
            raise MediaError("Yaaba est disponible avec les modèles du Mac. Sélectionne ce mode dans le back-office.")
        messages = []
        remaining = 3500
        for item in reversed(history[-10:]):
            if not isinstance(item, dict) or item.get("role") not in {"user", "assistant"}:
                continue
            content = item.get("content")
            if not isinstance(content, str) or not content.strip():
                continue
            content = content[:512]
            if len(content) > remaining:
                break
            remaining -= len(content)
            messages.insert(0, {"role": item["role"], "content": content})
        messages.append({"role": "user", "content": self._text(text)})
        result = await self.bridge.submit("/chat", {
            "model": "tengsoaba-4b", "messages": messages, "max_tokens": 256, "stream": False,
        })
        try:
            return self._output(result["choices"][0]["message"], "content")
        except (KeyError, IndexError, TypeError):
            raise MediaError("Yaaba a renvoyé une réponse invalide.") from None
