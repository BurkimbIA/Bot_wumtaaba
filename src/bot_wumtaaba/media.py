"""Private audio archive: generated filenames only, atomic writes, no public URL."""

import hashlib
import os
import tempfile
from pathlib import Path


def archive_audio(directory: Path, message_id: str, data: bytes, mime: str) -> dict:
    directory = Path(directory)
    directory.mkdir(parents=True, exist_ok=True, mode=0o700)
    digest = hashlib.sha256(data).hexdigest()
    # Stable per inbound message and content, so provider retries reuse the same file.
    filename = hashlib.sha256((message_id + digest).encode()).hexdigest() + ".audio"
    descriptor, temporary = tempfile.mkstemp(prefix=".audio-", dir=directory)
    try:
        with os.fdopen(descriptor, "wb") as output:
            os.fchmod(output.fileno(), 0o600)
            output.write(data)
            output.flush()
            os.fsync(output.fileno())
        os.replace(temporary, directory / filename)
    finally:
        if os.path.exists(temporary):
            os.unlink(temporary)
    return {
        "mime_type": mime, "sha256": digest, "byte_count": len(data),
        "storage_path": filename, "status": "archived",
    }
