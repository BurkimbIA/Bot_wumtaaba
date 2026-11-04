"""The idle shutdown that lets the Fly machine stop."""

import signal
import time

from fastapi.testclient import TestClient

from bot_wumtaaba import main
from bot_wumtaaba.config import Settings


def test_pending_work_keeps_the_bot_awake(store):
    assert not store.has_pending_work()
    store.enqueue("wamid.1", "22670000001", {"type": "text", "text": {"body": "menu"}})
    assert store.has_pending_work()


def test_idle_bot_stops_itself_but_health_checks_do_not_count(database, monkeypatch):
    stops = []
    monkeypatch.setattr(main.os, "kill", lambda pid, sig: stops.append(sig))
    settings = Settings(_env_file=None, whatsapp_token="t", meta_app_secret="t", gemini_api_key="t",
                        webhook_verify_token="x" * 32, whatsapp_phone_number_id="123",
                        allowed_senders="22670000000", supabase_url="https://project.supabase.co",
                        supabase_api_key="t", idle_shutdown_seconds=1, **database)
    with TestClient(main.create_app(settings)) as client:
        deadline = time.monotonic() + 5
        while not stops and time.monotonic() < deadline:
            assert client.get("/healthz").status_code == 200
            time.sleep(0.2)
    assert stops and stops[0] == signal.SIGTERM
