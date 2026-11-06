"""Deterministic payment dialogue; financial writes are returned to the inbox transaction."""

import re
from datetime import UTC, datetime
from uuid import uuid4

from .domain import buttons, text


def money(amount):
    return f"{amount:,}".replace(",", " ") + " F CFA"


def reference(value):
    match = re.fullmatch(r"(?:wt-)?([a-f0-9]{8})", value.strip(), re.IGNORECASE)
    return "WT-" + match[1].upper() if match else value.strip()


def detail(store, sender, ref, page=0):
    row = store.get_agreement(sender, reference(ref))
    if not row:
        return [text("Aucun accord correspondant dans ton carnet.")]
    payments = store.payment_history(sender, row["id"], offset=page * 5, limit=6)
    lines = [
        f"*{row['reference']} · {row['client']}*",
        row["product"],
        f"Prix : {money(row['total'])}",
        f"Payé : {money(row['paid'])}",
        f"*Reste : {money(row['balance'])}*",
        f"Échéance : {row['due_date']}",
        "\n*Historique des versements*",
    ]
    for payment in payments[:5]:
        stamp = datetime.fromtimestamp(payment["created_at"], UTC).strftime("%d/%m/%Y %H:%M")
        label = {
            "initial": "Avance",
            "legacy_settlement": "Règlement historique déclaré",
            "settlement": "Règlement déclaré",
        }.get(payment["kind"], "Paiement")
        lines.append(f"{stamp} UTC · {label} : {money(payment['amount'])}")
    if not payments:
        lines.append("Aucun versement sur cette page.")
    lines.append("\nVersements déclarés par le commerçant, sans vérification bancaire.")
    choices = [(f"payment:{row['id']}", "Ajouter paiement")] if row["balance"] else []
    if len(payments) > 5:
        choices.append((f"history:{row['id']}:{page + 1}", "Suite historique"))
    choices.append(("list:0", "Mes accords"))
    # Detail is bounded to five rows and stays under WhatsApp's button-body limit.
    return [text("\n".join(lines)), buttons("Que souhaites-tu faire ?", choices)]


def prepare(store, sender, state, body):
    """Accept only an explicit integer amount, never a guessed amount from an LLM."""
    if state.get("mode") == "payment_select":
        ref = reference(body)
        row = store.get_agreement(sender, ref)
        if not row:
            # Search is owner-scoped and limited; homonyms are never chosen automatically.
            with store.lock:
                rows = store.db.execute(
                    "SELECT id,reference,client_name,paid,total FROM agreements WHERE sender=? "
                    "AND instr(search_fold(client_name),search_fold(?))>0 ORDER BY created_at DESC LIMIT 6",
                    (sender, body.strip()[:100]),
                ).fetchall()
            if not rows:
                return (
                    state,
                    [text("Accord introuvable. Envoie sa référence WT-… ou le nom du client.")],
                    None,
                )
            choices = [
                {
                    "id": "payment:" + r["id"],
                    "title": r["reference"],
                    "description": f"{r['client_name'][:30]} · reste {money(r['total'] - r['paid'])}",
                }
                for r in rows
            ]
            return (
                state,
                [
                    {
                        "type": "interactive",
                        "interactive": {
                            "type": "list",
                            "body": {"text": "Choisis précisément l’accord à créditer."},
                            "action": {
                                "button": "Choisir l’accord",
                                "sections": [{"title": "Accords", "rows": choices}],
                            },
                        },
                    }
                ],
                None,
            )
        return start(row)
    row = store.get_agreement(sender, state.get("agreement_id", ""))
    if row is None:
        return {}, [text("Accord introuvable. Reprends depuis Mes accords.")], None
    clean = re.sub(r"[ \u00a0\u202f]", "", body.strip())
    clean = re.sub(r"(?i)(?:fcfa|francscfa|francs)?[.!]?$", "", clean)
    if not re.fullmatch(r"[0-9]{1,10}", clean):
        state.pop("payment", None)
        return (
            state,
            [
                text(
                    "Envoie uniquement un montant entier en F CFA, par exemple 20 000. Aucun paiement enregistré."
                )
            ],
            None,
        )
    amount = int(clean)
    if not 0 < amount <= row["balance"]:
        state.pop("payment", None)
        return (
            state,
            [text(f"Le montant doit être positif et ne pas dépasser {money(row['balance'])}.")],
            None,
        )
    operation = {
        "_operation": "payment",
        "id": uuid4().hex,
        "agreement_id": row["id"],
        "amount": amount,
        "expected_paid": row["paid"],
    }
    state["payment"] = operation
    state.pop("pending_audio", None)
    return (
        state,
        [
            buttons(
                f"*Paiement à vérifier · {row['reference']}*\n{row['client']} · {row['product'][:120]}\n"
                f"Nouveau versement : {money(amount)}\nDéjà payé : {money(row['paid'])}\n"
                f"Payé après validation : {money(row['paid'] + amount)}\n"
                f"*Nouveau reste : {money(row['balance'] - amount)}*\n"
                "Corrige le montant par message ou confirme.",
                [("payconfirm:" + operation["id"], "Enregistrer"), ("paycancel", "Annuler")],
            )
        ],
        None,
    )


