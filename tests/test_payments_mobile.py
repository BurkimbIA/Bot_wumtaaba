import json
import time
from unittest.mock import AsyncMock

import pytest
from fastapi.testclient import TestClient

from bot_wumtaaba.config import Settings
from bot_wumtaaba.conversation import Conversation
from bot_wumtaaba.main import create_app
from bot_wumtaaba.mobile import apply_mobile_operation, digest, pairing_operation
from bot_wumtaaba.portal import PortalConversation
from bot_wumtaaba.store import Store

OWNER = "22670000001"
OTHER = "22670000002"
IDENTIFIER = "abcd1234aaaa"


def message(body):
    return {"type": "text", "text": {"body": body}}


def button(action):
    return {"type": "interactive", "interactive": {"button_reply": {"id": action}}}


def save(store, identifier=IDENTIFIER, sender=OWNER, paid=10000):
    mid = "create-" + identifier
    store.enqueue(mid, sender, message("confirm"))
    store.claim_inbox()
    store.finish_inbox(
        mid,
        sender,
        {},
        [],
        {
            "id": identifier,
            "client": "Awa",
            "client_phone": "22670000009",
            "product": "Riz",
            "total": 75000,
            "paid": paid,
            "due_date": "2099-12-20",
        },
    )


def commit(store, operation, mid="pay-1", sender=OWNER):
    store.enqueue(mid, sender, message("confirmer"))
    store.claim_inbox()
    store.finish_inbox(mid, sender, {}, [message("success")], operation)


def operation(amount=20000, paid=10000, identifier="unique-payment"):
    return {
        "_operation": "payment",
        "id": identifier,
        "agreement_id": IDENTIFIER,
        "amount": amount,
        "expected_paid": paid,
    }


@pytest.fixture
def store():
    s = Store(":memory:")
    save(s)
    yield s
    s.close()


def test_ledger_atomic_recalculation_and_replay(store):
    commit(store, operation())
    row = store.get_agreement(OWNER, IDENTIFIER)
    assert row["paid"] == 30000 and row["balance"] == 45000
    assert store.list_agreements(OWNER)[0]["paid"] == 30000
    assert [r["amount"] for r in store.payment_history(OWNER, IDENTIFIER)] == [10000, 20000]
    commit(store, operation(), mid="second-click")
    assert len(store.payment_history(OWNER, IDENTIFIER)) == 2
    assert store.payment_history(OTHER, IDENTIFIER) == []
    assert store.get_agreement(OTHER, IDENTIFIER) is None


@pytest.mark.parametrize("amount", [-1, 0, 65001, True, 1.5])
def test_invalid_amount_rolls_back_inbox_and_balance(store, amount):
    before = store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0]
    with pytest.raises(ValueError):
        commit(store, operation(amount=amount))
    assert store.get_agreement(OWNER, IDENTIFIER)["paid"] == 10000
    assert (
        store.db.execute("SELECT status FROM inbox WHERE id=?", ("pay-1",)).fetchone()[0]
        == "processing"
    )
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == before


def test_stale_balance_and_foreign_ownership_rejected(store):
    with pytest.raises(ValueError):
        commit(store, operation(paid=0))
    with pytest.raises(ValueError):
        commit(store, operation(), mid="foreign", sender=OTHER)
    assert store.get_agreement(OWNER, IDENTIFIER)["paid"] == 10000


def test_full_payment_cancels_reminders_and_legacy_settle_is_ledgered(store):
    commit(store, operation(amount=65000))
    assert store.calendar_for(OWNER)[0]["status"] == "settled"
    assert store.get_agreement(OWNER, IDENTIFIER)["balance"] == 0
    assert not store.db.execute(
        "SELECT 1 FROM reminders WHERE status IN('pending','pending_consent')"
    ).fetchone()
    assert store.settle_agreement(OWNER, "WT-ABCD1234")
    assert len(store.payment_history(OWNER, IDENTIFIER)) == 2


