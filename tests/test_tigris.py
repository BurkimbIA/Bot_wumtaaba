from datetime import datetime, timezone
from types import SimpleNamespace
from urllib.parse import parse_qs, urlsplit

import httpx
import pytest
from pydantic import SecretStr

from bot_wumtaaba.tigris import MAX_AUDIO_BYTES, TigrisAudioStore

KEY = "wuumtaaba/annotations/" + "a" * 64 + ".audio"


def settings(**changes):
    return SimpleNamespace(**{
        "annotation_bucket": "burkimbia-store", "aws_endpoint_url_s3": "https://t3.storage.dev",
        "aws_region": "auto", "aws_access_key_id": SecretStr("TESTACCESS"),
        "aws_secret_access_key": SecretStr("very-secret-key"), "aws_session_token": None, **changes,
    })


@pytest.fixture(autouse=True)
def fixed_clock(monkeypatch):
    monkeypatch.setattr("bot_wumtaaba.tigris._now", lambda: datetime(2026, 11, 8, 12, tzinfo=timezone.utc))


def test_aws_official_signature_vector():
    # AWS S3 signature-v4-header-based-auth example (2013-05-24 GET /test.txt).
    store = TigrisAudioStore(settings(aws_region="us-east-1", aws_secret_access_key=SecretStr(
        "wJalrXUtnFEMI/K7MDENG/bPxRfiCYEXAMPLEKEY")), None)
    canonical = (
        "GET\n/test.txt\n\nhost:examplebucket.s3.amazonaws.com\nrange:bytes=0-9\n"
        "x-amz-content-sha256:e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855\n"
        "x-amz-date:20130524T000000Z\n\nhost;range;x-amz-content-sha256;x-amz-date\n"
        "e3b0c44298fc1c149afbf4c8996fb92427ae41e4649b934ca495991b7852b855"
    )
    _, signature = store._signature(canonical, "20130524", "20130524T000000Z")
    assert signature == "f0e8bdb87c964420e857bd35b5d6ed310bd44f0170aba48dd91039c6036bdb41"


@pytest.mark.parametrize("endpoint", ["http://t3.storage.dev", "https://evil.example", "https://t3.storage.dev:443",
    "https://user@t3.storage.dev", "https://t3.storage.dev/path", "https://t3.storage.dev?query=1"])
def test_endpoint_restricted(endpoint):
    with pytest.raises(ValueError, match="endpoint"):
        TigrisAudioStore(settings(aws_endpoint_url_s3=endpoint), None)


@pytest.mark.parametrize("key", ["other/a", "wuumtaaba/annotations/../x", "wuumtaaba/annotations/./x",
    "wuumtaaba/annotations//x", "wuumtaaba/", "wuumtaaba/annotations/a\\b", "wuumtaaba/media/a?b",
    "wuumtaaba/media/a#b", "wuumtaaba/media/%2e%2e",
    "wuumtaaba/annotations/a\nx"])
def test_keys_restricted(key):
    with pytest.raises(ValueError, match="key"):
        TigrisAudioStore(settings(), None).presign_get(key)


@pytest.mark.parametrize("expires", [0, 3601, -1, True, 1.5])
def test_expiry_restricted(expires):
    with pytest.raises(ValueError, match="expiry"):
        TigrisAudioStore(settings(), None).presign_get(KEY, expires)


def test_presigned_url_scope_token_and_encoding():
    store = TigrisAudioStore(settings(aws_session_token=SecretStr("a+b/token=")), None)
    url = store.presign_get("wuumtaaba/annotations/audio 1.audio", 60)
    parsed = urlsplit(url)
    query = parse_qs(parsed.query)
    assert parsed.path == "/burkimbia-store/wuumtaaba/annotations/audio%201.audio"
    assert query["X-Amz-Credential"] == ["TESTACCESS/20261108/auto/s3/aws4_request"]
    assert query["X-Amz-SignedHeaders"] == ["host"]
    assert query["X-Amz-Security-Token"] == ["a+b/token="]
    assert query["X-Amz-Expires"] == ["60"]
    assert "very-secret-key" not in url
    assert "%2B" in url and "%2Ftoken%3D" in url
    assert len(query["X-Amz-Signature"][0]) == 64
    assert store.presign_get(KEY, 1) != store.presign_get(KEY, 3600)


def test_empty_credentials_rejected():
    with pytest.raises(ValueError, match="credentials"):
        TigrisAudioStore(settings(aws_secret_access_key=SecretStr("")), None)


