import json
from types import SimpleNamespace

import pytest

from bot_wumtaaba.migrations import contact
from bot_wumtaaba.reminders import can_send_outbox, queue_due_reminders
from bot_wumtaaba.store import Store

NOW = 2_000_000_000


@pytest.fixture
def setup():
    store = Store(":memory:")
    settings = SimpleNamespace(reminder_enabled=True, reminder_template_name="",
                               reminder_template_language="fr")
    with store.db:
        for phone in ("22670000001", "22670000002"):
            contact(store.db, phone, NOW - 60, inbound=True)
        store.db.execute(
            "INSERT INTO agreements(id,sender,data,created_at,reference,client_phone,"
            "total,paid,due_date) VALUES('abc','22670000001','{}',?,'WT-ABC',"
            "'22670000002',30000,10000,'2033-05-18')", (NOW - 100,))
        store.db.execute(
            "INSERT INTO calendar_events(id,agreement_id,owner_sender,customer_phone,"
            "starts_at,timezone,status) VALUES('abc','abc','22670000001','22670000002',"
            "?,'Africa/Ouagadougou','open')", (NOW,))
        store.db.execute(
            "INSERT INTO customer_consents(agreement_id,phone,granted,updated_at,source_message_id) "
            "VALUES('abc','22670000002',1,?,'in:consent')", (NOW - 60,))
        for recipient, role in (("22670000001", "merchant"), ("22670000002", "customer")):
            store.db.execute(
                "INSERT INTO reminders(event_id,recipient,role,scheduled_at,status) "
                "VALUES('abc',?,?,?,'pending')", (recipient, role, NOW))
    yield store, settings
    store.close()


def test_atomic_queue_no_duplicate_even_uncertain(setup):
    store, settings = setup
    assert queue_due_reminders(store, settings, NOW) == 2
    assert queue_due_reminders(store, settings, NOW) == 0
    row = store.claim_outbox()
    assert can_send_outbox(store, row, NOW)
    assert row["content"]["type"] == "text"
    assert "20000 F CFA" in row["content"]["text"]["body"]
    store.fail_outbox(row["id"], "timeout", status="uncertain")
    assert queue_due_reminders(store, settings, NOW + 30) == 0
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 2


def test_template_block_then_recheck_and_send(setup):
    store, settings = setup
    with store.db:
        store.db.execute("UPDATE conversations SET last_inbound_at=?", (NOW - 24 * 3600,))
    assert queue_due_reminders(store, settings, NOW) == 0
    assert {r[0] for r in store.db.execute("SELECT status FROM reminders")} == {"blocked_template"}
    settings.reminder_template_name = "wuumtaaba_echeance_v1"
    assert queue_due_reminders(store, settings, NOW) == 2
    content = json.loads(store.db.execute("SELECT content FROM outbox LIMIT 1").fetchone()[0])
    assert content["template"]["language"]["code"] == "fr"
    assert [p["text"] for p in content["template"]["components"][0]["parameters"]] == [
        "WT-ABC", "20000", "2033-05-18"]


@pytest.mark.parametrize("mutation", [
    "UPDATE customer_consents SET granted=0",
    "DELETE FROM customer_consents",
    "UPDATE customer_consents SET phone='22679999999'",
    "UPDATE customer_consents SET source_message_id=NULL",
    "UPDATE agreements SET client_phone=NULL",
])
def test_both_need_exact_customer_consent(setup, mutation):
    store, settings = setup
    with store.db:
        store.db.execute(mutation)
    assert queue_due_reminders(store, settings, NOW) == 0
    assert {r[0] for r in store.db.execute("SELECT status FROM reminders")} == {"pending_consent"}


@pytest.mark.parametrize("mutation", [
    "UPDATE agreements SET paid=total",
    "UPDATE calendar_events SET status='cancelled'",
    "UPDATE contacts SET reminders_enabled=0",
])
def test_paid_cancelled_or_stop_prevent_send(setup, mutation):
    store, settings = setup
    with store.db:
        store.db.execute(mutation)
    assert queue_due_reminders(store, settings, NOW) == 0


def test_no_catchup_after_day(setup):
    store, settings = setup
    assert queue_due_reminders(store, settings, NOW + 86400) == 0
    assert {r[0] for r in store.db.execute("SELECT status FROM reminders")} == {"missed"}


