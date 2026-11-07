"""Parcours WhatsApp courts, avec validation explicite avant sauvegarde."""

import re
from copy import deepcopy
from uuid import uuid4

import httpx

from . import payments
from .agreement_voice import moore_spoken_summary, pieces, spoken_summary, translate_all
from .clients import MediaError
from .domain import Draft, buttons, chunks, menu, text
from .media import archive_audio, join_voice_parts
from .mobile import pairing_operation


class Conversation:
    def __init__(self, settings, whatsapp, intelligence, store, media=None, burkimbia=None):
        self.settings = settings
        self.media = media
        self.whatsapp = whatsapp
        self.intelligence = intelligence
        self.store = store
        self.burkimbia = burkimbia

    async def handle(self, sender: str, message: dict, state: dict):
        state = deepcopy(state)
        kind = message.get("type")
        body = message.get("text", {}).get("body", "").strip() if kind == "text" else ""
        action = (
            message.get("interactive", {})
            .get("button_reply", message.get("interactive", {}).get("list_reply", {}))
            .get("id", "")
        )
        command = body.casefold()
        public_access = getattr(self.settings, "public_access", False)
        customer_only = (
            not public_access
            and sender not in self.settings.senders
            and self.store.is_known_customer(sender)
        )
        customer_help = text(
            "Tu peux envoyer un vocal à transcrire. Pour autoriser les rappels, "
            "envoie ACCEPTER WT-référence ; pour les retirer : REFUSER WT-référence. "
            "STOP désactive tous les rappels pour ton numéro ; « rappels oui » les réactive. "
            "Après STOP, confirme aussi chaque référence avec ACCEPTER."
        )
        if customer_only:
            # A merchant draft from an earlier role must never permit customer-side creation.
            state = {}
        if command == "stop":
            self.store.set_reminders_enabled(sender, False)
            return (
                {},
                [
                    text(
                        "Conversation arrêtée et rappels désactivés pour ton numéro. "
                        + (
                            "Pour reprendre : « rappels oui », puis ACCEPTER suivi de chaque référence."
                            if customer_only
                            else "Écris « menu » pour reprendre, ou « rappels oui » pour "
                            "réactiver tes rappels commerçant."
                        )
                    )
                ],
                None,
            )
        if command == "rappels oui" and (
            public_access or sender in self.settings.senders or customer_only
        ):
            self.store.set_reminders_enabled(sender, True)
            return (
                state,
                [
                    text(
                        "Rappels réactivés pour ton numéro. Pour un accord où tu es client, "
                        "envoie aussi ACCEPTER suivi de sa référence."
                    )
                ],
                None,
            )
        consent = re.fullmatch(r"(accepter|refuser)\s+(?:wt-)?([a-f0-9]{8})", command)
        if consent:
            granted = consent[1] == "accepter"
            matched = self.store.set_customer_consent(sender, "WT-" + consent[2].upper(), granted)
            reply = (
                (
                    (
                        "Consentement enregistré pour cette référence. Si tu as envoyé STOP, "
                        "envoie aussi « rappels oui » pour réactiver ton numéro. "
                        "REFUSER suivi de la référence retire ce consentement."
                    )
                    if granted
                    else "Rappels refusés pour cette référence."
                )
                if matched
                else "Référence introuvable pour ton numéro. Vérifie avec le commerçant."
            )
            if matched and granted and not self.store.reminders_enabled(sender):
                reply = (
                    "Consentement enregistré, mais tes rappels restent désactivés après STOP. "
                    "Envoie « rappels oui » pour réactiver ton numéro."
                )
            return state, [text(reply)], None
        if customer_only and kind != "audio" and not action.startswith(("audio:", "refuse:")):
            return {}, [customer_help], None
        if command in {"application", "appli"}:
            operation, code = pairing_operation(sender)
            return (
                state,
                [
                    text(
                        "*Connecter l’application Wuumtaaba*\n"
                        "Ton code personnel (valable 5 minutes, une seule utilisation) :\n"
                        + code
                        + "\nSaisis-le uniquement dans ton application. Ne le partage pas.\n"
                        "« deconnecter application » révoque tous les appareils."
                    )
                ],
                operation,
            )
        if command in {"deconnecter application", "déconnecter application"}:
            return (
                state,
                [text("Tous tes accès à l’application ont été déconnectés.")],
                {"_operation": "mobile_revoke"},
            )
        payment_result = payments.handle(self.store, sender, state, command, action)
        if payment_result is not None:
            return payment_result
        if (
            body
            and state.get("mode") in {"payment_select", "payment_amount"}
            and command not in {"menu", "annuler", "bonjour", "salut"}
        ):
            state.pop("pending_audio", None)
            return payments.prepare(self.store, sender, state, body)
        settled = re.fullmatch(r"(?:regle|réglé|paye|payé)\s+(?:wt-)?([a-f0-9]{8})", command)
        if settled:
            matched = self.store.settle_agreement(sender, "WT-" + settled[1].upper())
            return (
                state,
                [
                    text(
                        "Accord marqué réglé : versement du solde ajouté à l’historique et rappels annulés."
                        if matched
                        else "Aucun accord correspondant dans ton carnet."
                    )
                ],
                None,
            )
        if command == "calendrier":
            records = self.store.calendar_for(sender)
            lines = ["*Ton calendrier d’échéances*"]
            for record in records:
                lines.append(
                    f"{record['due_date']} — {record['client']} — "
                    f"WT-{record['id'][:8].upper()} — {record.get('status', 'prévu')}"
                )
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
            state = {"mode": "draft", "draft": {}, "language_choice": uuid4().hex}
            return state, [self._language_menu(state)], None
        if state.get("language_choice"):
            aliases = {
                "français": "french",
                "francais": "french",
                "french": "french",
                "mooré": "moore",
                "moore": "moore",
            }
            language = aliases.get(command)
            if action.startswith("language:"):
                parts = action.split(":")
                if len(parts) == 3 and parts[2] == state["language_choice"]:
                    language = parts[1]
            if language not in {"french", "moore"}:
                return state, [self._language_menu(state)], None
            state.pop("language_choice")
            state["language"] = language
            return state, [text(self._entry_prompt(language))], None
        if action.startswith("language:"):
            return state, [text("Ce choix de langue a expiré. Reprends avec Nouvel accord.")], None
        if action.startswith("read:"):
            return await self._read_summary(sender, message, state, action)
        if action == "help" or command == "aide":
            return (
                state,
                [
                    text(
                        "1 — Nouvel accord : choisis français ou mooré, décris ta vente puis vérifie le résumé.\n"
                        "« Écouter » : lecture facultative du résumé avant de confirmer.\n"
                        "2 — Mes accords : consulte tes accords enregistrés.\n"
                        "3 — Ajouter un paiement : choisis l’accord, saisis le montant, confirme.\n"
                        "« historique WT-référence » : détail des versements.\n"
                        "« application » : connecter ton carnet mobile.\n"
                        "« calendrier » : échéances à venir.\n"
                        "« réglé WT-référence » : clôturer un accord et annuler ses rappels.\n"
                        "« STOP » : désactiver tes rappels ; « rappels oui » : les réactiver.\n"
                        "« menu » ou « annuler » : abandonner le brouillon.\n\n"
                        "Les accords sont des notes personnelles, pas une validation du client. "
                        "Les textes sont analysés par Google Gemini. Les vocaux sont traités directement "
                        "et conservés en privé avec leur transcription. La voix française utilise Google Gemini ; "
                        "le mooré utilise BurkimbIA pour transcrire et traduire avant extraction par Gemini. "
                        + (
                            f"Historique conservé {self.settings.retention_days} jours. "
                            if self.settings.retention_days
                            else "Historique conservé sans purge automatique. "
                        )
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
            rows = []
            for record in records[:5]:
                ref = "WT-" + record["id"][:8].upper()
                client = record.get("client") or "Client non renseigné"
                balance = Draft.model_validate(
                    {key: record[key] for key in Draft.model_fields if key in record}
                ).balance
                lines.append(
                    f"{ref} · {client} · reste {payments.money(balance)}"
                )
                rows.append(
                    {
                        "id": "detail:" + record["id"],
                        "title": ref,
                        "description": f"{client[:30]} · {payments.money(balance)}",
                    }
                )
            replies = chunks("\n".join(lines))
            replies.append(
                {
                    "type": "interactive",
                    "interactive": {
                        "type": "list",
                        "body": {
                            "text": "Ouvre un accord pour voir ses versements ou ajouter un paiement."
                        },
                        "action": {
                            "button": "Détails des accords",
                            "sections": [{"title": "Accords", "rows": rows}],
                        },
                    },
                }
            )
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
            agreement = {
                "id": token,
                **draft.model_dump(mode="json"),
                "language": state.get("language", "french"),
            }
            return (
                {},
                [
                    text(
                        "✅ Accord enregistré. Réf. WT-"
                        + token[:8].upper()
                        + (
                            "\nÉchéance ajoutée au calendrier. Les rappels aux deux numéros "
                            "seront activés seulement après accord du client."
                            if draft.due_date and draft.balance is not None
                            else "\nEnregistré avec les informations disponibles. "
                            "Aucun rappel programmé sans date et montants renseignés."
                        )
                        + (
                            "\nDemande au client d’envoyer depuis son numéro : ACCEPTER WT-"
                            + token[:8].upper()
                            + " à ce bot."
                            if draft.client_phone
                            else "\nSans numéro client, aucun rappel n’est envoyé."
                        )
                    ),
                    menu(),
                ],
                agreement,
            )
        if action.startswith(("audio:", "refuse:")):
            state.pop("pending_audio", None)
            return state, [text("Cette demande audio a expiré. Renvoie ton vocal.")], None
        if kind == "audio":
            # A new voice correction invalidates any older payment summary immediately.
            state.pop("payment", None)
            media_id = message.get("audio", {}).get("id", "")
            if not media_id.isdigit():
                return (
                    state,
                    [text("Ce vocal est invalide. Renvoie une note vocale WhatsApp.")],
                    None,
                )
            state.pop("pending_audio", None)
            state.pop("confirmation", None)
            language = state.get("language", "french") if state.get("mode") == "draft" else "french"
            pending = {
                "id": media_id,
                "message_id": message.get("id") or uuid4().hex,
                "language": language,
            }
            try:
                audio = await self.whatsapp.audio(pending["id"])
                metadata = await archive_audio(
                    self.media, pending["message_id"], audio[0], audio[2]
                )
                language = pending.get("language", "french")
                metadata.update(
                    {
                        "media_id": pending["id"],
                        "language": language,
                        "provider": "burkimbia" if language == "moore" else "gemini",
                    }
                )
                self.store.record_media(pending["message_id"], metadata=metadata)
                if language == "moore":
                    if self.burkimbia is None:
                        raise MediaError(
                            "Les modèles mooré ne sont pas configurés. Réessaie plus tard."
                        )
                    transcript = await self.burkimbia.transcribe(*audio, language="moore")
                else:
                    transcript = await self.intelligence.transcribe(*audio)
                self.store.record_media(
                    pending["message_id"], metadata={"status": "transcribed"}, transcript=transcript
                )
            except (MediaError, ValueError, httpx.HTTPError, OSError) as error:
                self.store.record_media(
                    pending["message_id"],
                    metadata={
                        "media_id": pending["id"],
                        "status": "failed",
                        "error": type(error).__name__,
                    },
                )
                state.pop("pending_audio", None)
                return (
                    state,
                    [
                        text(
                            str(error)
                            if isinstance(error, MediaError)
                            else "Impossible de traiter ce vocal. Renvoie-le pour réessayer."
                        )
                    ],
                    None,
                )
            state.pop("pending_audio", None)
            prefix = chunks(
                (
                    "🎙️ *Transcription mooré — BurkimbIA*\n"
                    if language == "moore"
                    else "🎙️ *Transcription française*\n"
                )
                + transcript
            )
            # A transcription never creates an agreement without a draft + confirmation.
            if state.get("mode") == "payment_amount":
                new_state, replies, operation = payments.prepare(
                    self.store, sender, state, transcript
                )
                return new_state, prefix + replies, operation
            if state.get("mode") != "draft":
                return state, prefix + [customer_help if customer_only else menu()], None
            if language == "moore":
                try:
                    french = await translate_all(self.burkimbia, transcript, "moore", "french")
                    self.store.record_media(
                        pending["message_id"],
                        metadata={"translation_french": french, "status": "translated"},
                    )
                except (MediaError, ValueError, httpx.HTTPError) as error:
                    return (
                        state,
                        prefix
                        + [
                            text(
                                str(error)
                                if isinstance(error, MediaError)
                                else "La traduction n’a pas abouti. Renvoie un message plus court. Aucun accord enregistré."
                            )
                        ],
                        None,
                    )
                prefix += chunks("🇫🇷 *Traduction française à vérifier*\n" + french)
                return await self._extract(french, state, prefix)
            return await self._extract(transcript, state, prefix)
        if body and state.get("mode") == "draft":
            state.pop("pending_audio", None)
            state.pop("confirmation", None)
            if state.get("language") == "moore":
                try:
                    french = await translate_all(self.burkimbia, body, "moore", "french")
                except (MediaError, ValueError, httpx.HTTPError) as error:
                    return (
                        state,
                        [
                            text(
                                str(error)
                                if isinstance(error, MediaError)
                                else "La traduction n’a pas abouti. Aucun accord enregistré."
                            )
                        ],
                        None,
                    )
                return await self._extract(
                    french, state, chunks("🇫🇷 *Traduction française à vérifier*\n" + french)
                )
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
                    [
                        (f"confirm:{token}", "Enregistrer"),
                        ("new", "Recommencer"),
                        (
                            f"read:{token}",
                            "Écouter en mooré"
                            if state.get("language") == "moore"
                            else "Écouter en français",
                        ),
                    ],
                )
            ],
            None,
        )

    @staticmethod
    def _language_menu(state):
        token = state["language_choice"]
        return buttons(
            "*Nouvel accord*\nDans quelle langue souhaites-tu décrire ta vente ?",
            [(f"language:french:{token}", "Français"), (f"language:moore:{token}", "Mooré")],
        )

    @staticmethod
    def _entry_prompt(language):
        return (
            "*Langue choisie : " + ("mooré" if language == "moore" else "français") + "*\n"
            "Envoie un vocal ou un message avec les détails de ton accord, "
            "par exemple le produit, le montant ou la date prévue.\n"
            "Écris « annuler » pour quitter."
        )

    async def _read_summary(self, sender, message, state, action):
        token = state.get("confirmation")
        if state.get("mode") != "draft" or not token or action != "read:" + token:
            return (
                state,
                [text("Ce résumé a expiré. Utilise le bouton du dernier résumé reçu.")],
                None,
            )
        try:
            draft = Draft.model_validate(state.get("draft", {}))
            source = spoken_summary(draft)
            language = state.get("language", "french")
            if language == "moore":
                spoken = await moore_spoken_summary(self.burkimbia, draft)
                parts = [
                    await self.burkimbia.synthesize(part, language="moore")
                    for part in pieces(spoken)
                ]
                audio = await join_voice_parts(parts)
                provider = "burkimbia"
            else:
                spoken = source
                audio = await self.intelligence.synthesize_french(spoken)
                provider = "gemini"
            metadata = await archive_audio(
                self.media, message.get("id") or uuid4().hex, audio[0], audio[2]
            )
            metadata.update(
                {
                    "purpose": "agreement_summary",
                    "provider": provider,
                    "language": language,
                    "source_text": source,
                    "spoken_text": spoken,
                    "confirmation": token,
                }
            )
            media_id = await self.whatsapp.upload_audio(*audio)
            return (
                state,
                [
                    {"type": "audio", "audio": {"id": media_id}, "_local_audio": metadata},
                    text(
                        "🔊 Résumé à vérifier. L’écoute ne valide pas l’accord. "
                        "Corrige par message ou utilise Enregistrer sur le dernier résumé."
                    ),
                ],
                None,
            )
        except (MediaError, ValueError, httpx.HTTPError, OSError, TimeoutError) as error:
            return (
                state,
                [
                    text(
                        str(error)
                        if isinstance(error, MediaError)
                        else "La lecture audio est momentanément indisponible. Le résumé écrit reste à vérifier. "
                        "Aucun accord enregistré."
                    )
                ],
                None,
            )
