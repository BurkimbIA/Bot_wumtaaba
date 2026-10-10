import base64
import hashlib
import json

import httpx
import pytest
from pydantic import ValidationError

from bot_wumtaaba.clients import Intelligence, MediaError, WhatsApp
from bot_wumtaaba.config import Settings


def settings(**kwargs):
    return Settings(
        _env_file=None,
        whatsapp_token="test-meta",
        openai_api_key="test-ai",
        whatsapp_phone_number_id="123",
        **kwargs,
    )


@pytest.mark.parametrize("encoding", ["hex", "base64"])
async def test_download_validates_and_returns_voice(encoding):
    data = b"test audio"
    digest = hashlib.sha256(data)
    checksum = (
        digest.hexdigest() if encoding == "hex" else base64.b64encode(digest.digest()).decode()
    )
    requests = []

    def handler(request):
        requests.append(request)
        assert request.headers["authorization"] == "Bearer test-meta"
        if request.url.host == "graph.facebook.com":
            assert request.url.params["phone_number_id"] == "123"
            return httpx.Response(
                200,
                json={
                    "url": "https://lookaside.fbsbx.com/audio",
                    "mime_type": "audio/ogg; codecs=opus",
                    "sha256": checksum,
                },
            )
        return httpx.Response(200, content=data)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await WhatsApp(settings(), http).audio("456") == (data, "vocal.ogg", "audio/ogg")
    assert len(requests) == 2


@pytest.mark.parametrize(
    "url",
    [
        "http://lookaside.fbsbx.com/a",
        "https://evil.test/a",
        "https://lookaside.fbsbx.com.evil.test/a",
        "https://user@lookaside.fbsbx.com/a",
        "https://lookaside.fbsbx.com:444/a",
    ],
)
async def test_never_sends_token_to_untrusted_media_host(url):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(200, json={"url": url, "mime_type": "audio/ogg"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="Hôte"):
            await WhatsApp(settings(), http).audio("456")
    assert len(seen) == 1


@pytest.mark.parametrize(
    "metadata,body,error",
    [
        ({"file_size": 1025}, b"x", "volumineux"),
        ({}, b"x" * 1025, "volumineux"),
        ({}, b"", "vide"),
        ({"sha256": "bad-hash"}, b"x", "incomplet"),
        ({"mime_type": "text/html"}, b"x", "Format"),
    ],
)
async def test_rejects_invalid_audio(metadata, body, error):
    def handler(request):
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200,
                json={"url": "https://lookaside.fbsbx.com/a", "mime_type": "audio/ogg", **metadata},
            )
        return httpx.Response(200, content=body)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match=error):
            await WhatsApp(settings(max_audio_bytes=1024), http).audio("456")


async def test_media_redirect_is_not_followed():
    seen = []

    def handler(request):
        seen.append(request.url.host)
        if request.url.host == "graph.facebook.com":
            return httpx.Response(
                200, json={"url": "https://lookaside.fbsbx.com/a", "mime_type": "audio/ogg"}
            )
        return httpx.Response(302, headers={"Location": "https://evil.test/"})

    async with httpx.AsyncClient(
        transport=httpx.MockTransport(handler), follow_redirects=True
    ) as http:
        with pytest.raises(httpx.HTTPStatusError):
            await WhatsApp(settings(), http).audio("456")
    assert seen == ["graph.facebook.com", "lookaside.fbsbx.com"]


async def test_transcription_is_french_multipart():
    def handler(request):
        assert request.url.path == "/v1/audio/transcriptions"
        assert "multipart/form-data" in request.headers["content-type"]
        assert b'name="language"\r\n\r\nfr' in request.content
        assert b'filename="vocal.ogg"' in request.content
        assert b"voice-bytes" in request.content
        return httpx.Response(200, json={"text": "  Bonjour monsieur  "})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (
            await Intelligence(settings(), http).transcribe(
                b"voice-bytes", "vocal.ogg", "audio/ogg"
            )
            == "Bonjour monsieur"
        )


async def test_empty_transcription_rejected():
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda r: httpx.Response(200, json={"text": "  "}))
    ) as http:
        with pytest.raises(MediaError):
            await Intelligence(settings(), http).transcribe(b"voice", "vocal.ogg", "audio/ogg")


@pytest.mark.parametrize(
    "extra,valid",
    [
        ({}, True),
        ({"total": "1000"}, False),
        ({"solde": 200}, False),
        ({"due_date": 1700000000}, False),
    ],
)
async def test_extraction_schema_and_local_validation(extra, valid):
    def handler(request):
        payload = json.loads(request.content)
        schema = payload["response_format"]["json_schema"]
        assert schema["strict"] is True
        assert schema["schema"]["additionalProperties"] is False
        user_data = json.loads(payload["messages"][1]["content"])
        assert user_data["brouillon"] == {"client": "Awa"}
        content = {
            "client": "Awa",
            "product": "riz",
            "total": 1000,
            "paid": 200,
            "due_date": "2026-10-20",
            **extra,
        }
        return httpx.Response(
            200, json={"choices": [{"message": {"content": json.dumps(content)}}]}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        ai = Intelligence(settings(), http)
        if valid:
            draft = await ai.extract("vente", {"client": "Awa"})
            assert "800 F CFA" in draft.summary()
        else:
            with pytest.raises(ValidationError):
                await ai.extract("vente", {"client": "Awa"})
