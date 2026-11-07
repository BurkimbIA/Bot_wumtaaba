"""Partial notes preserve unknown values and never invent financial state."""

import json

import pytest
from fastapi.testclient import TestClient

from bot_wumtaaba import admin_data, payments
from bot_wumtaaba.config import Settings
from bot_wumtaaba.main import create_app
from bot_wumtaaba.mobile import apply_mobile_operation, pairing_operation

OWNER = "22670000001"
IDENTIFIER = "ab12cd34-partial"


def save_partial(store, **values):
    agreement = {
        "id": IDENTIFIER,
        "client": None,
        "client_phone": None,
        "product": "Riz",
        "total": None,
        "paid": None,
        "due_date": None,
        **values,
    }
    message_id = "create-" + agreement["id"]
    store.enqueue(message_id, OWNER, {"type": "text", "text": {"body": "Enregistrer"}})
    store.claim_inbox()
    store.finish_inbox(message_id, OWNER, {}, [], agreement)
    return agreement


@pytest.mark.parametrize("values", [
    {},
    {"total": 50000},
    {"paid": 10000},
    {"due_date": "2099-12-20"},
    {"total": 50000, "paid": 0},
])
def test_partial_persists_unknowns_and_does_not_schedule(store, make_store, values):
    original = save_partial(store, **values)
    row = store.get_agreement(OWNER, IDENTIFIER)
    assert row is not None
    for field in ("client", "total", "paid", "due_date"):
        assert row[field] == original[field]
    assert row["balance"] == (50000 if values == {"total": 50000, "paid": 0} else None)
    assert store.list_agreements(OWNER)[0]["total"] == original["total"]
    assert store.calendar_for(OWNER) == []
    assert store.db.execute("SELECT COUNT(*) FROM reminders").fetchone()[0] == 0
    ledger = store.payment_history(OWNER, IDENTIFIER)
    assert [payment["amount"] for payment in ledger] == ([10000] if values.get("paid") == 10000 else [])
    reopened = make_store()
    assert reopened.get_agreement(OWNER, IDENTIFIER)["paid"] == original["paid"]
    data = json.loads(store.db.execute("SELECT data FROM agreements WHERE id=%s", (IDENTIFIER,)).fetchone()[0])
    assert data == original


def test_unknown_balance_cannot_accept_payment_or_settlement(store):
    save_partial(store, total=50000)
    row = store.get_agreement(OWNER, IDENTIFIER)
    state, replies, operation = payments.start(row)
    assert operation is None and state == {} and "n’est pas renseigné" in str(replies)
    _, replies, operation = payments.prepare(
        store, OWNER, {"mode": "payment_amount", "agreement_id": IDENTIFIER}, "1000"
    )
    assert operation is None and "Aucun paiement ajouté" in str(replies)
    op = {"id": "partial-payment", "agreement_id": IDENTIFIER, "amount": 1000, "expected_paid": None}
    with store.db, pytest.raises(ValueError, match="Paiement invalide"):
        store._apply_payment(OWNER, op)
    assert store.settle_agreement(OWNER, "WT-AB12CD34") is False
    assert store.payment_history(OWNER, IDENTIFIER) == []
    assert store.get_agreement(OWNER, IDENTIFIER)["paid"] is None
    detail = payments.detail(store, OWNER, IDENTIFIER)
    assert "Non renseigné" in str(detail) and "Client non renseigné" in str(detail)
    assert "Ajouter paiement" not in str(detail)


def test_known_finances_allow_payment_without_inventing_due_date(store):
    save_partial(store, total=50000, paid=0, product=None)
    row = store.get_agreement(OWNER, IDENTIFIER)
    state, _, _ = payments.start(row)
    prepared, replies, _ = payments.prepare(store, OWNER, state, "1000")
    operation = prepared["payment"]
    assert operation["amount"] == 1000 and "Produit non renseigné" in str(replies)
    with store.db:
        store._apply_payment(OWNER, operation)
    assert store.get_agreement(OWNER, IDENTIFIER)["balance"] == 49000
    assert store.get_agreement(OWNER, IDENTIFIER)["due_date"] is None
    assert store.calendar_for(OWNER) == []


def test_admin_aggregates_do_not_assume_missing_paid_is_zero(store):
    save_partial(store, total=50000)
    assert admin_data.overview(store)["counts"]["balance"] == 0
    assert admin_data.agreement_sellers(store)["items"][0]["balance"] == 0
    assert admin_data.agreements(store)["items"][0]["paid"] is None


@pytest.fixture
def api(database):
    settings = Settings(
        **database, supabase_url="https://project.supabase.co", supabase_api_key="test",
        public_access=True, whatsapp_token="test", meta_app_secret="test",
        webhook_verify_token="x" * 32, gemini_api_key="test", whatsapp_phone_number_id="12345",
    )
    with TestClient(create_app(settings, run_worker=False)) as client:
        store = client.app.state.store
        operation, code = pairing_operation(OWNER)
        with store.lock, store.db:
            apply_mobile_operation(store.db, OWNER, operation)
        token = client.post("/mobile/v1/session", json={"code": code}).json()["token"]
        yield client, store, {"Authorization": "Bearer " + token}


def test_mobile_partial_reads_preserve_nulls_and_do_not_claim_settlement(api):
    client, store, headers = api
    save_partial(store, product=None, client="Awa")
    detail = client.get("/mobile/v1/agreements/" + IDENTIFIER, headers=headers)
    assert detail.status_code == 200
    value = detail.json()
    assert value["total"] is None and value["paid"] is None and value["balance"] is None
    assert value["due_date"] is None and value["payments"] == []
    assert value["product"] == "Produit non renseigné" and value["status"] == "incomplete"
    rows = client.get("/mobile/v1/agreements", headers=headers).json()["items"]
    assert len(rows) == 1 and rows[0]["balance"] is None
    assert client.get("/mobile/v1/agreements?status=settled", headers=headers).json()["items"] == []
    customers = client.get("/mobile/v1/clients", headers=headers).json()["items"]
    assert len(customers) == 1 and customers[0]["balance"] is None and customers[0]["incomplete"] == 1
    summary = client.get("/mobile/v1/summary", headers=headers).json()
    assert summary["agreements"] == 1 and summary["incomplete"] == 1 and summary["balance"] == 0


def test_mobile_partial_known_price_does_not_become_known_balance(api):
    client, store, headers = api
    save_partial(store, total=50000)
    summary = client.get("/mobile/v1/summary", headers=headers).json()
    assert summary["total"] == 50000 and summary["balance"] == 0 and summary["incomplete"] == 1
    customers = client.get("/mobile/v1/clients", headers=headers).json()["items"]
    assert customers[0]["name"] == "Client non renseigné" and customers[0]["balance"] is None
