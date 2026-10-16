"""Two independent WhatsApp experiences with durable, explicit navigation."""

import re
from copy import deepcopy
from uuid import uuid4

import httpx

from .clients import MediaError
from .domain import buttons, chunks, text
from .media import archive_audio


def root_menu():
    return buttons("Bienvenue ! Choisis ton espace.",
                   [("portal:burkimbia", "BurkimbIA"), ("portal:wuumtaaba", "Wuumtaaba")])


def burkimbia_menu():
    return {
        "type": "interactive",
        "interactive": {
            "type": "list", "body": {"text": "*BurkimbIA* — modèles en test\nChoisis un service. "
                                                "Au clavier : /traduction /transcription /voix /yaaba.\n"
                                                "« services » pour revenir ici ; « menu » pour changer d’espace."},
            "action": {"button": "Les services", "sections": [{"title": "BurkimbIA", "rows": [
                {"id": "bi:translate", "title": "Traduction", "description": "Français ↔ Mooré"},
                {"id": "bi:transcribe", "title": "Transcription", "description": "Vocal en Mooré → texte"},
                {"id": "bi:synthesize", "title": "Synthèse vocale", "description": "Texte en Mooré → audio"},
                {"id": "bi:yaaba", "title": "Yaaba", "description": "Assistant — disponibilité en test"},
            ]}]},
        },
    }


