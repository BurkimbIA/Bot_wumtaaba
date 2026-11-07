"""Register immutable task sources only after private Storage round-trip verification."""

import hashlib
import json
import re
import time


async def register_task(store, media, task_id, sample, audio, mime="audio/flac"):
    if (
        not re.fullmatch(r"[0-9]{3}", task_id)
        or not isinstance(audio, bytes)
        or not 0 < len(audio) <= 7 * 1024 * 1024
    ):
        raise ValueError("Tâche ou audio invalide")
    digest = hashlib.sha256(audio).hexdigest()
    if sample.get("sha256") != digest:
        raise ValueError("Empreinte audio différente de la source")
    if not all(isinstance(sample.get(k), str) and sample[k] for k in ("dataset", "revision", "id")):
        raise ValueError("Provenance de la tâche incomplète")
    proposals = sample.get("proposals", {})
    if not all(
        isinstance(proposals.get(k), dict) and proposals[k].get("text") and proposals[k].get("model_id")
        for k in ("A", "B")
    ):
        raise ValueError("Les deux propositions et leurs modèles sont requis")
    path = f"annotations/{digest}.audio"
    await media.put(path, audio, mime)
    restored = await media.get(path)
    if restored is None or hashlib.sha256(restored).hexdigest() != digest:
        raise ValueError("Audio non confirmé dans le stockage privé")
    with store.lock, store.db:
        store.db.execute("SELECT pg_advisory_xact_lock(hashtext(%s))", ("annotation-task:" + task_id,))
        old = store.db.execute(
            "SELECT sample,audio_path FROM annotation_tasks WHERE task_id=%s", (task_id,)
        ).fetchone()
        if old:
            if json.loads(old["sample"]) != sample or old["audio_path"] != path:
                raise ValueError("Identifiant de tâche déjà utilisé pour une autre source")
        else:
            store.db.execute(
                "INSERT INTO annotation_tasks(task_id,sample,audio_path,audio_sha256,audio_mime,created_at) "
                "VALUES(%s,%s,%s,%s,%s,%s)",
                (task_id, json.dumps(sample, ensure_ascii=False), path, digest, mime, time.time()),
            )
    return path


def assign_sent_previews(store, task_id):
    """Backfill only genuine sent cards with the same blind A/B contents; never send a message."""
    with store.lock, store.db:
        row = store.db.execute("SELECT sample FROM annotation_tasks WHERE task_id=%s", (task_id,)).fetchone()
        if row is None:
            raise ValueError("Tâche absente du stockage durable")
        sample = json.loads(row[0])
        expected = {f"annpreview:{task_id}:{choice}" for choice in ("A", "B", "tie")}
        rows = store.db.execute(
            "SELECT id,recipient,content,created_at FROM outbox WHERE status='sent' "
            "AND remote_id IS NOT NULL AND content LIKE %s ORDER BY id DESC",
            (f"%annpreview:{task_id}:%",),
        ).fetchall()
        count = 0
        for row in rows:
            content = json.loads(row["content"])
            interactive = content.get("interactive", {})
            ids = {b.get("reply", {}).get("id") for b in interactive.get("action", {}).get("buttons", [])}
            body = interactive.get("body", {}).get("text", "")
            if (
                interactive.get("type") != "button"
                or not expected <= ids
                or not all(f"{k} : {sample['proposals'][k]['text']}" in body for k in ("A", "B"))
            ):
                continue
            inserted = store.db.execute(
                "INSERT INTO annotation_assignments(recipient,task_id,source_outbox_id,assigned_at) "
                "VALUES(%s,%s,%s,%s) ON CONFLICT(recipient,task_id) DO NOTHING RETURNING recipient",
                (row["recipient"], task_id, row["id"], row["created_at"]),
            ).fetchone()
            count += inserted is not None
        return count