@pytest.mark.parametrize("mutation", [
    "UPDATE customer_consents SET granted=0",
    "UPDATE contacts SET reminders_enabled=0",
    "UPDATE agreements SET paid=total",
    "UPDATE calendar_events SET status='cancelled'",
])
def test_dispatch_rechecks_authorization(setup, mutation):
    store, settings = setup
    queue_due_reminders(store, settings, NOW)
    outgoing = store.claim_outbox()
    with store.db:
        store.db.execute(mutation)
    assert not can_send_outbox(store, outgoing, NOW)
    assert queue_due_reminders(store, settings, NOW + 30) == 0


def test_dispatch_rechecks_actual_inbound_window(setup):
    store, settings = setup
    queue_due_reminders(store, settings, NOW)
    outgoing = store.claim_outbox()
    assert not can_send_outbox(store, outgoing, NOW + 23 * 3600)


def test_disabled_and_future_do_not_queue(setup):
    store, settings = setup
    assert queue_due_reminders(store, settings, NOW - 1) == 0
    settings.reminder_enabled = False
    assert queue_due_reminders(store, settings, NOW) == 0


def test_transaction_rolls_back_partial_enqueue(setup, monkeypatch):
    store, settings = setup
    original = store.queue_outbox
    count = 0

    def broken(*args, **kwargs):
        nonlocal count
        count += 1
        if count == 2:
            raise RuntimeError("simulated disk failure")
        return original(*args, **kwargs)

    monkeypatch.setattr(store, "queue_outbox", broken)
    with pytest.raises(RuntimeError, match="disk failure"):
        queue_due_reminders(store, settings, NOW)
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0
    assert {r[0] for r in store.db.execute("SELECT status FROM reminders")} == {"pending"}


@pytest.mark.parametrize("body", ["STOP", " Refuser WT-ABCDEF12 ", "refuser abcdef12"])
@pytest.mark.parametrize("status", ["pending", "processing"])
def test_received_customer_optout_blocks_both_before_processing(setup, body, status):
    store, settings = setup
    with store.db:
        store.db.execute("UPDATE agreements SET reference='WT-ABCDEF12'")
    queue_due_reminders(store, settings, NOW)
    store.enqueue("optout", "22670000002", {"type": "text", "text": {"body": body},
                                               "timestamp": str(NOW)})
    with store.db:
        store.db.execute("UPDATE inbox SET status=? WHERE id='optout'", (status,))
    for _ in range(2):
        outgoing = store.claim_outbox()
        assert not can_send_outbox(store, outgoing, NOW)
        store.fail_outbox(outgoing["id"], "optout")
    assert queue_due_reminders(store, settings, NOW) == 0


def test_pending_optout_does_not_queue_then_store_handles_it(setup):
    store, settings = setup
    store.enqueue("optout", "22670000002", {"type": "text", "text": {"body": "STOP"},
                                               "timestamp": str(NOW)})
    assert queue_due_reminders(store, settings, NOW) == 0
    assert store.db.execute("SELECT COUNT(*) FROM outbox").fetchone()[0] == 0


def test_merchant_stop_only_blocks_own_reminder(setup):
    store, settings = setup
    store.enqueue("optout", "22670000001", {"type": "text", "text": {"body": "STOP"},
                                               "timestamp": str(NOW)})
    assert queue_due_reminders(store, settings, NOW) == 1
    assert store.claim_outbox()["recipient"] == "22670000002"


@pytest.mark.parametrize("sender,body", [
    ("22670000002", "REFUSER WT-FFFFFFFF"),
    ("22670000001", "REFUSER WT-ABCDEF12"),
    ("22679999999", "STOP"),
    ("22670000002", "Je dis STOP dans une phrase"),
])
def test_unrelated_pending_commands_do_not_block(setup, sender, body):
    store, settings = setup
    with store.db:
        store.db.execute("UPDATE agreements SET reference='WT-ABCDEF12'")
    store.enqueue("unrelated", sender, {"type": "text", "text": {"body": body},
                                        "timestamp": str(NOW)})
    assert queue_due_reminders(store, settings, NOW) == 2
