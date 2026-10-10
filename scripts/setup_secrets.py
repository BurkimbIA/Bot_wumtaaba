"""Saisie locale masquée des clés. N'envoyer aucun secret dans le chat."""

import os
from getpass import getpass
from pathlib import Path

from dotenv import set_key

path = Path(".env")
if not path.exists():
    path.write_text(Path(".env.example").read_text())
os.chmod(path, 0o600)
for key in ("WHATSAPP_TOKEN", "META_APP_SECRET", "OPENAI_API_KEY"):
    value = getpass(f"{key} (Entrée pour conserver) : ").strip()
    if value:
        set_key(str(path), key, value)
print("Clés enregistrées localement. Lance python scripts/check_config.py.")
