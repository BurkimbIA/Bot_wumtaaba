from datetime import datetime

import pytest
from pydantic import ValidationError

from bot_wumtaaba.domain import Draft, buttons, chunks, menu


def complete(**extra):
    return Draft(
        client=" Awa ", product="riz", total=10000, paid=2500, due_date="2026-10-20", **extra
    )


def test_summary_computes_balance_locally():
    draft = complete()
    assert draft.question() is None
    assert draft.client == "Awa"
    assert "Reste : 7 500 F CFA" in draft.summary()
    assert "20/10/2026" in draft.summary()


@pytest.mark.parametrize(
    "values",
    [
        {"total": True},
        {"total": "100"},
        {"total": 1.5},
        {"total": 0},
        {"paid": -1},
        {"total": 1000000001},
        {"solde": 100},
        {"due_date": "2026-02-30"},
        {"due_date": 1700000000},
        {"due_date": datetime(2026, 10, 20)},
        {"client": "a" * 101},
    ],
)
def test_invalid_values_rejected(values):
    with pytest.raises(ValidationError):
        Draft(**values)


def test_empty_draft_and_overpayment_block_confirmation():
    assert "préciser les éléments" in Draft(client="  ").question()
    draft = complete()
    draft.paid = 11000
    assert "dépasse" in draft.question()
    with pytest.raises(ValueError):
        draft.summary()


def test_no_implicit_zero_advance():
    draft = Draft(client="Awa", product="riz", total=1000, due_date="2026-10-20")
    assert draft.question() is None
    assert draft.paid is None and draft.balance is None
    assert "Déjà payé : Non renseigné" in draft.summary()
    assert "Reste : Non renseigné" in draft.summary()


def test_partial_agreement_is_ready_without_invented_values():
    draft = Draft(product="riz")
    assert draft.question() is None
    assert draft.client is None and draft.total is None and draft.due_date is None
    assert "Produit : riz" in draft.summary()
    assert "Client : Non renseigné" in draft.summary()
    assert "Prix : Non renseigné" in draft.summary()
    assert "Date prévue : Non renseignée" in draft.summary()


def test_key_points_without_sale_fields_can_be_reviewed_and_restored():
    draft = Draft(key_points="Le message mentionne dix jours ; la traduction est à clarifier.")
    restored = Draft.model_validate_json(draft.model_dump_json())
    assert restored.question() is None
    assert restored.key_points in restored.summary()
    assert restored.total is None and restored.due_date is None


def test_blank_key_points_do_not_make_an_empty_message_confirmable():
    assert Draft(key_points="  ").question() is not None


def test_chunks_preserve_transcription_without_loss():
    original = "bonjour 🌿" * 1000
    messages = chunks(original)
    assert all(len(m["text"]["body"]) <= 3500 for m in messages)
    assert "".join(m["text"]["body"] for m in messages) == original


@pytest.mark.parametrize(
    "options",
    [
        [],
        [("id", "x" * 21)],
        [("", "Ok")],
        [("a", "Ok"), ("a", "Oui")],
        [(str(i), "Ok") for i in range(4)],
    ],
)
def test_invalid_buttons_rejected(options):
    with pytest.raises(ValueError):
        buttons("hello", options)


def test_menu_has_three_working_choices():
    assert [b["reply"]["id"] for b in menu()["interactive"]["action"]["buttons"]] == [
        "new",
        "list:0",
        "payment",
    ]


@pytest.mark.parametrize("phone", ["+22670123456", "22670123456"])
def test_optional_phone_normalized(phone):
    draft = complete(client_phone=phone)
    assert draft.client_phone == "22670123456"
    assert "22670123456" in draft.summary()
    assert complete().question() is None


@pytest.mark.parametrize("phone", ["070123456", "+33 605949083", "123", 22670123456, "abc"])
def test_phone_never_invents_country(phone):
    with pytest.raises(ValidationError):
        complete(client_phone=phone)
