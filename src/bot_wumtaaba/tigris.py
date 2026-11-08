"""Private annotation audio in Tigris, using the S3 Signature Version 4 protocol."""

import hashlib
import hmac
import re
from datetime import datetime, timezone
from urllib.parse import quote, urlsplit

import httpx

MAX_AUDIO_BYTES = 25_000_000
_PREFIX = "wuumtaaba/"


def _now():
    return datetime.now(timezone.utc)


def _digest(value):
    return hashlib.sha256(value).hexdigest()


def _query(parameters):
    return "&".join(f"{quote(k, safe='-_.~')}={quote(v, safe='-_.~')}" for k, v in sorted(parameters))


class TigrisAudioStore:
    """Scoped object access; bucket creation and public policies are deliberately absent."""

    provider = "tigris"

    def __init__(self, settings, http):
        self.http = http
        endpoint = urlsplit(settings.aws_endpoint_url_s3)
        if (endpoint.scheme != "https" or endpoint.netloc not in {"t3.storage.dev", "fly.storage.tigris.dev"}
                or endpoint.path not in {"", "/"} or endpoint.query or endpoint.fragment):
            raise ValueError("Unsupported Tigris endpoint")
        self.endpoint = f"https://{endpoint.netloc}"
        self.host = endpoint.netloc
        self.bucket = settings.annotation_bucket
        if not re.fullmatch(r"[a-z0-9][a-z0-9.-]{1,61}[a-z0-9]", self.bucket):
            raise ValueError("Invalid annotation bucket")
        self.region = settings.aws_region
        if not re.fullmatch(r"[a-z0-9-]+", self.region):
            raise ValueError("Invalid Tigris region")
        self._access = self._secret(settings.aws_access_key_id)
        self._secret_key = self._secret(settings.aws_secret_access_key)
        self._token = self._secret(getattr(settings, "aws_session_token", None), required=False)
        if not re.fullmatch(r"[A-Za-z0-9_-]+", self._access):
            raise ValueError("Invalid Tigris access key")

    @staticmethod
    def _secret(value, required=True):
        value = value.get_secret_value() if value is not None else ""
        if (required and not value) or any(ord(c) < 32 or ord(c) == 127 for c in value):
            raise ValueError("Missing or invalid Tigris credentials")
        return value

    def _path(self, key):
        if (not isinstance(key, str) or not key.startswith(_PREFIX) or key == _PREFIX
                or any(c in key for c in "\\?#%") or any(part in {"", ".", ".."} for part in key.split("/"))
                or any(ord(c) < 32 or ord(c) == 127 for c in key)):
            raise ValueError("Invalid annotation object key")
        return "/" + self.bucket + "/" + quote(key, safe="/-_.~")

    def _signature(self, canonical, date, timestamp):
        scope = f"{date}/{self.region}/s3/aws4_request"
        string_to_sign = f"AWS4-HMAC-SHA256\n{timestamp}\n{scope}\n{_digest(canonical.encode())}"
        key = ("AWS4" + self._secret_key).encode()
        for part in (date, self.region, "s3", "aws4_request"):
            key = hmac.new(key, part.encode(), hashlib.sha256).digest()
        return scope, hmac.new(key, string_to_sign.encode(), hashlib.sha256).hexdigest()

    def _request(self, method, key, data=b"", mime=None):
        path = self._path(key)
        timestamp = _now().strftime("%Y%m%dT%H%M%SZ")
        headers = {"host": self.host, "x-amz-date": timestamp, "x-amz-content-sha256": _digest(data)}
        if self._token:
            headers["x-amz-security-token"] = self._token
        if mime:
            headers["content-type"] = mime
        names = ";".join(sorted(headers))
        canonical_headers = "".join(f"{k}:{' '.join(headers[k].split())}\n" for k in sorted(headers))
        canonical = f"{method}\n{path}\n\n{canonical_headers}\n{names}\n{_digest(data)}"
        scope, signature = self._signature(canonical, timestamp[:8], timestamp)
        headers["authorization"] = (
            f"AWS4-HMAC-SHA256 Credential={self._access}/{scope}, SignedHeaders={names}, Signature={signature}"
        )
        # A fresh Request excludes client-wide cookies, auth and unsigned x-amz headers.
        return httpx.Request(method, self.endpoint + path, headers=headers, content=data)

    async def put(self, key, data, mime):
        if not data or len(data) > MAX_AUDIO_BYTES:
            raise ValueError("Annotation audio is empty or exceeds 25 MB")
        response = await self.http.send(self._request("PUT", key, data, mime), auth=None, follow_redirects=False)
        try:
            response.raise_for_status()
        finally:
            await response.aclose()

    async def delete(self, key):
        response = await self.http.send(self._request("DELETE", key), auth=None, follow_redirects=False)
        try:
            if response.status_code != 404:
                response.raise_for_status()
        finally:
            await response.aclose()

    async def get(self, key):
        response = await self.http.send(self._request("GET", key), auth=None, follow_redirects=False, stream=True)
        try:
            if response.status_code == 404:
                return None
            response.raise_for_status()
            chunks, size = [], 0
            async for chunk in response.aiter_bytes():
                size += len(chunk)
                if size > MAX_AUDIO_BYTES:
                    raise ValueError("Annotation audio exceeds 25 MB")
                chunks.append(chunk)
            return b"".join(chunks)
        finally:
            await response.aclose()

    async def verify_private(self, key):
        request = httpx.Request("HEAD", self.endpoint + self._path(key))
        response = await self.http.send(request, auth=None, follow_redirects=False)
        try:
            if response.status_code not in {401, 403, 404}:
                raise ValueError("Annotation object is public or its privacy could not be verified")
        finally:
            await response.aclose()

    def presign_get(self, key, expires=900):
        if type(expires) is not int or not 1 <= expires <= 3600:
            raise ValueError("Annotation URL expiry must be between 1 and 3600 seconds")
        path = self._path(key)
        timestamp = _now().strftime("%Y%m%dT%H%M%SZ")
        scope = f"{timestamp[:8]}/{self.region}/s3/aws4_request"
        parameters = [
            ("X-Amz-Algorithm", "AWS4-HMAC-SHA256"),
            ("X-Amz-Credential", f"{self._access}/{scope}"),
            ("X-Amz-Date", timestamp), ("X-Amz-Expires", str(expires)),
            ("X-Amz-SignedHeaders", "host"),
        ]
        if self._token:
            parameters.append(("X-Amz-Security-Token", self._token))
        query = _query(parameters)
        canonical = f"GET\n{path}\n{query}\nhost:{self.host}\n\nhost\nUNSIGNED-PAYLOAD"
        _, signature = self._signature(canonical, timestamp[:8], timestamp)
        return f"{self.endpoint}{path}?{query}&X-Amz-Signature={signature}"