class PortalConversation:
    def __init__(self, settings, whatsapp, burkimbia, store, legacy):
        self.settings, self.whatsapp, self.burkimbia = settings, whatsapp, burkimbia
        self.store, self.legacy = store, legacy

    async def _legacy(self, sender, message, state):
        legacy_state, replies, agreement = await self.legacy.handle(
            sender, message, state.get("legacy", {}))
        return {"portal": "wuumtaaba", "legacy": legacy_state}, replies, agreement

    async def handle(self, sender, message, state):
        state = deepcopy(state)
        # Existing deployed drafts and pending consents remain valid after upgrading.
        if state and "portal" not in state:
            state = {"portal": "wuumtaaba", "legacy": state}
        kind = message.get("type")
        body = message.get("text", {}).get("body", "").strip() if kind == "text" else ""
        command = body.casefold()
        interactive = message.get("interactive", {})
        action = interactive.get("button_reply", interactive.get("list_reply", {})).get("id", "")
        global_command = command in {"stop", "rappels oui"} or re.fullmatch(
            r"(accepter|refuser)\s+(?:wt-)?[a-f0-9]{8}", command)
        if global_command:
            legacy_state, replies, agreement = await self.legacy.handle(
                sender, message, state.get("legacy", {}))
            if command == "stop":
                return {}, replies, agreement
            if not state.get("portal"):
                state = {"portal": "wuumtaaba"}
            state["legacy"] = legacy_state
            return state, replies, agreement
        if command in {"menu", "annuler", "bonjour", "salut", "accueil"} or action == "portal:home":
            return {}, [root_menu()], None
        if action == "portal:wuumtaaba" or command in {"wuumtaaba", "wumtaaba"}:
            # Legacy menu applies existing customer-only authorization.
            return await self._legacy(sender, {"type": "text", "text": {"body": "menu"}}, {})
        if action == "portal:burkimbia" or command == "burkimbia":
            return {"portal": "burkimbia"}, [burkimbia_menu()], None
        if command == "services" and state.get("portal") == "burkimbia":
            return {"portal": "burkimbia"}, [burkimbia_menu()], None
        if not action and state.get("portal") == "burkimbia":
            aliases = {"/traduction": "translate", "/transcription": "transcribe",
                       "/voix": "synthesize", "/yaaba": "yaaba",
                       "/fr-mo": "fr-mo", "/mo-fr": "mo-fr"}
            if not state.get("feature"):
                aliases.update({"traduction": "translate", "transcription": "transcribe",
                                "synthèse vocale": "synthesize", "synthese vocale": "synthesize",
                                "yaaba": "yaaba"})
            if command in aliases:
                action = "bi:" + aliases[command]
        if action.startswith("bi:"):
            if state.get("portal") != "burkimbia":
                return state, [text("Ce choix a expiré. Écris « menu » pour reprendre.")], None
            choice = action[3:]
            if choice == "translate":
                return {"portal": "burkimbia", "feature": "translate"}, [buttons(
                    "Dans quel sens veux-tu traduire ?\nAu clavier : /fr-mo ou /mo-fr.", [("bi:fr-mo", "Français → Mooré"),
                                                           ("bi:mo-fr", "Mooré → Français")])], None
            if choice in {"fr-mo", "mo-fr"}:
                if state.get("feature") != "translate":
                    return state, [text("Choisis d’abord Traduction dans les services.")], None
                source, target = ("french", "moore") if choice == "fr-mo" else ("moore", "french")
                return {"portal": "burkimbia", "feature": "translate", "source": source,
                        "target": target}, [text("Envoie le texte à traduire. « menu » pour quitter.")], None
            prompts = {
                "transcribe": "Envoie un vocal en Mooré, de 60 secondes maximum et moins de 7 Mo. "
                              "Je demanderai ton accord avant de le traiter.",
                "synthesize": "Envoie le texte en Mooré à lire à voix haute (512 caractères maximum). "
                              "« menu » pour quitter.",
                "yaaba": "Écris ton message pour Yaaba. « menu » pour quitter.",
            }
            if choice in prompts:
                if choice == "yaaba" and not getattr(self.burkimbia, "yaaba_available", getattr(self.settings, "burkimbia_yaaba_enabled", False)):
                    return {"portal": "burkimbia"}, [text(
                        "Yaaba n’est pas encore disponible en mode Cloud. Le mode Mac permet de l’utiliser. "
                        "Les autres services restent accessibles."), burkimbia_menu()], None
                return {"portal": "burkimbia", "feature": choice}, [text(prompts[choice])], None
            return state, [text("Ce choix n’est plus disponible."), burkimbia_menu()], None
        if state.get("portal") == "wuumtaaba":
            return await self._legacy(sender, message, state)
        if state.get("portal") != "burkimbia":
            return {}, [root_menu()], None
        feature = state.get("feature")
        if command in {"/accepter-vocal", "/refuser-vocal"}:
            pending = state.get("pending_audio", {})
            if feature != "transcribe" or not pending.get("token"):
                return state, [text("Aucun vocal en attente de consentement. Renvoie ton vocal.")], None
            prefix = "bi-audio:" if command == "/accepter-vocal" else "bi-refuse:"
            action = prefix + pending["token"]
        if action.startswith(("bi-audio:", "bi-refuse:")):
            pending = state.get("pending_audio", {})
            if not pending or action.partition(":")[2] != pending.get("token"):
                return state, [text("Cette demande audio a expiré. Renvoie ton vocal.")], None
            state.pop("pending_audio", None)
            if action.startswith("bi-refuse:"):
                return state, [text("Vocal non traité. Tu peux envoyer un autre message.")], None
            try:
                audio = await self.whatsapp.audio(pending["id"])
                metadata = archive_audio(self.settings.media_directory, pending["message_id"],
                                         audio[0], audio[2])
                metadata.update({"media_id": pending["id"], "provider": "burkimbia"})
                self.store.record_media(pending["message_id"], metadata=metadata)
                result = await self.burkimbia.transcribe(*audio, language="moore")
                self.store.record_media(pending["message_id"],
                                        metadata={"status": "transcribed"}, transcript=result)
                return state, chunks("🎙️ *Transcription Mooré — BurkimbIA*\n" + result), None
            except (MediaError, ValueError, httpx.HTTPError, OSError) as error:
                self.store.record_media(pending["message_id"], metadata={
                    "status": "failed", "provider": "burkimbia", "error": type(error).__name__})
                return state, [self._error()], None
        media = message.get(kind, {}) if kind in {"audio", "document"} else {}
        document_audio = kind == "document" and media.get("mime_type", "").split(";")[0].strip() in {
            "audio/mpeg", "audio/mp4", "audio/ogg", "audio/wav", "audio/x-wav", "audio/webm"
        }
        if (kind == "audio" or document_audio) and feature == "transcribe":
            media_id = media.get("id", "")
            if not isinstance(media_id, str) or not media_id.isdigit():
                return state, [text("Ce vocal est invalide. Renvoie une note vocale WhatsApp.")], None
            token = uuid4().hex
            state["pending_audio"] = {"id": media_id, "token": token,
                                      "message_id": message.get("id") or token}
            return state, [buttons(
                "Autorises-tu le téléchargement de ce vocal depuis Meta, sa transmission au modèle "
                "de transcription Mooré BurkimbIA et la conservation privée de l’audio et du texte ? "
                "La transcription sera affichée ici.\nAu clavier : /accepter-vocal ou /refuser-vocal "
                "pour le dernier vocal reçu.",
                [(f"bi-audio:{token}", "Accepter"), (f"bi-refuse:{token}", "Refuser")])], None
        if action:
            return state, [text("Ce bouton n’est plus disponible. Écris « menu » pour reprendre.")], None
        if body and feature in {"translate", "synthesize", "yaaba"}:
            limit = 512
            if len(body) > limit:
                return state, [text(f"Pour ce test, envoie un texte de {limit} caractères maximum.")], None
            state.pop("pending_audio", None)
            try:
                if feature == "translate":
                    if not state.get("source"):
                        return state, [text("Choisis le sens de traduction avec les boutons précédents.")], None
                    result = await self.burkimbia.translate(body, source=state["source"], target=state["target"])
                    return state, chunks("*Traduction BurkimbIA*\n" + result), None
                if feature == "synthesize":
                    audio = await self.burkimbia.synthesize(body, language="moore")
                    metadata = archive_audio(self.settings.media_directory,
                                             message.get("id") or uuid4().hex, audio[0], audio[2])
                    metadata.update({"provider": "burkimbia", "source_text": body})
                    media_id = await self.whatsapp.upload_audio(*audio)
                    return state, [{"type": "audio", "audio": {"id": media_id},
                                    "_local_audio": metadata}], None
                if not getattr(self.burkimbia, "yaaba_available", getattr(self.settings, "burkimbia_yaaba_enabled", False)):
                    return state, [text("Yaaba n’est pas encore disponible via une API vérifiée.")], None
                history = state.get("history", [])[-10:]
                result = await self.burkimbia.chat(body, history)
                state["history"] = (history + [{"role": "user", "content": body},
                                              {"role": "assistant", "content": result}])[-10:]
                return state, chunks(result), None
            except MediaError as exc:
                return state, [text(str(exc))], None
            except (ValueError, httpx.HTTPError, OSError):
                return state, [self._error()], None
        if feature == "transcribe":
            return state, [text("Envoie un vocal en Mooré, de 60 secondes maximum et moins de 7 Mo. "
                                 "« menu » pour quitter.")], None
        return state, [burkimbia_menu()], None

    @staticmethod
    def _error():
        return text("Le modèle BurkimbIA n’a pas pu traiter cette demande. "
                    "Réessaie dans quelques instants ou écris « menu » pour changer de service.")
