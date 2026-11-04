"""Private audio archive in a Supabase Storage bucket: generated names only, no public URL."""

import hashlib
import os
import tempfile
from pathlib import Path


class MediaStore:
    """Private bucket behind the server key; viewers only reach audio through /admin."""

    def __init__(self, settings, http):
        self.http = http
        self.url = f"{settings.supabase_url.rstrip('/')}/storage/v1/object/{settings.media_bucket}/"
        key = settings.supabase_api_key.get_secret_value()
        self.headers = {"apikey": key, "Authorization": "Bearer " + key}

    async def put(self, name, data, mime):
        response = await self.http.post(self.url + name, content=data, headers={
            **self.headers, "Content-Type": mime or "application/octet-stream", "x-upsert": "true"})
        response.raise_for_status()

    async def get(self, name):
        response = await self.http.get(self.url + name, headers=self.headers)
        # Storage answers 400 with a 404 body for a missing object.
        if response.status_code in {400, 404}:
            return None
        response.raise_for_status()
        return response.content


async def archive_audio(media, message_id: str, data: bytes, mime: str) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    # Stable per inbound message and content, so provider retries reuse the same object.
    filename = hashlib.sha256((message_id + digest).encode()).hexdigest() + ".audio"
    await media.put(filename, data, mime)
    return {
        "mime_type": mime, "sha256": digest, "byte_count": len(data),
        "storage_path": filename, "status": "archived",
    }


async def encode_whatsapp_audio(data: bytes, mime: str) -> tuple[bytes, str, str]:
    """Convert provider WAV to MP3 in an isolated temporary directory, without a shell."""
    import asyncio
    import shutil

    from .clients import MediaError

    if not data or len(data) > 25_000_000:
        raise MediaError("La voix générée est vide ou trop volumineuse.")
    if mime == "audio/mpeg":
        return data, "burkimbia.mp3", mime
    if mime not in {"audio/wav", "audio/x-wav"} or not data.startswith(b"RIFF"):
        raise MediaError("Format audio BurkimbIA inattendu.")
    binary = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")
    if not binary:
        raise MediaError("La conversion audio est momentanément indisponible.")
    with tempfile.TemporaryDirectory(prefix="wuumtaaba-voice-") as directory:
        source, output = Path(directory) / "source.wav", Path(directory) / "voice.mp3"
        source.write_bytes(data)
        source.chmod(0o600)
        process = await asyncio.create_subprocess_exec(
            binary, "-nostdin", "-hide_banner", "-loglevel", "error", "-y",
            "-i", str(source), "-t", "120", "-vn", "-ac", "1", "-ar", "24000",
            "-codec:a", "libmp3lame", "-b:a", "64k", str(output),
            stdout=asyncio.subprocess.DEVNULL, stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        if process.returncode or not output.exists() or not output.stat().st_size:
            raise MediaError("Impossible de convertir cette voix pour WhatsApp.")
        return output.read_bytes(), "burkimbia.mp3", "audio/mpeg"
