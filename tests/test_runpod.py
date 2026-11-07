import asyncio
import json
from types import SimpleNamespace

import httpx
import pytest
from pydantic import SecretStr

from bot_wumtaaba import runpod
from bot_wumtaaba.clients import MediaError
from bot_wumtaaba.runpod import RunPodClient


def settings(**updates):
    return SimpleNamespace(
        **{
            "runpod_api_key": SecretStr("rpa_private-test-key"),
            "runpod_api_base_url": "https://api.runpod.ai/v2",
            "runpod_timeout_seconds": 5,
            **updates,
        }
    )


@pytest.mark.asyncio
async def test_submits_once_with_private_key_and_polls_to_raw_completed_output(monkeypatch):
    monkeypatch.setattr(runpod, "POLL_SECONDS", 0)
    calls = []
    output = {"output": {"text": "Laafi bala", "success": True}, "metadata": {"model": "Whisper"}}

    def handler(request):
        calls.append(request)
        assert request.headers["authorization"] == "Bearer rpa_private-test-key"
        assert request.url.host == "api.runpod.ai"
        if request.url.path.endswith("/run"):
            assert json.loads(request.content) == {"input": {"audio_base64": "YXVkaW8="}}
            return httpx.Response(200, json={"id": "job-1-e1", "status": "IN_QUEUE"})
        return httpx.Response(200, json={"id": "job-1-e1", "status": "COMPLETED", "output": output})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        result = await RunPodClient(settings(), http).infer("h1powcx3dv6iy6", {"audio_base64": "YXVkaW8="})
    assert result == output
    assert [(request.method, request.url.path) for request in calls] == [
        ("POST", "/v2/h1powcx3dv6iy6/run"),
        ("GET", "/v2/h1powcx3dv6iy6/status/job-1-e1"),
    ]


@pytest.mark.asyncio
async def test_completed_vllm_list_is_returned_without_shape_guessing():
    output = [{"choices": [{"tokens": ["Bonjour"]}]}]
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "job-1", "status": "COMPLETED", "output": output})
        )
    ) as http:
        assert await RunPodClient(settings(), http).infer("endpoint1", {}) == output


@pytest.mark.asyncio
async def test_health_is_read_only_and_does_not_create_jobs():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"workers": {"idle": 0}, "jobs": {"inQueue": 0}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        assert (await RunPodClient(settings(), http).health("endpoint1"))["workers"]["idle"] == 0
    assert calls == [("GET", "/v2/endpoint1/health")]


@pytest.mark.asyncio
@pytest.mark.parametrize("status", ["FAILED", "CANCELLED", "TIMED_OUT"])
async def test_failed_terminal_jobs_are_sanitized_without_redundant_cancel(status):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            200, json={"id": "job-1", "status": status, "error": "rpa_private-test-key sensitive text"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError) as error:
            await RunPodClient(settings(), http).infer("endpoint1", {})
    assert "private-test" not in str(error.value)
    assert len(calls) == 1


@pytest.mark.asyncio
@pytest.mark.parametrize(
    "output", [{"success": False, "error": "private detail"}, {"error": "private detail"}, None, {}, []]
)
async def test_completed_but_invalid_model_output_is_a_sanitized_failure(output):
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"status": "COMPLETED", "output": output})
        )
    ) as http:
        with pytest.raises(MediaError) as error:
            await RunPodClient(settings(), http).infer("endpoint1", {})
    assert "private detail" not in str(error.value)


@pytest.mark.asyncio
@pytest.mark.parametrize("code", [302, 401, 403, 404, 429, 500])
async def test_http_errors_are_private_and_never_follow_redirects(code):
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(
            code, text="rpa_private-test-key details", headers={"Location": "https://attacker.example/"}
        )

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler), follow_redirects=True) as http:
        with pytest.raises(MediaError) as error:
            await RunPodClient(settings(), http).health("endpoint1")
    assert "private-test-key" not in str(error.value)
    assert len(calls) == 1
    if code == 429:
        assert "quota RunPod" in str(error.value)


