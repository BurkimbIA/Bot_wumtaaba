"""Prépare le volume SQLite puis abandonne les droits root avant le serveur."""

import os
import pwd
import sys
from pathlib import Path

if os.getuid() == 0:
    account = pwd.getpwnam("bot")
    data = Path("/app/data")
    data.mkdir(parents=True, exist_ok=True)
    os.chown(data, account.pw_uid, account.pw_gid)
    os.setgroups([])
    os.setgid(account.pw_gid)
    os.setuid(account.pw_uid)

os.execvp(sys.argv[1], sys.argv[1:])
