"""Prepare a persistent local database from the versioned production baseline."""
import os
from pathlib import Path
import secrets
import subprocess
import sys

ROOT = Path(__file__).resolve().parents[1]


def main():
    env = ROOT / '.env.dev'
    if not env.exists():
        password = secrets.token_hex(20)
        content = (
            f'DATABASE_URL=postgresql://wumtaaba_dev:{password}@127.0.0.1:55432/wumtaaba_dev\n'
            'DATABASE_SCHEMA=wuumtaaba\n'
            f'DEV_DATABASE_PASSWORD={password}\n'
            f'ADMIN_PASSWORD={secrets.token_urlsafe(24)}\n'
            'ADMIN_COOKIE_SECURE=false\n'
        )
        fd = os.open(env, os.O_WRONLY | os.O_CREAT | os.O_EXCL, 0o600)
        with os.fdopen(fd, 'w') as stream:
            stream.write(content)
    subprocess.run(['docker', 'compose', '--env-file', str(env), '-f', str(ROOT / 'compose.dev.yml'),
                    'up', '-d', '--wait'], cwd=ROOT, check=True)
    subprocess.run([sys.executable, str(ROOT / 'scripts/dev_database.py'), '--env-file', str(env), 'apply'],
                   cwd=ROOT, check=True)
    print('Base dev prête. Code administrateur local dans .env.dev ; aucune donnée client importée.')


if __name__ == '__main__':
    main()