async def test_dialogue_confirmation_correction_cancellation_and_stale_button(store):
    bot = Conversation(Settings(public_access=True), AsyncMock(), AsyncMock(), store)
    state, _, _ = await bot.handle(OWNER, message("paiement"), {})
    state, _, _ = await bot.handle(OWNER, message("WT-ABCD1234"), state)
    state, replies, op = await bot.handle(OWNER, message("20 000"), state)
    assert op is None and "45 000" in str(replies)
    old = state["payment"]["id"]
    state, _, _ = await bot.handle(OWNER, message("5 000"), state)
    new = state["payment"]["id"]
    assert old != new
    state, replies, op = await bot.handle(OWNER, button("payconfirm:" + old), state)
    assert op is None and "expiré" in str(replies)
    state, _, op = await bot.handle(OWNER, button("payconfirm:" + new), state)
    assert state == {} and op["amount"] == 5000
    commit(store, op)
    _, replies, _ = await bot.handle(OWNER, message("historique WT-ABCD1234"), {})
    assert "5 000" in str(replies) and "60 000" in str(replies)
    state, _, _ = await bot.handle(OWNER, button("payment:" + IDENTIFIER), {})
    state, _, _ = await bot.handle(OWNER, message("20000"), state)
    state, _, _ = await bot.handle(OWNER, button("paycancel"), state)
    _, _, op = await bot.handle(OWNER, button("payconfirm:" + new), state)
    assert op is None


async def test_owner_cannot_select_foreign_agreement_and_ambiguous_search_requires_choice(store):
    save(store, identifier="bbbb1234", sender=OWNER)
    bot = Conversation(Settings(public_access=True), AsyncMock(), AsyncMock(), store)
    _, replies, _ = await bot.handle(OTHER, button("payment:" + IDENTIFIER), {})
    assert "introuvable" in str(replies)
    state, _, _ = await bot.handle(OWNER, message("paiement"), {})
    state, replies, _ = await bot.handle(OWNER, message("Awa"), state)
    assert (
        state["mode"] == "payment_select"
        and len(replies[0]["interactive"]["action"]["sections"][0]["rows"]) == 2
    )


def test_persistence_and_legacy_settled_backfill(tmp_path):
    path = tmp_path / "ledger.sqlite3"
    s = Store(path)
    save(s)
    commit(s, operation())
    s.close()
    s = Store(path)
    assert s.get_agreement(OWNER, IDENTIFIER)["balance"] == 45000
    assert len(s.payment_history(OWNER, IDENTIFIER)) == 2
    s.close()
    legacy = tmp_path / "legacy.sqlite3"
    s = Store(legacy)
    save(s)
    with s.db:
        for table in ["payments", "mobile_codes", "mobile_tokens"]:
            s.db.execute("DROP TABLE " + table)
        s.db.execute("DELETE FROM schema_migrations WHERE version=3")
        s.db.execute("UPDATE calendar_events SET status='settled'")
    s.close()
    s = Store(legacy)
    assert s.get_agreement(OWNER, IDENTIFIER)["balance"] == 0
    assert [p["kind"] for p in s.payment_history(OWNER, IDENTIFIER)] == [
        "initial",
        "legacy_settlement",
    ]
    s.close()
    s = Store(legacy)
    assert len(s.payment_history(OWNER, IDENTIFIER)) == 2
    assert (tmp_path / "legacy.sqlite3.pre-v3.sqlite3").exists()
    s.close()


@pytest.fixture
def api(tmp_path):
    settings = Settings(
        database_path=tmp_path / "api.sqlite3",
        public_access=True,
        whatsapp_token="test",
        meta_app_secret="test",
        webhook_verify_token="x" * 32,
        gemini_api_key="test",
        whatsapp_phone_number_id="12345",
    )
    app = create_app(settings, run_worker=False)
    with TestClient(app) as client:
        save(app.state.store)
        yield client, app.state.store


def session(client, store, sender=OWNER):
    op, code = pairing_operation(sender)
    with store.lock, store.db:
        apply_mobile_operation(store.db, sender, op)
    response = client.post("/mobile/v1/session", json={"code": code})
    assert response.status_code == 200
    return {"Authorization": "Bearer " + response.json()["token"]}, code


