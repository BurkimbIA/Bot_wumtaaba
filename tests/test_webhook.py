import hashlib
import hmac
import json
import time
from unittest.mock import AsyncMock

import httpx
import pytest
from fastapi.testclient import TestClient

from bot_wumtaaba.config import Settings
from bot_wumtaaba.main import create_app, process_once
from bot_wumtaaba.store import Store


@pytest.fixture
def settings(tmp_path):
    return Settings(
        _env_file=None,
        whatsapp_token="test-meta",
        meta_app_secret="test-secret",
        webhook_verify_token="test-verify-token-24-characters",
        gemini_api_key="test-ai",
        whatsapp_phone_number_id="123",
        allowed_senders="22670000000",
        database_path=tmp_path / "test.sqlite3",
    )


def payload(sender="22670000000", phone="123", msg_id="message-1", age=0):
    return {
        "object": "whatsapp_business_account",
        "entry": [
            {
                "changes": [
                    {
                        "field": "messages",
                        "value": {
                            "metadata": {"phone_number_id": phone},
                            "messages": [
                                {
                                    "id": msg_id,
                                    "from": sender,
                                    "timestamp": str(int(time.time()) - age),
                                    "type": "text",
                                    "text": {"body": "menu"},
                                }
                            ],
                        },
                    }
                ]
            }
        ],
    }


def post(client, data):
    body = json.dumps(data).encode()
    sig = "sha256=" + hmac.new(b"test-secret", body, hashlib.sha256).hexdigest()
    return client.post("/webhook", content=body, headers={"x-hub-signature-256": sig})


def test_verification_and_no_public_docs(settings):
    with TestClient(create_app(settings, run_worker=False)) as client:
        assert client.get("/healthz").status_code == 200
        assert client.get("/docs").status_code == 404
        assert client.get("/webhook").status_code == 403
        assert (
            client.get(
                "/webhook",
                params={
                    "hub.mode": "subscribe",
                    "hub.verify_token": settings.webhook_verify_token.get_secret_value(),
                    "hub.challenge": "123456",
                },
            ).text
            == "123456"
        )


def test_unsigned_and_tampered_rejected(settings):
    with TestClient(create_app(settings, run_worker=False)) as client:
        assert client.post("/webhook", json=payload()).status_code == 403
        assert (
            client.post(
                "/webhook", content=b"{}", headers={"x-hub-signature-256": "bad"}
            ).status_code
            == 403
        )
        assert client.app.state.store.claim_inbox() is None


def test_idempotence_and_sender_filter(settings):
    with TestClient(create_app(settings, run_worker=False)) as client:
        assert post(client, payload()).status_code == 200
        post(client, payload())
        post(client, payload(sender="999", msg_id="other"))
        post(client, payload(phone="wrong", msg_id="wrong"))
        post(client, payload(age=86400, msg_id="old"))
        job = client.app.state.store.claim_inbox()
        assert job["id"] == "message-1"
        assert client.app.state.store.claim_inbox() is None


def test_payload_limit_and_malformed(settings):
    with TestClient(create_app(settings, run_worker=False)) as client:
        assert client.post("/webhook", content=b"x" * 1_000_001).status_code == 413
        assert post(client, []).status_code == 400
        assert (
            post(client, {"object": "whatsapp_business_account", "entry": "bad"}).status_code == 400
        )


def test_hourly_limit(settings):
    settings.max_messages_per_hour = 1
    with TestClient(create_app(settings, run_worker=False)) as client:
        post(client, payload(msg_id="one"))
        post(client, payload(msg_id="two"))
        assert client.app.state.store.count_recent("22670000000") == 1


@pytest.mark.asyncio
async def test_worker_transaction_and_send(tmp_path):
    store = Store(tmp_path / "db")
    store.enqueue("id", "user", {"type": "text"})
    conversation = AsyncMock()
    conversation.handle.return_value = ({}, [{"type": "text", "text": {"body": "ok"}}], None)
    whatsapp = AsyncMock()
    whatsapp.send.return_value = "remote-id"
    assert await process_once(store, conversation, whatsapp)
    assert not await process_once(store, conversation, whatsapp)
    assert store.db.execute("SELECT status FROM outbox").fetchone()[0] == "sent"
    store.close()


@pytest.mark.asyncio
async def test_ambiguous_send_never_auto_retried(tmp_path):
    store = Store(tmp_path / "db")
    store.enqueue("id", "user", {"type": "text"})
    conversation = AsyncMock()
    conversation.handle.return_value = ({}, [{"type": "text", "text": {"body": "ok"}}], None)
    whatsapp = AsyncMock()
    whatsapp.send.side_effect = httpx.ReadTimeout("network")
    await process_once(store, conversation, whatsapp)
    store.recover()
    assert store.claim_outbox() is None
    assert store.db.execute("SELECT status FROM outbox").fetchone()[0] == "uncertain"
    store.close()


@pytest.mark.asyncio
async def test_old_incoming_never_processed_after_restart(tmp_path):
    store = Store(tmp_path / "db")
    store.enqueue("old", "user", {"timestamp": str(int(time.time()) - 86400)})
    conversation, whatsapp = AsyncMock(), AsyncMock()
    await process_once(store, conversation, whatsapp)
    conversation.handle.assert_not_awaited()
    assert store.db.execute("SELECT status FROM inbox").fetchone()[0] == "failed"
    store.close()


@pytest.mark.asyncio
async def test_provider_outage_after_three_attempts_notifies_user(tmp_path):
    store = Store(tmp_path / "db")
    store.enqueue("failure", "user", {"type": "audio"})
    conversation, whatsapp = AsyncMock(), AsyncMock()
    conversation.handle.side_effect = httpx.ReadTimeout("provider unavailable")
    whatsapp.send.return_value = "error-notice"
    for _ in range(3):
        store.db.execute("UPDATE inbox SET available_at=0")
        store.db.commit()
        await process_once(store, conversation, whatsapp)
    assert whatsapp.send.await_count == 1
    assert "indisponible" in whatsapp.send.call_args.args[1]["text"]["body"]
    store.close()


def test_outbox_expiry_based_on_whatsapp_timestamp(tmp_path):
    store = Store(tmp_path / "db")
    original_time = int(time.time()) - 7200
    store.enqueue("id", "user", {"timestamp": str(original_time)})
    store.claim_inbox()
    store.finish_inbox("id", "user", {}, [{"type": "text"}])
    assert store.claim_outbox()["created_at"] == original_time
    store.close()