@pytest.mark.asyncio
async def test_deadline_cancels_known_queued_job_and_does_not_retry_submission():
    calls = []

    def handler(request):
        calls.append((request.method, request.url.path))
        return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="délai"):
            await RunPodClient(settings(runpod_timeout_seconds=0.01), http).infer("endpoint1", {})
    assert calls == [("POST", "/v2/endpoint1/run"), ("POST", "/v2/endpoint1/cancel/job-1")]


@pytest.mark.asyncio
async def test_caller_cancellation_cancels_job_and_propagates_cancelled_error(monkeypatch):
    monkeypatch.setattr(runpod, "POLL_SECONDS", 0)
    polling = asyncio.Event()
    calls = []

    async def handler(request):
        calls.append((request.method, request.url.path))
        if "/status/" in request.url.path:
            polling.set()
            await asyncio.Event().wait()
        return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        task = asyncio.create_task(RunPodClient(settings(), http).infer("endpoint1", {}))
        await polling.wait()
        task.cancel()
        with pytest.raises(asyncio.CancelledError):
            await task
    assert calls[-1] == ("POST", "/v2/endpoint1/cancel/job-1")


@pytest.mark.asyncio
async def test_network_failure_after_submission_cancels_known_job(monkeypatch):
    monkeypatch.setattr(runpod, "POLL_SECONDS", 0)
    calls = []

    def handler(request):
        calls.append(request.url.path)
        if "/status/" in request.url.path:
            raise httpx.ConnectError("private error", request=request)
        return httpx.Response(200, json={"id": "job-1", "status": "IN_QUEUE"})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError):
            await RunPodClient(settings(), http).infer("endpoint1", {})
    assert calls == ["/v2/endpoint1/run", "/v2/endpoint1/status/job-1", "/v2/endpoint1/cancel/job-1"]


@pytest.mark.asyncio
async def test_lost_submission_response_is_not_retried_without_a_known_job_id():
    calls = []

    def handler(request):
        calls.append(request)
        raise httpx.ReadError("lost response", request=request)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError):
            await RunPodClient(settings(), http).infer("endpoint1", {})
    assert len(calls) == 1


@pytest.mark.asyncio
async def test_response_limit_and_invalid_job_ids_never_escape_origin(monkeypatch):
    monkeypatch.setattr(runpod, "MAX_RESPONSE_BYTES", 16)
    calls = []

    def handler(request):
        calls.append(request)
        return httpx.Response(200, content=b"x" * 17)

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="volumineuse"):
            await RunPodClient(settings(), http).health("endpoint1")
    assert len(calls) == 1
    monkeypatch.setattr(runpod, "MAX_RESPONSE_BYTES", 1024)
    async with httpx.AsyncClient(
        transport=httpx.MockTransport(
            lambda request: httpx.Response(200, json={"id": "../../attacker", "status": "IN_QUEUE"})
        )
    ) as http:
        with pytest.raises(MediaError, match="Identifiant"):
            await RunPodClient(settings(), http).infer("endpoint1", {})


@pytest.mark.asyncio
async def test_private_key_never_sent_to_untrusted_base_or_invalid_endpoint():
    async with httpx.AsyncClient() as http:
        for url in ["http://api.runpod.ai/v2", "https://attacker.example/v2", "https://api.runpod.ai/v2?x=1"]:
            with pytest.raises(ValueError):
                RunPodClient(settings(runpod_api_base_url=url), http)
        for endpoint in ["https://attacker.example", "../endpoint", "endpoint?x=1", [], None]:
            with pytest.raises(MediaError):
                await RunPodClient(settings(), http).health(endpoint)
        with pytest.raises(MediaError, match="clé RunPod"):
            await RunPodClient(settings(runpod_api_key=SecretStr("")), http).health("endpoint1")


@pytest.mark.asyncio
async def test_health_has_a_total_deadline_even_while_waiting_for_response():
    async def handler(request):
        await asyncio.sleep(0.1)
        return httpx.Response(200, json={"workers": {}})

    async with httpx.AsyncClient(transport=httpx.MockTransport(handler)) as http:
        with pytest.raises(MediaError, match="temps"):
            await RunPodClient(settings(runpod_timeout_seconds=0.01), http).health("endpoint1")
