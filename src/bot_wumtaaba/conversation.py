"""Parcours WhatsApp courts, avec validation explicite avant sauvegarde."""

import re
from copy import deepcopy
from uuid import uuid4

import httpx

from .clients import MediaError
from .domain import Draft, buttons, chunks, menu, text
from .media import archive_audio


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
        public_access = getattr(self.settings, "public_access", False)
        customer_only = not public_access and sender not in self.settings.senders and self.store.is_known_customer(sender)
        customer_help = text(
            "Tu peux envoyer un vocal à transcrire. Pour autoriser les rappels, "
            "envoie ACCEPTER WT-référence ; pour les retirer : REFUSER WT-référence. "
            "STOP désactive tous les rappels pour ton numéro ; « rappels oui » les réactive. "
            "Après STOP, confirme aussi chaque référence avec ACCEPTER."
        )
        if customer_only:
            # A merchant draft from an earlier role must never permit customer-side creation.
            state = {"pending_audio": state["pending_audio"]} if "pending_audio" in state else {}
        if command == "stop":
            self.store.set_reminders_enabled(sender, False)
            return {}, [text("Conversation arrêtée et rappels désactivés pour ton numéro. "
                             + ("Pour reprendre : « rappels oui », puis ACCEPTER suivi de chaque référence."
                                if customer_only else
                                "Écris « menu » pour reprendre, ou « rappels oui » pour "
                                "réactiver tes rappels commerçant."))], None
        if command == "rappels oui" and (public_access or sender in self.settings.senders or customer_only):
            self.store.set_reminders_enabled(sender, True)
            return state, [text("Rappels réactivés pour ton numéro. Pour un accord où tu es client, "
                                "envoie aussi ACCEPTER suivi de sa référence.")], None
        consent = re.fullmatch(r"(accepter|refuser)\s+(?:wt-)?([a-f0-9]{8})", command)
        if consent:
            granted = consent[1] == "accepter"
            matched = self.store.set_customer_consent(sender, "WT-" + consent[2].upper(), granted)
            reply = (
                ("Consentement enregistré pour cette référence. Si tu as envoyé STOP, "
                 "envoie aussi « rappels oui » pour réactiver ton numéro. "
                 "REFUSER suivi de la référence retire ce consentement.")
                if granted else "Rappels refusés pour cette référence."
            ) if matched else "Référence introuvable pour ton numéro. Vérifie avec le commerçant."
            if matched and granted and not self.store.reminders_enabled(sender):
                reply = ("Consentement enregistré, mais tes rappels restent désactivés après STOP. "
                         "Envoie « rappels oui » pour réactiver ton numéro.")
            return state, [text(reply)], None
        if customer_only and kind != "audio" and not action.startswith(("audio:", "refuse:")):
            return {}, [customer_help], None
        settled = re.fullmatch(r"(?:regle|réglé|paye|payé)\s+(?:wt-)?([a-f0-9]{8})", command)
        if settled:
            matched = self.store.settle_agreement(sender, "WT-" + settled[1].upper())
            return state, [text("Accord marqué réglé et rappels annulés. Le montant de "
                                "l’avance d’origine reste conservé dans l’historique."
                                if matched else "Aucun accord correspondant dans ton carnet.")], None
        if command == "calendrier":
            records = self.store.calendar_for(sender)
            lines = ["*Ton calendrier d’échéances*"]
            for record in records:
                lines.append(f"{record['due_date']} — {record['client']} — "
                             f"WT-{record['id'][:8].upper()} — {record.get('status', 'prévu')}")
            if not records:
                lines.append("Aucune échéance à venir.")
            return state, chunks("\n".join(lines)), None
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
                        "avance et date prévue. Tu peux ajouter le numéro international du client "
                        "avec son indicatif (optionnel). Exemple : Awa, 2 sacs de riz, 30 000 F, "
                        "10 000 F payés, solde le 20 décembre 2026.\n\n"
                        "Le texte est analysé par Google Gemini pour préparer le résumé. "
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
                        "« calendrier » : échéances à venir.\n"
                        "« réglé WT-référence » : clôturer un accord et annuler ses rappels.\n"
                        "« STOP » : désactiver tes rappels ; « rappels oui » : les réactiver.\n"
                        "« menu » ou « annuler » : abandonner le brouillon.\n\n"
                        "Les accords sont des notes personnelles, pas une validation du client. "
                        "Les textes sont analysés par Google Gemini. Chaque vocal demande ton accord avant "
                        "téléchargement, conservation privée et transcription via Google Gemini. "
                        + (f"Historique conservé {self.settings.retention_days} jours. "
                           if self.settings.retention_days else "Historique conservé sans purge automatique. ")
                        + f"Assistance : {self.settings.support_contact}."
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
                [text("✅ Accord enregistré. Réf. WT-" + token[:8].upper()
                      + "\nÉchéance ajoutée au calendrier. Les rappels aux deux numéros "
                        "seront activés seulement après accord du client."
                      + ("\nDemande au client d’envoyer depuis son numéro : ACCEPTER WT-"
                         + token[:8].upper() + " à ce bot." if draft.client_phone else
                         "\nSans numéro client, aucun rappel n’est envoyé.")), menu()],
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
            state["pending_audio"] = {"id": media_id, "token": token, "message_id": message.get("id") or token}
            return (
                state,
                [
                    buttons(
                        "Pour ce vocal, autorises-tu Wuumtaaba à le télécharger depuis Meta et à "
                        "le transmettre à Google Gemini pour une transcription en français ? Le fichier "
                        "sera conservé en privé avec sa transcription dans ton historique. "
                        "Le texte sera affiché ici.",
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
                    [text("Vocal non traité. Tu peux envoyer un autre message.")],
                    None,
                )
            try:
                audio = await self.whatsapp.audio(pending["id"])
                metadata = archive_audio(self.settings.media_directory, pending["message_id"],
                                         audio[0], audio[2])
                metadata["media_id"] = pending["id"]
                self.store.record_media(pending["message_id"], metadata=metadata)
                transcript = await self.intelligence.transcribe(*audio)
                self.store.record_media(pending["message_id"],
                                        metadata={"status": "transcribed"}, transcript=transcript)
            except (MediaError, ValueError, httpx.HTTPError, OSError) as error:
                self.store.record_media(pending["message_id"], metadata={
                    "media_id": pending["id"], "status": "failed", "error": type(error).__name__
                })
                state.pop("pending_audio", None)
                return state, [text(str(error) if isinstance(error, MediaError) else
                                    "Impossible de traiter ce vocal. Renvoie-le pour réessayer.")], None
            state.pop("pending_audio", None)
            prefix = chunks("🎙️ *Transcription française*\n" + transcript)
            # Une transcription seule est utile sans forcer la création d'un accord.
            if state.get("mode") != "draft":
                return state, prefix + [customer_help if customer_only else menu()], None
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
