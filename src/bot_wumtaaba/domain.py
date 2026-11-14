"""Validation métier déterministe. Aucun solde n'est calculé par l'IA."""

import re
from datetime import date

from pydantic import BaseModel, ConfigDict, Field, StrictInt, field_validator


class Draft(BaseModel):
    model_config = ConfigDict(extra="forbid", str_strip_whitespace=True)
    key_points: str | None = Field(default=None, max_length=500)
    client: str | None = Field(default=None, max_length=100)
    client_phone: str | None = None
    product: str | None = Field(default=None, max_length=200)
    total: StrictInt | None = Field(default=None, ge=1, le=1_000_000_000)
    paid: StrictInt | None = Field(default=None, ge=0, le=1_000_000_000)
    due_date: date | None = None

    @field_validator("client_phone", mode="before")
    @classmethod
    def international_phone(cls, value):
        if value is None:
            return None
        if not isinstance(value, str) or not re.fullmatch(r"\+?[1-9]\d{7,14}", value.strip()):
            raise ValueError("Numéro international requis, indicatif compris, sans espaces")
        return value.strip().lstrip("+")

    @field_validator("due_date", mode="before")
    @classmethod
    def iso_date(cls, value):
        if value is None or type(value) is date:
            return value
        if not isinstance(value, str) or not re.fullmatch(r"\d{4}-\d{2}-\d{2}", value):
            raise ValueError("La date doit être au format YYYY-MM-DD")
        return value

    def question(self) -> str | None:
        if not any(value is not None and value != "" for value in self.model_dump().values()):
            return "Je n’ai pas pu résumer ce message. Peux-tu préciser les éléments à retenir ?"
        if self.paid is not None and self.total is not None and self.paid > self.total:
            return "L’avance dépasse le prix total. Corrige le prix ou le montant déjà payé."
        return None

    @property
    def balance(self) -> int | None:
        if self.total is None or self.paid is None:
            return None
        return self.total - self.paid

    def summary(self) -> str:
        if self.question():
            raise ValueError("Accord incomplet")
        def money(n):
            if n is None:
                return "Non renseigné"
            return f"{n:,}".replace(",", " ") + " F CFA"

        return (
            (f"*Éléments clés à vérifier*\n{self.key_points}\n\n" if self.key_points else "*Accord à vérifier*\n")
            + f"Client : {self.client or 'Non renseigné'}\n"
            f"Produit : {self.product or 'Non renseigné'}\n"
            f"Téléphone client : {self.client_phone or 'non renseigné'}\n"
            f"Prix : {money(self.total)}\nDéjà payé : {money(self.paid)}\n"
            f"*Reste : {money(self.balance)}*\n"
            f"Date prévue : {self.due_date.strftime('%d/%m/%Y') if self.due_date else 'Non renseignée'}"
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
    return buttons(body, [("new", "Nouvel accord"), ("list:0", "Mes accords"), ("payment", "Ajouter paiement")])
