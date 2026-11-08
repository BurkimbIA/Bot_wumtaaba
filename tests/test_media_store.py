"""Bot audio keeps stable database paths while using private Tigris objects."""

import httpx
import pytest

from bot_wumtaaba.config import Settings
from bot_wumtaaba.media import MediaStore


@pytest.mark.parametrize("name", ["a" * 64 + ".audio", "experiments/" + "b" * 32 + ".audio"])
async def test_storage_maps_upload_read_and_delete_to_private_tigris(name):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, content=b"voice" if request.method == "GET" else b"")

    settings = Settings(_env_file=None, aws_access_key_id="test-key", aws_secret_access_key="secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        media = MediaStore(settings, http)
        await media.put(name, b"voice", "audio/ogg")
        assert await media.get(name) == b"voice"
        await media.delete(name)
    assert [request.method for request in seen] == ["PUT", "GET", "DELETE"]
    assert all(str(request.url) == "https://t3.storage.dev/burkimbia-store/wuumtaaba/media/" + name
               for request in seen)
    assert all(request.headers["authorization"].startswith("AWS4-HMAC-SHA256 ") for request in seen)
    assert seen[0].headers["content-type"] == "audio/ogg"


async def test_missing_tigris_object_is_not_read_from_supabase():
    def handler(request):
        assert request.url.host == "t3.storage.dev"
        return httpx.Response(404)

    settings = Settings(_env_file=None, aws_access_key_id="test-key", aws_secret_access_key="secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await MediaStore(settings, http).get("a" * 64 + ".audio") is None


@pytest.mark.parametrize("name", ["../audio", "wuumtaaba/annotations/a.audio", "a.audio", "experiments/../a.audio"])
async def test_invalid_database_paths_are_rejected_before_network(name):
    def handler(request):
        pytest.fail("invalid path reached storage")

    settings = Settings(_env_file=None, aws_access_key_id="test-key", aws_secret_access_key="secret")
    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        media = MediaStore(settings, http)
        for action in (lambda: media.put(name, b"voice", "audio/ogg"),
                       lambda: media.get(name), lambda: media.delete(name)):
            with pytest.raises(ValueError, match="Invalid bot audio path"):
                await action()
