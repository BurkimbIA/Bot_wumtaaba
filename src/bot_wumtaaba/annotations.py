"""Annotation callbacks for assigned WhatsApp examples; persistence belongs to finish_inbox."""

import json
import re
import time
from copy import deepcopy

from .domain import buttons, text

ASSIGNMENT_TTL = 7 * 24 * 3600
PREFERENCE = re.compile(r"annpreview:(\d{3}):(A|B|tie)")
RATING = re.compile(r"ann:(\d{3}):rate:([AB]):([1-5])")
SKIP = re.compile(r"ann:(\d{3}):skip")
STAGES = {"rate_A", "rate_B", "correction"}
EXIT_COMMANDS = {"stop", "menu", "annuler", "accueil", "wuumtaaba", "wumtaaba"}
SCALE = (
    ("Inutilisable", "La transcription ne correspond pas aux paroles."),
    ("Nombreuses erreurs", "Une grande partie doit être corrigée."),
    ("À corriger", "Compréhensible, avec plusieurs erreurs."),
    ("Presque exacte", "Quelques corrections mineures."),
    ("Fidèle", "La transcription respecte les paroles de l’audio."),
)


def _decode(value):
    try:
        return json.loads(value) if isinstance(value, str) else value
    except (TypeError, ValueError):
        return None


def _assigned_sample(store, sender, task):
    """An ID alone is never an assignment: prove the sent card and its exact A/B mapping."""
    now = time.time()
    rows = store.db.execute(
        "SELECT t.sample,a.assigned_at,o.content FROM annotation_assignments a "
        "JOIN annotation_tasks t ON t.task_id=a.task_id "
        "JOIN outbox o ON o.id=a.source_outbox_id AND o.recipient=a.recipient "
        "JOIN contacts c ON c.id=a.recipient "
        "WHERE a.recipient=%s AND a.task_id=%s AND a.status IN ('assigned','completed') "
        "AND t.enabled=1 AND c.reminders_enabled=1 "
        "AND o.status='sent' AND o.remote_id IS NOT NULL AND a.assigned_at>=%s",
        (sender, task, now - ASSIGNMENT_TTL),
    ).fetchall()
    expected = {f"annpreview:{task}:{choice}" for choice in ("A", "B", "tie")}
    for row in rows:
        if not 0 <= now - row["assigned_at"] < ASSIGNMENT_TTL:
            continue
        content = _decode(row["content"])
        if not isinstance(content, dict):
            continue
        interactive = content.get("interactive", {})
        if not isinstance(interactive, dict) or interactive.get("type") != "button":
            continue
        ids = {
            item.get("reply", {}).get("id")
            for item in interactive.get("action", {}).get("buttons", [])
            if isinstance(item, dict)
        }
        body = interactive.get("body", {}).get("text")
        if not expected <= ids or not isinstance(body, str):
            continue
        sample = _decode(row["sample"])
        if not isinstance(sample, dict):
            continue
        proposals = sample.get("proposals", {})
        if not all(
            isinstance(proposals.get(label), dict)
            and isinstance(proposals[label].get("text"), str)
            and proposals[label]["text"].strip()
            and isinstance(proposals[label].get("model_id"), str)
            and proposals[label]["model_id"]
            for label in ("A", "B")
        ):
            continue
        if not all(
            isinstance(sample.get(key), str) and sample[key]
            for key in ("id", "dataset", "revision", "sha256")
        ):
            continue
        if all(f"{label} : {proposals[label]['text']}" in body for label in ("A", "B")):
            return deepcopy(sample)
    return None


def _rating_prompt(task, label, prefix=""):
    return {
        "type": "interactive",
        "interactive": {
            "type": "list",
            "body": {
                "text": prefix + f"Note la proposition {label} de 1 à 5 pour ANN-MOORE-{task}. "
                "Écoute l’audio du même exemple. Tu peux aussi écrire un chiffre ou PASSER."
            },
            "action": {
                "button": "Choisir la note",
                "sections": [
                    {
                        "title": f"Note de {label}",
                        "rows": [
                            {
                                "id": f"ann:{task}:rate:{label}:{number}",
                                "title": f"{number} · {title}",
                                "description": description,
                            }
                            for number, (title, description) in enumerate(SCALE, 1)
                        ],
                    }
                ],
            },
        },
    }