async def test_put_get_and_anonymous_verification():
    requests = []

    def handle(request):
        requests.append(request)
        if request.method == "HEAD":
            assert "authorization" not in request.headers
            assert "cookie" not in request.headers
            assert not any(h.startswith("x-amz-") for h in request.headers)
            return httpx.Response(403)
        signed = request.headers["authorization"].split("SignedHeaders=")[1].split(",")[0].split(";")
        assert all(h in signed for h in request.headers if h.startswith("x-amz-"))
        assert "cookie" not in request.headers
        assert request.headers["x-amz-security-token"] == "token"
        if request.method == "PUT":
            assert request.content == b"audio"
            assert request.headers["content-type"] == "audio/mpeg"
            assert request.headers["x-amz-content-sha256"] == (
                "6ed8919ce20490a5e3ad8630a4fab69475297abd07db73918dd5f36fcfaeb11b"
            )
            return httpx.Response(200)
        return httpx.Response(200, content=b"audio")

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), auth=("client", "password"),
        headers={"x-amz-unsigned": "unsafe"}, cookies={"session": "private"}) as http:
        store = TigrisAudioStore(settings(aws_session_token=SecretStr("token")), http)
        await store.put(KEY, b"audio", "audio/mpeg")
        assert await store.get(KEY) == b"audio"
        await store.verify_private(KEY)
    assert len(requests) == 3


async def test_missing_and_oversized_audio():
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(404))) as http:
        store = TigrisAudioStore(settings(), http)
        assert await store.get(KEY) is None
        with pytest.raises(ValueError):
            await store.put(KEY, b"", "audio/mpeg")
    async with httpx.AsyncClient(transport=httpx.MockTransport(
        lambda _: httpx.Response(200, content=b"x" * (MAX_AUDIO_BYTES + 1)))) as http:
        with pytest.raises(ValueError, match="25 MB"):
            await TigrisAudioStore(settings(), http).get(KEY)


@pytest.mark.parametrize("status", [200, 204, 301, 302, 500])
async def test_public_or_unverifiable_object_rejected(status):
    async with httpx.AsyncClient(transport=httpx.MockTransport(lambda _: httpx.Response(
        status, headers={"location": "https://evil.example"})), follow_redirects=True) as http:
        with pytest.raises(ValueError, match="privacy"):
            await TigrisAudioStore(settings(), http).verify_private(KEY)


async def test_authenticated_redirect_not_followed():
    requests = []

    def handle(request):
        requests.append(request)
        return httpx.Response(307, headers={"location": "https://evil.example"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle), follow_redirects=True) as http:
        store = TigrisAudioStore(settings(), http)
        with pytest.raises(httpx.HTTPStatusError):
            await store.get(KEY)
        with pytest.raises(httpx.HTTPStatusError):
            await store.put(KEY, b"audio", "audio/mpeg")
    assert len(requests) == 2


@pytest.mark.parametrize("status", [204, 404])
async def test_delete_scoped_object(status):
    def handle(request):
        assert request.method == "DELETE"
        assert request.url.path == "/burkimbia-store/wuumtaaba/media/test.audio"
        assert "authorization" in request.headers
        return httpx.Response(status)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        await TigrisAudioStore(settings(), http).delete("wuumtaaba/media/test.audio")


def test_presigned_canonical_request(monkeypatch):
    store = TigrisAudioStore(settings(), None)

    def sign(canonical, date, timestamp):
        assert date == "20261108"
        assert timestamp == "20261108T120000Z"
        assert canonical == (
            "GET\n/burkimbia-store/wuumtaaba/media/audio%201.audio\n"
            "X-Amz-Algorithm=AWS4-HMAC-SHA256&"
            "X-Amz-Credential=TESTACCESS%2F20261108%2Fauto%2Fs3%2Faws4_request&"
            "X-Amz-Date=20261108T120000Z&X-Amz-Expires=900&X-Amz-SignedHeaders=host\n"
            "host:t3.storage.dev\n\nhost\nUNSIGNED-PAYLOAD"
        )
        return "scope", "signature"

    monkeypatch.setattr(store, "_signature", sign)
    assert store.presign_get("wuumtaaba/media/audio 1.audio").endswith("&X-Amz-Signature=signature")


async def test_put_preserves_existing_25_mb_limit():
    sizes = []

    def handle(request):
        sizes.append(len(request.content))
        return httpx.Response(200)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handle)) as http:
        store = TigrisAudioStore(settings(), http)
        await store.put("wuumtaaba/media/large.audio", b"a" * MAX_AUDIO_BYTES, "audio/mpeg")
        with pytest.raises(ValueError, match="25 MB"):
            await store.put("wuumtaaba/media/large.audio", b"a" * (MAX_AUDIO_BYTES + 1), "audio/mpeg")
    assert sizes == [25_000_000]
