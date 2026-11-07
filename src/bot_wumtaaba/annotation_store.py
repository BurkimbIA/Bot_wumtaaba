"""Annotation writes participate in the same transaction as inbox completion and replies."""

import json
import re
import time


def apply_annotation_operation(db, sender, source_message_id, operation):
    task = operation.get("task_id")
    ratings = operation.get("ratings", {})
    correction = operation.get("correction")
    sample = operation.get("sample")
    if (
        operation.get("_operation") != "annotation"
        or not isinstance(task, str)
        or not re.fullmatch(r"[0-9]{3}", task)
        or operation.get("preference") not in ("A", "B", "tie", "neither")
        or operation.get("status") not in ("in_progress", "completed")
        or not isinstance(ratings, dict)
        or set(ratings) - {"A", "B"}
        or any(v is not None and (type(v) is not int or not 1 <= v <= 5) for v in ratings.values())
        or (correction is not None and (not isinstance(correction, str) or len(correction) > 4000))
        or not isinstance(sample, dict)
        or not isinstance(sample.get("sha256"), str)
        or not re.fullmatch(r"[a-f0-9]{64}", sample["sha256"])
        or not sample.get("dataset")
        or not sample.get("id")
    ):
        raise ValueError("Opération d’annotation invalide")
    assigned = db.execute(
        "SELECT t.sample FROM annotation_assignments a JOIN annotation_tasks t ON t.task_id=a.task_id "
        "WHERE a.recipient=%s AND a.task_id=%s AND a.status!='cancelled' AND t.enabled=1",
        (sender, task),
    ).fetchone()
    if assigned is None or json.loads(assigned[0]) != sample:
        raise ValueError("Tâche d’annotation absente ou modifiée")
    now = time.time()
    inserted = db.execute(
        "INSERT INTO annotation_events(source_message_id,recipient,task_id,payload,created_at) "
        "VALUES(%s,%s,%s,%s,%s) ON CONFLICT(source_message_id) DO NOTHING RETURNING source_message_id",
        (source_message_id, sender, task, json.dumps(operation, ensure_ascii=False), now),
    ).fetchone()
    if inserted is None:
        return
    db.execute(
        "INSERT INTO annotation_responses(recipient,task_id,preference,ratings,correction,sample,status,"
        "source_message_id,created_at,updated_at) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
        "ON CONFLICT(recipient,task_id) DO UPDATE SET preference=excluded.preference,ratings=excluded.ratings,"
        "correction=excluded.correction,sample=excluded.sample,status=excluded.status,"
        "source_message_id=excluded.source_message_id,updated_at=excluded.updated_at",
        (
            sender,
            task,
            operation["preference"],
            json.dumps(ratings),
            correction,
            json.dumps(sample, ensure_ascii=False),
            operation["status"],
            source_message_id,
            now,
            now,
        ),
    )
    if operation["status"] == "completed":
        db.execute(
            "UPDATE annotation_assignments SET status='completed' WHERE recipient=%s AND task_id=%s",
            (sender, task),
        )