def start(row):
    if not row["balance"]:
        return {}, [text("Cet accord est déjà soldé. Aucun paiement ajouté.")], None
    return (
        {"mode": "payment_amount", "agreement_id": row["id"]},
        [
            text(
                f"*{row['reference']} · {row['client']}*\nReste : {money(row['balance'])}\n"
                "Quel montant viens-tu de recevoir ? Envoie le montant entier en F CFA. "
                "Tu peux aussi dicter uniquement le montant dans un vocal. « annuler » pour quitter."
            )
        ],
        None,
    )


def handle(store, sender, state, command, action):
    if action == "payment" or command in {"3", "paiement", "ajouter un paiement"}:
        return (
            {"mode": "payment_select"},
            [text("Envoie la référence WT-… ou le nom du client pour choisir l’accord.")],
            None,
        )
    if action == "paycancel":
        return {}, [text("Paiement annulé. Aucun versement enregistré.")], None
    if action.startswith("payconfirm:"):
        op = state.get("payment")
        if not op or action != "payconfirm:" + op["id"]:
            return (
                state,
                [text("Ce paiement a expiré ou a déjà été enregistré. Utilise le dernier résumé.")],
                None,
            )
        row = store.get_agreement(sender, op["agreement_id"])
        if row is None or row["paid"] != op["expected_paid"] or op["amount"] > row["balance"]:
            return (
                {},
                [
                    text(
                        "Le solde a changé. Recommence pour vérifier le nouveau montant. Aucun paiement ajouté."
                    )
                ],
                None,
            )
        return (
            {},
            [
                text(
                    f"✅ Paiement enregistré : {money(op['amount'])}\n"
                    f"{row['reference']} · {row['client']}\n"
                    f"Reste : {money(row['balance'] - op['amount'])}"
                ),
                buttons("Consulter le détail", [("detail:" + row["id"], "Voir l’historique")]),
            ],
            op,
        )
    if action.startswith("payment:"):
        row = store.get_agreement(sender, action.partition(":")[2])
        return start(row) if row else ({}, [text("Accord introuvable dans ton carnet.")], None)
    if action.startswith(("detail:", "history:")):
        parts = action.split(":")
        try:
            page = max(0, int(parts[2])) if parts[0] == "history" else 0
        except (ValueError, IndexError):
            page = 0
        return state, detail(store, sender, parts[1], page), None
    match = re.fullmatch(r"(?:historique|detail|détail)\s+(\S+)", command)
    if match:
        return state, detail(store, sender, match[1]), None
    return None
