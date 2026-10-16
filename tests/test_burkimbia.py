import base64
import json

import httpx
import pytest

from bot_wumtaaba.burkimbia import BurkimbIA
from bot_wumtaaba.clients import MediaError
from bot_wumtaaba.config import Settings


def settings(**kwargs):
    return Settings(_env_file=None, burkimbia_api_key="bia_test-secret", **kwargs)


@pytest.mark.asyncio
async def test_translation_contract_and_private_header():
    def handler(request):
        assert request.url == "https://bia-backend.fly.dev/api/v1/translate"
        assert request.headers["X-API-Key"] == "bia_test-secret"
        assert "authorization" not in request.headers
        assert json.loads(request.content) == {
            "text": "Bonjour",
            "src_lang": "french",
            "tgt_lang": "moore",
            "model": "bia-translation-v1-fast",
        }
        return httpx.Response(200, json={"output": " Ne y yibeogo "})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await BurkimbIA(settings(), http).translate("Bonjour") == "Ne y yibeogo"


@pytest.mark.asyncio
async def test_asr_sends_private_bytes_with_moore_hint():
    def handler(request):
        body = json.loads(request.content)
        assert body["audio_base64"] == base64.b64encode(b"audio").decode()
        assert body["language"] == "mos"
        assert "audio_url" not in body
        return httpx.Response(200, json={"text": "Laafi bala"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (
            await BurkimbIA(settings(), http).transcribe(b"audio", "private.ogg", "audio/ogg")
            == "Laafi bala"
        )


@pytest.mark.asyncio
async def test_tts_converts_wav_before_whatsapp(monkeypatch):
    wav = b"RIFF0000WAVEdata"

    async def encode(data, mime):
        assert data == wav and mime == "audio/wav"
        return b"mp3", "burkimbia.mp3", "audio/mpeg"

    monkeypatch.setattr("bot_wumtaaba.burkimbia.encode_whatsapp_audio", encode)

    def handler(request):
        assert json.loads(request.content)["voice_cloning"] is False
        return httpx.Response(200, json={"wav": base64.b64encode(wav).decode(), "success": True})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert await BurkimbIA(settings(), http).synthesize("Laafi") == (
            b"mp3",
            "burkimbia.mp3",
            "audio/mpeg",
        )


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [302, 401, 403, 422, 429, 500])
async def test_provider_failure_does_not_expose_response_or_secret(code):
    seen = []

    def handler(request):
        seen.append(request)
        return httpx.Response(
            code,
            json={"detail": "PRIVATE_SERVER_INFO"},
            headers={"Location": "https://evil.invalid"},
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError) as error:
            await BurkimbIA(settings(), http).translate("Bonjour")
        assert "PRIVATE" not in str(error.value) and "bia_test" not in str(error.value)
        assert len(seen) == 1


@pytest.mark.asyncio
async def test_timeout_is_safe_and_not_retried():
    def handler(request):
        raise httpx.ReadTimeout("secret upstream detail", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="trop de temps"):
            await BurkimbIA(settings(), http).translate("Bonjour")


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "result", [{"output": ""}, {"output": ["unexpected"]}, {"success": False}, {"output": 42}]
)
async def test_malformed_translation_rejected(result):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(lambda _: httpx.Response(200, json=result))
    ) as http:
        with pytest.raises(MediaError):
            await BurkimbIA(settings(), http).translate("Bonjour")


@pytest.mark.asyncio
async def test_local_limits_and_yaaba_do_not_call_remote():
    def handler(request):
        pytest.fail("Invalid requests must not spend provider quota")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        client = BurkimbIA(settings(), http)
        for text in ["", "x" * 513]:
            with pytest.raises(MediaError):
                await client.translate(text)
        with pytest.raises(MediaError):
            await client.translate("Hello", source="english")
        with pytest.raises(MediaError):
            await client.transcribe(b"", "x", "audio/ogg")
        with pytest.raises(MediaError, match="Yaaba"):
            await client.chat("Bonjour", [])


@pytest.mark.parametrize(
    "url",
    [
        "http://api.example.com",
        "https://user:secret@example.com",
        "https://api.example.com?key=secret",
    ],
)
def test_bad_base_url_rejected(url):
    with pytest.raises(ValueError):
        BurkimbIA(settings(burkimbia_api_url=url), httpx.AsyncClient())


@pytest.mark.asyncio
async def test_mac_routing_language_and_no_cloud_fallback():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    bridge = SimpleNamespace(provider="mac", submit=AsyncMock(return_value={"text": "Laafi"}))
    def forbidden(request):
        pytest.fail("Mac requests must never reach cloud")
    async with httpx.AsyncClient(transport=httpx.MockTransport(forbidden)) as http:
        client = BurkimbIA(settings(), http, bridge)
        assert await client.transcribe(b"voice", "x.ogg", "audio/ogg") == "Laafi"
        assert bridge.submit.call_args.args[1]["language"] == "moore"
        bridge.submit.side_effect = MediaError("Mac offline")
        with pytest.raises(MediaError, match="Mac offline"):
            await client.translate("Bonjour")


@pytest.mark.asyncio
async def test_dynamic_switch_and_yaaba():
    from types import SimpleNamespace
    from unittest.mock import AsyncMock
    bridge = SimpleNamespace(provider="mac", submit=AsyncMock(return_value={"choices": [{"message": {"content": "Laafi"}}]}))
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(200,json={"output":"cloud"}))) as http:
        client = BurkimbIA(settings(), http, bridge)
        assert client.yaaba_available
        assert await client.chat("Bonjour", []) == "Laafi"
        assert bridge.submit.call_args.args[0] == "/chat"
        bridge.provider = "cloud"
        assert not client.yaaba_available
        assert await client.translate("Bonjour") == "cloud"
        with pytest.raises(MediaError, match="Yaaba"):
            await client.chat("Bonjour", [])
