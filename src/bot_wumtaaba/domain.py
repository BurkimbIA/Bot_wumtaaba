"""Validation métier déterministe. Aucun solde n'est calculé par l'IA."""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    client: str | None = Field(default=None, max_length=100)
    product: str | None = Field(default=None, max_length=200)
    total: StrictInt | None = Field(default=None, ge=1, le=1_000_000_000)
    paid: StrictInt | None = Field(default=None, ge=0, le=1_000_000_000)
    due_date: date | None = None

    @field_validator("due_date", mode="before")
    @classmethod
    def iso_date(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("La date doit être au format YYYY-MM-DD")
        return value

    def question(self) -> str | None:
        for key, question in (
            ("client", "Quel est le nom du client ?"),
            ("product", "Quel produit as-tu vendu ?"),
            ("total", "Quel est le prix total en F CFA ?"),
            ("paid", "Combien a déjà été payé ? Écris 0 s’il n’y a pas d’avance."),
            ("due_date", "Quelle est la date prévue ? Indique le jour, le mois et l’année."),
        ):
            value = getattr(self, key)
            if value is None or (isinstance(value, str) and not value.strip()):
                return question
        if self.paid > self.total:
            return "L’avance dépasse le prix total. Corrige le prix ou le montant déjà payé."
        return None

    def summary(self) -> str:
        if self.question():
            raise ValueError("Accord incomplet")
        money = lambda n: f"{n:,}".replace(",", " ") + " F CFA"
        return (
            f"*Accord à vérifier*\nClient : {self.client}\nProduit : {self.product}\n"
            f"Prix : {money(self.total)}\nDéjà payé : {money(self.paid)}\n"
            f"*Reste : {money(self.total - self.paid)}*\n"
            f"Date prévue : {self.due_date.strftime('%d/%m/%Y')}\n\n"
            "Note personnelle — non confirmée par le client."
        )


def text(body: str) -> dict:
    return {"type": "text", "text": {"body": body[:4000], "preview_url": False}}


def chunks(body: str) -> list[dict]:
    return [text(body[i : i + 3500]) for i in range(0, len(body), 3500)]


def buttons(body: str, options: list[tuple[str, str]]) -> dict:
    if (
        not 1 <= len(options) <= 3
        or any(
            not label.strip() or len(label) > 20 or not key or len(key) > 256
            for key, label in options
        )
        or len({key for key, _ in options}) != len(options)
    ):
        raise ValueError("Limites des boutons WhatsApp")
    return {
        "type": "interactive",
        "interactive": {
            "type": "button",
            "body": {"text": body[:1024]},
            "action": {
                "buttons": [
                    {"type": "reply", "reply": {"id": key, "title": label}}
                    for key, label in options
                ]
            },
        },
    }


def menu(body: str = "*Wuumtaaba* 🌿\nTon carnet d’accords. Que souhaites-tu faire ?") -> dict:
    return buttons(body, [("new", "Nouvel accord"), ("list:0", "Mes accords"), ("help", "Aide")])