def test_mobile_one_time_code_expiration_revocation_no_cache_and_isolation(api):
    client, store = api
    assert client.get("/mobile/v1/summary").status_code == 401
    headers, code = session(client, store)
    assert client.post("/mobile/v1/session", json={"code": code}).status_code == 401
    assert store.db.execute("SELECT hash FROM mobile_tokens").fetchone()[0] == digest(
        headers["Authorization"][7:]
    )
    detail = client.get("/mobile/v1/agreements/" + IDENTIFIER, headers=headers)
    assert detail.status_code == 200 and detail.json()["balance"] == 65000
    assert detail.headers["cache-control"] == "no-store"
    assert detail.json()["payments"][0]["kind"] == "initial"
    foreign, _ = session(client, store, OTHER)
    assert client.get("/mobile/v1/agreements/" + IDENTIFIER, headers=foreign).status_code == 404
    assert client.get("/mobile/v1/agreements", headers=foreign).json()["items"] == []
    assert client.get("/mobile/v1/clients", headers=foreign).json()["items"] == []
    assert client.get("/mobile/v1/summary", headers=foreign).json()["balance"] == 0
    with store.db:
        store.db.execute("UPDATE mobile_tokens SET expires_at=0 WHERE sender=?", (OWNER,))
    assert client.get("/mobile/v1/summary", headers=headers).status_code == 401
    headers, _ = session(client, store)
    assert client.delete("/mobile/v1/session", headers=headers).status_code == 204
    assert client.get("/mobile/v1/summary", headers=headers).status_code == 401


async def test_pairing_command_in_portal_commits_with_inbox_and_revoke_all(api):
    client, store = api
    settings = Settings(public_access=True)
    bot = Conversation(settings, AsyncMock(), AsyncMock(), store)
    portal = PortalConversation(settings, AsyncMock(), AsyncMock(), store, bot)
    _, replies, op = await portal.handle(OWNER, message("application"), {"portal": "burkimbia"})
    code = str(replies[0]["text"]["body"]).split("\n")[2]
    assert client.post("/mobile/v1/session", json={"code": code}).status_code == 401
    commit(store, op, mid="pair")
    token = client.post("/mobile/v1/session", json={"code": code}).json()["token"]
    _, _, op = await portal.handle(OWNER, message("deconnecter application"), {})
    commit(store, op, mid="revoke")
    assert (
        client.get("/mobile/v1/summary", headers={"Authorization": "Bearer " + token}).status_code
        == 401
    )


def test_mobile_filters_pagination_and_payment_refresh(api):
    client, store = api
    headers, _ = session(client, store)
    commit(store, operation())
    assert client.get("/mobile/v1/summary", headers=headers).json()["balance"] == 45000
    assert (
        client.get("/mobile/v1/agreements", params={"status": "settled"}, headers=headers).json()[
            "items"
        ]
        == []
    )
    assert (
        len(
            client.get("/mobile/v1/agreements", params={"q": "riz"}, headers=headers).json()[
                "items"
            ]
        )
        == 1
    )
    assert (
        client.get("/mobile/v1/agreements", params={"q": "' OR 1=1"}, headers=headers).json()[
            "items"
        ]
        == []
    )
    for i in range(27):
        save(store, identifier=f"{i:08x}aaaa")
    first = client.get("/mobile/v1/agreements", headers=headers).json()
    second = client.get("/mobile/v1/agreements", params={"page": 2}, headers=headers).json()
    assert len(first["items"]) == 25 and first["has_more"]
    assert len(second["items"]) == 3 and not second["has_more"]
    assert not ({r["id"] for r in first["items"]} & {r["id"] for r in second["items"]})
    grouped = client.get("/mobile/v1/clients", headers=headers).json()["items"]
    assert len(grouped) == 1 and grouped[0]["agreements"] == 28
    assert (
        len(
            client.get(
                "/mobile/v1/agreements", params={"client": grouped[0]["id"]}, headers=headers
            ).json()["items"]
        )
        == 25
    )


