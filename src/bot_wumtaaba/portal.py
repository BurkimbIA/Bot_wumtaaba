"""Direct Wuumtaaba entry point, compatible with previously saved portal sessions."""

from copy import deepcopy

from .annotations import handle_annotation


class PortalConversation:
    def __init__(self, settings, whatsapp, burkimbia, store, legacy, media=None):
        self.store = store
        self.legacy = legacy

    async def handle(self, sender, message, state):
        annotation = await handle_annotation(self.store, sender, message, state)
        if annotation is not None:
            return annotation
        # Keep existing agreement drafts; discard sessions from the retired services.
        if state.get("portal") == "wuumtaaba":
            legacy_state = deepcopy(state.get("legacy", {}))
        elif "portal" in state:
            legacy_state = {}
        else:
            legacy_state = deepcopy(state)

        body = message.get("text", {}).get("body", "").strip() if message.get("type") == "text" else ""
        command = body.casefold()
        interactive = message.get("interactive", {})
        action = interactive.get("button_reply", interactive.get("list_reply", {})).get("id", "")
        # Old menu buttons must never revive a service or reuse its audio consent.
        if action.startswith(("portal:", "bi:", "bi-audio:", "bi-refuse:")) or command in {
            "accueil",
            "wuumtaaba",
            "wumtaaba",
        }:
            message = {"type": "text", "text": {"body": "menu"}}

        result, replies, operation = await self.legacy.handle(sender, message, legacy_state)
        if command == "stop":
            return {}, replies, operation
        return {"portal": "wuumtaaba", "legacy": result}, replies, operation
