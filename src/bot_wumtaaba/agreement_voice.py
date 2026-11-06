"""Bounded Mooré/French processing for the agreement dialogue."""

import re

from .clients import MediaError


def pieces(value: str, limit=480) -> list[str]:
    if not isinstance(value, str) or not value.strip():
        raise MediaError("Le texte à traiter est vide. Renvoie ton message.")
    if len(value) > 4096:
        raise MediaError(
            "Ce message est trop long. Envoie un vocal plus court ou plusieurs messages."
        )
    result, current = [], ""
    for word in value.split():
        if len(word) > limit:
            raise MediaError(
                "Un élément du texte est trop long pour le modèle. Corrige ce passage."
            )
        if current and len(current) + len(word) + 1 > limit:
            result.append(current)
            current = ""
        current = (current + " " + word).strip()
    if current:
        result.append(current)
    return result


async def translate_all(api, value, source, target):
    if api is None:
        raise MediaError("Les modèles BurkimbIA ne sont pas configurés. Réessaie plus tard.")
    return "\n".join(
        [await api.translate(part, source=source, target=target) for part in pieces(value)]
    )


def spoken_summary(draft):
    if draft.question():
        raise MediaError("Complète d’abord les informations de l’accord.")
    return (
        f"Résumé de l’accord à vérifier. Client : {draft.client}. Produit : {draft.product}. "
        f"Prix total : {draft.total} francs CFA. Avance déjà payée : {draft.paid} francs CFA. "
        f"Reste à payer : {draft.total - draft.paid} francs CFA. "
        f"Date prévue : {draft.due_date.strftime('%d/%m/%Y')}. "
        "Note personnelle, non confirmée par le client. Vérifie avant d’enregistrer."
    )


def verify_spoken_amounts(translated, draft):
    # Refuse a translated readback if the model dropped/changed numerical amounts.
    numbers = {
        re.sub(r"\s", "", item) for item in re.findall(r"\d+(?:[ \u00a0\u202f]\d{3})*", translated)
    }
    if not {str(draft.total), str(draft.paid), str(draft.total - draft.paid)} <= numbers:
        raise MediaError(
            "La lecture en mooré n’a pas conservé tous les montants. "
            "Vérifie le résumé écrit ; aucun accord enregistré."
        )


async def moore_spoken_summary(api, draft):
    """Translate prose separately; monetary values and the date never enter the translator."""
    if api is None:
        raise MediaError("La lecture en mooré n’est pas configurée.")
    if draft.question():
        raise MediaError("Complète d’abord l’accord.")
    prompts = [
        "Résumé de l’accord à vérifier.",
        "Client",
        "Produit",
        "Prix total en francs CFA",
        "Avance déjà payée en francs CFA",
        "Reste à payer en francs CFA",
        "Date prévue, jour mois année",
        "Note personnelle non confirmée par le client. Vérifie avant d’enregistrer.",
    ]
    # Cache only generic labels on the existing provider client, never user data.
    key = (
        getattr(getattr(api, "bridge", None), "provider", "cloud"),
        getattr(getattr(api, "s", None), "burkimbia_translation_model", "default"),
    )
    cache = getattr(api, "_agreement_label_cache", None)
    if not isinstance(cache, dict):
        cache = {}
    labels = cache.get(key)
    if labels is None:
        labels = [await api.translate(p, source="french", target="moore") for p in prompts]
        if any(re.search(r"\d", label) for label in labels):
            raise MediaError(
                "La traduction des intitulés du résumé est invalide. "
                "Le résumé écrit reste disponible."
            )
        cache[key] = labels
        api._agreement_label_cache = cache
    product = await translate_all(api, draft.product, "french", "moore")
    spoken = "\n".join(
        [
            labels[0],
            f"{labels[1]} : {draft.client}.",
            f"{labels[2]} : {product}.",
            f"{labels[3]} : {draft.total}.",
            f"{labels[4]} : {draft.paid}.",
            f"{labels[5]} : {draft.total - draft.paid}.",
            f"{labels[6]} : {draft.due_date.strftime('%d/%m/%Y')}.",
            labels[7],
        ]
    )
    verify_spoken_amounts(spoken, draft)
    return spoken
