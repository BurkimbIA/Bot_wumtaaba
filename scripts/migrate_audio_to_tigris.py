"""Copy Supabase audio to private Tigris without deleting or changing database references.

Default is a read-only inventory. --apply transfers bytes in memory, refuses conflicting
Tigris objects, and verifies exact SHA-256 and private access for every object.
"""

import argparse
import asyncio
import hashlib
import json
from urllib.parse import quote, urlsplit

import httpx
import psycopg

from bot_wumtaaba.config import Settings
from bot_wumtaaba.tigris import MAX_AUDIO_BYTES, TigrisAudioStore


def _object(row):
    name, metadata = row["name"], row.get("metadata") or {}
    if (not isinstance(name, str) or not name or any(c in name for c in "\\?#%")
            or any(part in {"", ".", ".."} for part in name.split("/"))
            or any(ord(c) < 32 or ord(c) == 127 for c in name)):
        raise ValueError("Invalid source object key")
    if isinstance(metadata, str):
        metadata = json.loads(metadata)
    size = metadata.get("size")
    if size is not None and (type(size) is not int or not 0 < size <= MAX_AUDIO_BYTES):
        raise ValueError("Invalid source audio size")
    return name, size


async def migrate_audio(settings, rows, http, tigris=None, *, apply=False):
    """Inventory or verified copy; never write files, database rows, or source objects."""
    objects = [_object(row) for row in rows]
    if len({name for name, _ in objects}) != len(objects):
        raise ValueError("Duplicate source object keys")
    summary = {
        "mode": "apply" if apply else "read_only_plan",
        "objects": len(objects),
        "source_metadata_bytes": sum(size or 0 for _, size in objects),
        "objects_without_size": sum(size is None for _, size in objects),
        "verified_objects": 0,
        "verified_bytes": 0,
        "local_audio_writes": False,
        "source_deletes": False,
        "database_writes": False,
        "whatsapp_sends": False,
    }
    if not apply:
        return summary
    endpoint = urlsplit(settings.supabase_url)
    if (endpoint.scheme != "https" or not endpoint.hostname or endpoint.username or endpoint.password
            or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
        raise ValueError("Invalid Supabase source endpoint")
    bucket = settings.media_bucket
    if not bucket or any(c not in "abcdefghijklmnopqrstuvwxyzABCDEFGHIJKLMNOPQRSTUVWXYZ0123456789-_" for c in bucket):
        raise ValueError("Invalid Supabase source bucket")
    secret = settings.supabase_api_key.get_secret_value()
    if not secret:
        raise ValueError("Missing Supabase source credentials")
    if tigris is None:
        tigris = TigrisAudioStore(settings, http)
    for name, expected_size in objects:
        url = settings.supabase_url.rstrip("/") + "/storage/v1/object/" + bucket + "/" + quote(name, safe="/-_.~")
        request = httpx.Request("GET", url, headers={"apikey": secret, "Authorization": "Bearer " + secret})
        response = await http.send(request, auth=None, follow_redirects=False, stream=True)
        try:
            response.raise_for_status()
            audio = bytearray()
            async for chunk in response.aiter_bytes():
                audio.extend(chunk)
                if len(audio) > MAX_AUDIO_BYTES:
                    raise ValueError("Source audio exceeds size limit")
            mime = response.headers.get("content-type", "application/octet-stream")
        finally:
            await response.aclose()
        if not audio or (expected_size is not None and len(audio) != expected_size):
            raise ValueError("Source audio size differs from inventory")
        data = bytes(audio)
        digest = hashlib.sha256(data).digest()
        key = "wuumtaaba/media/" + name
        existing = await tigris.get(key)
        if existing is not None and hashlib.sha256(existing).digest() != digest:
            raise ValueError("Conflicting Tigris object; no overwrite performed")
        if existing is None:
            await tigris.put(key, data, mime)
        restored = await tigris.get(key)
        if restored is None or hashlib.sha256(restored).digest() != digest:
            raise ValueError("Tigris audio round-trip verification failed")
        await tigris.verify_private(key)
        summary["verified_objects"] += 1
        summary["verified_bytes"] += len(data)
    return summary


async def main(apply=False):
    settings = Settings()
    with psycopg.connect(settings.database_url.get_secret_value(), autocommit=True) as db:
        db.execute("SET default_transaction_read_only=on")
        rows = db.execute(
            "SELECT name,metadata FROM storage.objects WHERE bucket_id=%s ORDER BY name", (settings.media_bucket,)
        ).fetchall()
    sources = [{"name": name, "metadata": metadata} for name, metadata in rows]
    async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
        print(json.dumps(await migrate_audio(settings, sources, http, apply=apply)))


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    try:
        asyncio.run(main(parser.parse_args().apply))
    except Exception as error:
        # Provider exception strings can contain object URLs; keep operator output free of PII.
        print(json.dumps({"status": "failed", "error_type": type(error).__name__}))
        raise SystemExit(1) from None
