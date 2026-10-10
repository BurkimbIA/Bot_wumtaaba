"""Parcours WhatsApp courts, avec validation explicite avant sauvegarde."""

from copy import deepcopy
from uuid import uuid4

import httpx

from .clients import MediaError
from .domain import Draft, buttons, chunks, menu, text


class Conversation:
    def __init__(self, settings, whatsapp, intelligence, store):
        self.settings = settings
        self.whatsapp = whatsapp
        self.intelligence = intelligence
        self.store = store

    async def handle(self, sender: str, message: dict, state: dict):
        state = deepcopy(state)
        kind = message.get("type")
        body = message.get("text", {}).get("body", "").strip() if kind == "text" else ""
        action = message.get("interactive", {}).get("button_reply", {}).get("id", "")
        command = body.casefold()
        if command == "stop":
            return {}, [text("Conversation arrêtée. Écris « menu » pour reprendre.")], None
        if command in {"menu", "annuler", "bonjour", "salut"}:
            return (
                {},
                [
                    menu(
                        "Brouillon annulé. Que souhaites-tu faire ?"
                        if state
                        else "*Wuumtaaba* 🌿\nTon carnet d’accords. Que souhaites-tu faire ?"
                    )
                ],
                None,
            )
        if action == "new" or command in {"1", "nouvel accord", "nouveau"}:
            return (
                {"mode": "draft", "draft": {}},
                [
                    text(
                        "Décris l’accord en un message ou un vocal : client, produit, prix total, "
                        "avance et date prévue. Exemple : Awa, 2 sacs de riz, 30 000 F, "
                        "10 000 F payés, solde le 20 décembre 2026.\n\n"
                        "Le texte est analysé par OpenAI pour préparer le résumé. "
                        "Vérifie-le avant d’enregistrer. Écris « annuler » pour quitter."
                    )
                ],
                None,
            )
        if action == "help" or command in {"3", "aide"}:
            return (
                state,
                [
                    text(
                        "1 — Nouvel accord : décris ta vente puis vérifie et confirme le résumé.\n"
                        "2 — Mes accords : consulte tes accords enregistrés.\n"
                        "« menu » ou « annuler » : abandonner le brouillon.\n\n"
                        "Les accords sont des notes personnelles, pas une validation du client. "
                        "Les textes sont analysés par OpenAI. Chaque vocal demande ton accord avant "
                        "téléchargement et transcription via OpenAI. "
                        f"Pilote : accords et messages supprimés automatiquement après "
                        f"{self.settings.retention_days} jours. "
                        f"Assistance : {self.settings.support_contact}."
                    ),
                    menu(),
                ],
                None,
            )
        if action.startswith("list:") or command in {"2", "mes accords"}:
            try:
                offset = max(0, int(action.split(":", 1)[1])) if action else 0
            except ValueError:
                return state, [menu()], None
            records = self.store.list_agreements(sender, offset=offset, limit=6)
            if not records:
                return state, [menu("Aucun accord enregistré sur cette page.")], None
            lines = ["*Tes accords enregistrés*"]
            for record in records[:5]:
                draft = Draft.model_validate({k: record.get(k) for k in Draft.model_fields})
                lines.append(
                    "Réf. WT-"
                    + record["id"][:8].upper()
                    + "\n"
                    + draft.summary().replace("Accord à vérifier", "Accord enregistré")
                )
            replies = chunks("\n\n".join(lines))
            if len(records) > 5:
                replies.append(
                    buttons("Voir les accords suivants", [(f"list:{offset + 5}", "Suivants")])
                )
            replies.append(menu())
            return state, replies, None
        if action.startswith("confirm:"):
            token = action.partition(":")[2]
            if not state.get("confirmation") or token != state["confirmation"]:
                return state, [text("Ce résumé a expiré. Utilise le dernier résumé reçu.")], None
            draft = Draft.model_validate(state.get("draft", {}))
            if draft.question():
                return state, [text(draft.question())], None
            agreement = {"id": token, **draft.model_dump(mode="json")}
            return (
                {},
                [text("✅ Accord enregistré. Réf. WT-" + token[:8].upper()), menu()],
                agreement,
            )
        if kind == "audio":
            media_id = message.get("audio", {}).get("id", "")
            if not media_id.isdigit():
                return (
                    state,
                    [text("Ce vocal est invalide. Renvoie une note vocale WhatsApp.")],
                    None,
                )
            token = uuid4().hex
            state.pop("confirmation", None)
            state["pending_audio"] = {"id": media_id, "token": token}
            return (
                state,
                [
                    buttons(
                        "Pour ce vocal, autorises-tu Wuumtaaba à le télécharger depuis Meta et à "
                        "le transmettre à OpenAI pour une transcription en français ? Le fichier "
                        "n’est pas conservé sur notre serveur. Le texte sera affiché ici.",
                        [(f"audio:{token}", "Accepter"), (f"refuse:{token}", "Refuser")],
                    )
                ],
                None,
            )
        if action.startswith(("audio:", "refuse:")):
            token = action.partition(":")[2]
            pending = state.get("pending_audio", {})
            if token != pending.get("token"):
                return state, [text("Cette demande audio a expiré. Renvoie ton vocal.")], None
            if action.startswith("refuse:"):
                state.pop("pending_audio", None)
                return (
                    state,
                    [text("Vocal non traité. Tu peux écrire ton accord ou envoyer « menu ».")],
                    None,
                )
            try:
                audio = await self.whatsapp.audio(pending["id"])
                transcript = await self.intelligence.transcribe(*audio)
            except MediaError as error:
                state.pop("pending_audio", None)
                return state, [text(str(error))], None
            state.pop("pending_audio", None)
            prefix = chunks("🎙️ *Transcription française*\n" + transcript)
            # Une transcription seule est utile sans forcer la création d'un accord.
            if state.get("mode") != "draft":
                return state, prefix + [menu()], None
            return await self._extract(transcript, state, prefix)
        if body and state.get("mode") == "draft":
            state.pop("pending_audio", None)
            return await self._extract(body, state, [])
        if action:
            return (
                state,
                [text("Ce bouton n’est plus disponible. Écris « menu » pour reprendre.")],
                None,
            )
        return state, [menu("Choisis une action, ou envoie un vocal à transcrire.")], None

    async def _extract(self, body, state, replies):
        # Toute correction invalide le bouton du résumé précédent.
        state.pop("confirmation", None)
        try:
            draft = await self.intelligence.extract(body, state.get("draft", {}))
        except MediaError as error:
            return state, replies + [text(str(error))], None
        except (ValueError, httpx.HTTPError):
            return (
                state,
                replies
                + [
                    text(
                        "Impossible de préparer le résumé. "
                        "Renvoie ta description ou ta correction. Aucun accord enregistré."
                    )
                ],
                None,
            )
        state["draft"] = draft.model_dump(mode="json")
        question = draft.question()
        if question:
            return state, replies + [text(question)], None
        token = uuid4().hex
        state["confirmation"] = token
        return (
            state,
            replies
            + [
                buttons(
                    draft.summary() + "\n\nCorrige par message si besoin, puis confirme.",
                    [(f"confirm:{token}", "Enregistrer"), ("new", "Recommencer")],
                )
            ],
            None,
        )
