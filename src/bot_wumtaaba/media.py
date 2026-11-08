"""Private audio archive in Tigris: logical database paths remain stable."""

import hashlib
import os
import re
import tempfile
from pathlib import Path

from .tigris import TigrisAudioStore


class MediaStore(TigrisAudioStore):
    """Store bot and experiment audio under the private Wuumtaaba prefix."""

    @staticmethod
    def object_key(name):
        if not isinstance(name, str) or not re.fullmatch(
            r"(?:[0-9a-f]{64}|experiments/[0-9a-f]{32})\.audio", name
        ):
            raise ValueError("Invalid bot audio path")
        return "wuumtaaba/media/" + name

    async def put(self, name, data, mime):
        await super().put(self.object_key(name), data, mime)

    async def get(self, name):
        return await super().get(self.object_key(name))

    async def delete(self, name):
        await super().delete(self.object_key(name))

    async def verify_private(self, name):
        await super().verify_private(self.object_key(name))


async def archive_audio(media, message_id: str, data: bytes, mime: str) -> dict:
    digest = hashlib.sha256(data).hexdigest()
    # Stable per inbound message and content, so provider retries reuse the same object.
    filename = hashlib.sha256((message_id + digest).encode()).hexdigest() + ".audio"
    await media.put(filename, data, mime)
    return {
        "mime_type": mime,
        "sha256": digest,
        "byte_count": len(data),
        "storage_path": filename,
        "status": "archived",
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
            binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-i",
            str(source),
            "-t",
            "120",
            "-vn",
            "-ac",
            "1",
            "-ar",
            "24000",
            "-codec:a",
            "libmp3lame",
            "-b:a",
            "64k",
            str(output),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
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


async def join_voice_parts(parts):
    """Join short MP3 readbacks without truncating any key field."""
    import asyncio
    import shutil

    from .clients import MediaError

    if len(parts) == 1:
        return parts[0]
    if not parts or len(parts) > 10 or any(p[2] != "audio/mpeg" for p in parts):
        raise MediaError("La lecture de ce résumé est trop longue ou invalide.")
    binary = os.environ.get("FFMPEG_BINARY") or shutil.which("ffmpeg")
    if not binary:
        raise MediaError("La conversion audio est momentanément indisponible.")
    with tempfile.TemporaryDirectory(prefix="wuumtaaba-summary-") as directory:
        root = Path(directory)
        for i, part in enumerate(parts):
            (root / f"part-{i}.mp3").write_bytes(part[0])
        (root / "parts.txt").write_text(
            "\n".join(f"file 'part-{i}.mp3'" for i in range(len(parts)))
        )
        process = await asyncio.create_subprocess_exec(
            binary,
            "-nostdin",
            "-hide_banner",
            "-loglevel",
            "error",
            "-y",
            "-f",
            "concat",
            "-safe",
            "1",
            "-i",
            str(root / "parts.txt"),
            "-c:a",
            "copy",
            str(root / "summary.mp3"),
            stdout=asyncio.subprocess.DEVNULL,
            stderr=asyncio.subprocess.DEVNULL,
        )
        try:
            await asyncio.wait_for(process.wait(), timeout=30)
        except (TimeoutError, asyncio.CancelledError):
            process.kill()
            await process.wait()
            raise
        result = root / "summary.mp3"
        if process.returncode or not result.exists() or not 0 < result.stat().st_size <= 14_000_000:
            raise MediaError("Impossible de préparer la lecture du résumé.")
        return result.read_bytes(), "resume.mp3", "audio/mpeg"
