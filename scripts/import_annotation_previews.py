"""Import previously sent annotation examples into Supabase; never send WhatsApp messages.

Default: read-only plan. --apply creates the schema, archives HF audio privately and
registers verified sent assignments. No task source or response is written to local disk.
"""

import argparse
import asyncio
import json
from urllib.parse import quote

import httpx
import psycopg
from psycopg import sql

from bot_wumtaaba.annotation_tasks import assign_sent_previews, register_task
from bot_wumtaaba.config import Settings
from bot_wumtaaba.media import MediaStore
from bot_wumtaaba.store import Store


async def main(apply=False):
    settings = Settings()
    db = psycopg.connect(settings.database_url.get_secret_value(), autocommit=True)
    try:
        db.execute("SET default_transaction_read_only=on")
        db.execute(sql.SQL("SET search_path TO {}").format(sql.Identifier(settings.database_schema)))
        # Source discovery stays read-only, including when the new migration is not installed yet.
        rows = db.execute(
            "SELECT details FROM audit_events WHERE kind='annotation_eval_preview_queued' ORDER BY id"
        ).fetchall()
        samples = {}
        for row in rows:
            sample = json.loads(row[0]).get("sample", {})
            index = sample.get("row_idx")
            if (
                sample.get("dataset") != "burkimbia/asr-benchmark-public"
                or type(index) is not int
                or index not in {2, 30}
            ):
                continue
            task_id = f"{index:03d}"
            if task_id in samples and samples[task_id] != sample:
                raise ValueError("Plusieurs versions incompatibles du même exemple")
            samples[task_id] = sample
        print(
            json.dumps(
                {
                    "mode": "apply" if apply else "read_only_plan",
                    "task_ids": sorted(samples),
                    "local_data_writes": False,
                    "whatsapp_sends": False,
                },
                ensure_ascii=False,
            )
        )
        if not apply:
            return
        if not samples:
            raise ValueError("Aucun exemple déjà envoyé à importer")
        store = Store(settings.database_url.get_secret_value(), settings.database_schema)
        try:
            async with httpx.AsyncClient(timeout=60, follow_redirects=False) as http:
                media = MediaStore(settings, http)
                bucket = await http.get(
                    settings.supabase_url.rstrip("/")
                    + "/storage/v1/bucket/"
                    + quote(settings.media_bucket, safe=""),
                    headers=media.headers,
                )
                bucket.raise_for_status()
                if bucket.json().get("public") is not False:
                    raise ValueError("Le bucket des annotations doit être privé")
                for task_id, sample in samples.items():
                    response = await http.get(
                        "https://datasets-server.huggingface.co/rows",
                        params={
                            "dataset": sample["dataset"],
                            "config": sample["config"],
                            "split": sample["split"],
                            "offset": sample["row_idx"],
                            "length": 1,
                        },
                    )
                    response.raise_for_status()
                    row = response.json()["rows"][0]["row"]
                    url = row["audio"][0]["src"]
                    if (
                        row["id"] != sample["id"]
                        or sample["revision"] not in url
                        or httpx.URL(url).host != "datasets-server.huggingface.co"
                    ):
                        raise ValueError("Source Hugging Face différente de l’exemple envoyé")
                    audio = bytearray()
                    async with http.stream("GET", url) as result:
                        result.raise_for_status()
                        async for chunk in result.aiter_bytes():
                            audio.extend(chunk)
                            if len(audio) > 7 * 1024 * 1024:
                                raise ValueError("Audio trop volumineux")
                    await register_task(store, media, task_id, sample, bytes(audio))
                    count = assign_sent_previews(store, task_id)
                    print(
                        json.dumps(
                            {"task_id": task_id, "private_audio_verified": True, "new_assignments": count},
                            ensure_ascii=False,
                        )
                    )
        finally:
            store.close()
    finally:
        db.close()


if __name__ == "__main__":
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("--apply", action="store_true")
    args = parser.parse_args()
    asyncio.run(main(args.apply))