def _prompt(active, prefix=""):
    task = active["task_id"]
    if active["stage"] in {"rate_A", "rate_B"}:
        return _rating_prompt(task, active["stage"][-1], prefix)
    return buttons(
        prefix + f"Pour ANN-MOORE-{task}, tu peux maintenant écrire ta propre transcription mooré "
        "ou corriger les propositions. Écris [inaudible] plutôt que d’inventer. "
        "Tu peux passer cette étape. MENU quitte l’annotation sans modifier ton carnet.",
        [(f"ann:{task}:skip", "Passer")],
    )


def _operation(active):
    answers = active["answers"]
    return {
        "_operation": "annotation",
        "task_id": active["task_id"],
        "sample": deepcopy(active["sample"]),
        "preference": answers["preference"],
        "ratings": deepcopy(answers["ratings"]),
        "correction": answers["correction"],
        "status": "completed" if active["stage"] == "done" else "in_progress",
    }


def _reply(state, body):
    return deepcopy(state), [text(body)], None


async def handle_annotation(store, sender, message, state):
    """Return None outside annotation routing. This function only reads the store."""
    body = message.get("text", {}).get("body", "").strip() if message.get("type") == "text" else ""
    command = body.casefold()
    if command in EXIT_COMMANDS:
        return None
    interactive = message.get("interactive", {})
    action = interactive.get("button_reply", interactive.get("list_reply", {})).get("id", "")
    active = state.get("annotation_active")
    active = active if isinstance(active, dict) else None
    if not action.startswith(("annpreview:", "ann:")):
        if action or message.get("type") != "text" or not (active and active.get("stage") in STAGES):
            return None
    choice, rating, skip = PREFERENCE.fullmatch(action), RATING.fullmatch(action), SKIP.fullmatch(action)
    task = (
        choice[1]
        if choice
        else rating[1]
        if rating
        else skip[1]
        if skip
        else (active.get("task_id") if not action and active else None)
    )
    if not isinstance(task, str) or not re.fullmatch(r"\d{3}", task):
        return _reply(
            state, "Ce choix d’annotation n’est pas reconnu. Utilise les boutons de l’exemple reçu."
        )
    sample = _assigned_sample(store, sender, task)
    if sample is None:
        return _reply(
            state,
            "Cet exemple d’annotation ne t’est pas attribué ou a expiré. Aucun choix n’a été enregistré.",
        )
    saved = store.db.execute(
        "SELECT status FROM annotation_responses WHERE recipient=%s AND task_id=%s", (sender, task)
    ).fetchone()
    if saved and saved["status"] == "completed":
        return _reply(state, f"Ton annotation ANN-MOORE-{task} est déjà enregistrée. Merci !")
    result = deepcopy(state)
    if choice:
        if active and active.get("task_id") == task and active.get("stage") in STAGES:
            return result, [_prompt(active, "Ton choix a déjà été reçu. ")], None
        active = {
            "task_id": task,
            "stage": "rate_A",
            "sample": sample,
            "answers": {"preference": choice[2], "ratings": {"A": None, "B": None}, "correction": None},
        }
        result["annotation_active"] = active
        return result, [_prompt(active, "Merci, ta préférence est enregistrée. ")], _operation(active)
    if (
        not active
        or active.get("task_id") != task
        or active.get("stage") not in STAGES
        or active.get("sample") != sample
    ):
        return _reply(
            state,
            "Ce choix ne correspond pas à l’étape en cours. "
            "Commence par choisir A, B ou Égalité sur l’exemple reçu.",
        )
    active = result["annotation_active"]
    stage = active["stage"]
    if stage in {"rate_A", "rate_B"}:
        label = stage[-1]
        if rating and rating[2] == label:
            value = int(rating[3])
        elif skip or not action and command == "passer":
            value = None
        elif not action and re.fullmatch(r"[1-5]", body):
            value = int(body)
        else:
            return _reply(state, f"Pour la proposition {label}, choisis une note de 1 à 5 ou écris PASSER.")
        active["answers"]["ratings"][label] = value
        active["stage"] = "rate_B" if label == "A" else "correction"
        return result, [_prompt(active, "Merci, cette étape est enregistrée. ")], _operation(active)
    if skip or not action and command == "passer":
        correction = None
    elif not action and message.get("type") == "text" and 0 < len(body) <= 4000:
        correction = body
    else:
        return _reply(state, "Écris ta transcription en texte (4 000 caractères maximum), ou PASSER.")
    active["answers"]["correction"] = correction
    active["stage"] = "done"
    return (
        result,
        [
            text(
                f"Merci ! Ton annotation ANN-MOORE-{task} est enregistrée : "
                "préférence, notes et transcription éventuelle. "
                "Tu peux choisir l’autre exemple ou écrire MENU pour revenir à ton carnet."
            )
        ],
        _operation(active),
    )
