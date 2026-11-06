"""Supabase Storage client for the private audio archive."""

from types import SimpleNamespace

import httpx

from bot_wumtaaba.config import Settings
from bot_wumtaaba.media import MediaStore


async def test_storage_uploads_privately_and_reports_missing_objects():
    seen = []

    def handler(request):
        seen.append(request)
        if request.method == "GET" and request.url.path.endswith("/missing.audio"):
            return httpx.Response(400, json={"statusCode": "404", "error": "not_found"})
        return httpx.Response(200, content=b"voice" if request.method == "GET" else b"{}")

    settings = SimpleNamespace(supabase_url="https://project.supabase.co/", media_bucket="wuumtaaba-media",
                               supabase_api_key=Settings(_env_file=None, supabase_api_key="sb_secret").supabase_api_key)
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        media = MediaStore(settings, http)
        await media.put("a.audio", b"voice", "audio/ogg")
        assert await media.get("a.audio") == b"voice"
        assert await media.get("missing.audio") is None
    upload = seen[0]
    assert str(upload.url) == "https://project.supabase.co/storage/v1/object/wuumtaaba-media/a.audio"
    assert upload.headers["apikey"] == "sb_secret" and upload.headers["x-upsert"] == "true"
    assert upload.headers["content-type"] == "audio/ogg"
