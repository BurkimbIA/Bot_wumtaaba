"""The idle shutdown that lets the Fly machine stop."""

import os
import subprocess
import sys
import time

from fastapi.testclient import TestClient

from bot_wumtaaba import main
from bot_wumtaaba.config import Settings


def test_pending_work_keeps_the_bot_awake(store):
    assert not store.has_pending_work()
    store.enqueue("wamid.1", "22670000001", {"type": "text", "text": {"body": "menu"}})
    assert store.has_pending_work()


def test_idle_bot_stops_itself_but_health_checks_do_not_count(database):
    stops = []
    settings = Settings(_env_file=None, whatsapp_token="t", meta_app_secret="t", gemini_api_key="t",
                        webhook_verify_token="x" * 32, whatsapp_phone_number_id="123",
                        allowed_senders="22670000000", supabase_url="https://project.supabase.co",
                        supabase_api_key="t", idle_shutdown_seconds=1, **database)
    app = main.create_app(settings)
    app.state.request_shutdown = lambda: stops.append(True)
    with TestClient(app) as client:
        deadline = time.monotonic() + 5
        while not stops and time.monotonic() < deadline:
            assert client.get("/healthz").status_code == 200
            time.sleep(0.2)
    assert stops == [True]


def test_real_server_idle_exit_is_successful(database, tmp_path):
    env = {**os.environ, **{key.upper(): str(value) for key, value in database.items()},
           "WHATSAPP_TOKEN": "test", "META_APP_SECRET": "test", "GEMINI_API_KEY": "test",
           "WEBHOOK_VERIFY_TOKEN": "x" * 32, "WHATSAPP_PHONE_NUMBER_ID": "123",
           "ALLOWED_SENDERS": "22670000000", "SUPABASE_URL": "https://project.supabase.co",
           "SUPABASE_API_KEY": "test", "IDLE_SHUTDOWN_SECONDS": "1"}
    result = subprocess.run(
        [sys.executable, "-m", "bot_wumtaaba.server", "--host", "127.0.0.1", "--port", "0"],
        env=env, cwd=tmp_path, capture_output=True, text=True, timeout=20,
    )
    assert result.returncode == 0, result.stderr
    assert "idle_shutdown" in result.stderr
    assert "Application shutdown complete" in result.stderr


def test_active_analysis_survives_idle_shutdown_threshold(database):
    import asyncio

    settings = Settings(_env_file=None, whatsapp_token="t", meta_app_secret="t", gemini_api_key="t",
                        webhook_verify_token="x" * 32, whatsapp_phone_number_id="123",
                        allowed_senders="22670000000", supabase_url="https://project.supabase.co",
                        supabase_api_key="t", idle_shutdown_seconds=1, **database)
    app = main.create_app(settings)
    stops = []
    app.state.request_shutdown = lambda: stops.append(True)

    @app.get('/test-slow-analysis')
    async def slow_analysis():
        await asyncio.sleep(2)
        return {'finished': True}

    with TestClient(app) as client:
        assert client.get('/test-slow-analysis').json() == {'finished': True}
        assert stops == []
        assert app.state.active_requests == 0