def test_expired_pairing_code_and_no_mobile_financial_write(api):
    client, store = api
    op, code = pairing_operation(OWNER)
    op["expires_at"] = time.time() - 1
    with store.db:
        apply_mobile_operation(store.db, OWNER, op)
    assert client.post("/mobile/v1/session", json={"code": code}).status_code == 401
    headers, _ = session(client, store)
    assert client.post("/mobile/v1/agreements", json={}, headers=headers).status_code == 405
    assert (
        client.post(
            "/mobile/v1/agreements/" + IDENTIFIER, json={"paid": 75000}, headers=headers
        ).status_code
        == 405
    )
    assert store.get_agreement(OWNER, IDENTIFIER)["paid"] == 10000


@pytest.mark.parametrize("kind", ["text", "template"])
def test_partial_payment_refreshes_already_queued_reminder(store, kind):
    from types import SimpleNamespace

    from bot_wumtaaba.reminders import _payload

    row = {
        "total": 75000,
        "paid": 10000,
        "reference": "WT-ABCD1234",
        "due_date": "2099-12-20",
        "last_inbound_at": time.time() if kind == "text" else None,
    }
    payload = _payload(
        row,
        SimpleNamespace(reminder_template_name="approved", reminder_template_language="fr"),
        time.time(),
    )
    with store.lock, store.db:
        reminder = store.db.execute("SELECT id FROM reminders LIMIT 1").fetchone()[0]
        out = store.queue_outbox(OWNER, payload)
        store.db.execute(
            "UPDATE outbox SET category='reminder',reminder_id=? WHERE id=?", (reminder, out)
        )
        store.db.execute(
            "UPDATE reminders SET status='queued',payload=?,outbox_id=? WHERE id=?",
            (json.dumps(payload), out, reminder),
        )
    commit(store, operation())
    updated = json.loads(
        store.db.execute("SELECT content FROM outbox WHERE id=?", (out,)).fetchone()[0]
    )
    if kind == "text":
        assert "45000 F CFA" in updated["text"]["body"]
    else:
        assert updated["template"]["components"][0]["parameters"][1]["text"] == "45000"


def test_history_pagination_and_nameless_customer_not_merged(api):
    client, store = api
    headers, _ = session(client, store)
    for i in range(27):
        commit(store, operation(amount=1, paid=10000 + i, identifier=f"payment-{i}"), mid=f"m-{i}")
    first = client.get("/mobile/v1/agreements/" + IDENTIFIER, headers=headers).json()
    second = client.get(
        "/mobile/v1/agreements/" + IDENTIFIER, params={"page": 2}, headers=headers
    ).json()
    assert len(first["payments"]) == 25 and first["has_more"]
    assert len(second["payments"]) == 3 and not second["has_more"]
    with store.db:
        store.db.execute("UPDATE agreements SET client_phone=NULL")
    save(store, identifier="ffffffffaaaa")
    with store.db:
        store.db.execute("UPDATE agreements SET client_phone=NULL")
    assert len(client.get("/mobile/v1/clients", headers=headers).json()["items"]) == 2


def test_customer_search_keeps_full_totals_across_name_variants_and_accents(api):
    client, store = api
    save(store, identifier="eeeeffffaaaa")
    with store.db:
        store.db.execute(
            "UPDATE agreements SET client_name='Awa Ouédraogo' WHERE id=?", (IDENTIFIER,)
        )
    headers, _ = session(client, store)
    rows = client.get("/mobile/v1/clients", params={"q": "ouedraogo"}, headers=headers).json()[
        "items"
    ]
    assert len(rows) == 1 and rows[0]["agreements"] == 2 and rows[0]["balance"] == 130000
    assert (
        len(
            client.get("/mobile/v1/agreements", params={"q": "ouedraogo"}, headers=headers).json()[
                "items"
            ]
        )
        == 1
    )


async def test_new_voice_correction_invalidates_previous_payment_before_consent(store):
    bot = Conversation(Settings(public_access=True), AsyncMock(), AsyncMock(), store)
    state, _, _ = await bot.handle(OWNER, button("payment:" + IDENTIFIER), {})
    state, _, _ = await bot.handle(OWNER, message("20000"), state)
    old = state["payment"]["id"]
    state, _, _ = await bot.handle(OWNER, {"type": "audio", "audio": {"id": "123"}}, state)
    _, replies, op = await bot.handle(OWNER, button("payconfirm:" + old), state)
    assert op is None and "expiré" in str(replies)
    bot.whatsapp.audio.assert_not_called()
