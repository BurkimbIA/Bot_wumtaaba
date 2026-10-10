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


def test_missing_fields_and_overpayment_block_confirmation():
    assert "nom" in Draft(client="  ").question()
    draft = complete()
    draft.paid = 11000
    assert "dépasse" in draft.question()
    with pytest.raises(ValueError):
        draft.summary()


def test_no_implicit_zero_advance():
    draft = Draft(client="Awa", product="riz", total=1000, due_date="2026-10-20")
    assert "déjà été payé" in draft.question()


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
        "help",
    ]
