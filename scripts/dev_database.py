"""Plan and apply reviewed, checksummed database migrations; no reset operation."""

import argparse
import hashlib
import json
import os
from pathlib import Path
from urllib.parse import unquote, urlsplit

import psycopg
from dotenv import dotenv_values

ROOT = Path(__file__).resolve().parents[1]
MIGRATIONS = ROOT / "migrations"
SCHEMA = "wuumtaaba"


def schema_snapshot(connection, schema=SCHEMA):
    """Stable catalog description; excludes owners, OIDs and runtime sequence values."""
    queries = {
        "relations": "SELECT c.relname,c.relkind,c.relrowsecurity,c.relforcerowsecurity,CASE WHEN c.relkind IN ('v','m') THEN pg_get_viewdef(c.oid,true) ELSE NULL END FROM pg_class c JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND c.relkind IN ('r','p','v','m') ORDER BY c.relname",
        "columns": "SELECT c.relname,a.attname,a.attnum,format_type(a.atttypid,a.atttypmod),a.attnotnull,a.attidentity,a.attgenerated,pg_get_expr(d.adbin,d.adrelid) FROM pg_attribute a JOIN pg_class c ON c.oid=a.attrelid JOIN pg_namespace n ON n.oid=c.relnamespace LEFT JOIN pg_attrdef d ON d.adrelid=a.attrelid AND d.adnum=a.attnum WHERE n.nspname=%s AND a.attnum>0 AND NOT a.attisdropped AND c.relkind IN ('r','p','v','m') ORDER BY c.relname,a.attnum",
        "constraints": "SELECT c.relname,k.conname,k.contype,k.convalidated,pg_get_constraintdef(k.oid,true) FROM pg_constraint k JOIN pg_class c ON c.oid=k.conrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,k.conname",
        "indexes": "SELECT c.relname,i.relname,pg_get_indexdef(i.oid) FROM pg_index x JOIN pg_class c ON c.oid=x.indrelid JOIN pg_class i ON i.oid=x.indexrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,i.relname",
        "functions": "SELECT p.proname,pg_get_function_identity_arguments(p.oid),pg_get_functiondef(p.oid) FROM pg_proc p JOIN pg_namespace n ON n.oid=p.pronamespace WHERE n.nspname=%s AND p.prokind IN ('f','p') ORDER BY p.proname,pg_get_function_identity_arguments(p.oid)",
        "policies": "SELECT c.relname,p.polname,p.polcmd,p.polpermissive,ARRAY(SELECT CASE WHEN role=0 THEN 'public' ELSE pg_get_userbyid(role)::text END FROM unnest(p.polroles) role ORDER BY 1),pg_get_expr(p.polqual,p.polrelid),pg_get_expr(p.polwithcheck,p.polrelid) FROM pg_policy p JOIN pg_class c ON c.oid=p.polrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname,p.polname",
        "sequences": "SELECT c.relname,format_type(s.seqtypid,NULL),s.seqstart,s.seqincrement,s.seqmax,s.seqmin,s.seqcache,s.seqcycle FROM pg_sequence s JOIN pg_class c ON c.oid=s.seqrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s ORDER BY c.relname",
        "triggers": "SELECT c.relname,t.tgname,pg_get_triggerdef(t.oid,true) FROM pg_trigger t JOIN pg_class c ON c.oid=t.tgrelid JOIN pg_namespace n ON n.oid=c.relnamespace WHERE n.nspname=%s AND NOT t.tgisinternal ORDER BY c.relname,t.tgname",
        "enums": "SELECT t.typname,e.enumlabel,e.enumsortorder::text FROM pg_enum e JOIN pg_type t ON t.oid=e.enumtypid JOIN pg_namespace n ON n.oid=t.typnamespace WHERE n.nspname=%s ORDER BY t.typname,e.enumsortorder",
    }
    with connection.transaction():
        connection.execute("SET LOCAL search_path TO pg_catalog")
        return {
            name: [list(row) for row in connection.execute(query, (schema,)).fetchall()]
            for name, query in queries.items()
        }


def schema_fingerprint(connection, schema=SCHEMA):
    encoded = json.dumps(schema_snapshot(connection, schema), ensure_ascii=False, separators=(",", ":"))
    return hashlib.sha256(encoded.encode()).hexdigest()


def validate_target(url, production=False):
    try:
        parsed = urlsplit(url)
        hostname = parsed.hostname
        # Accessing port also validates malformed or out-of-range values.
        _ = parsed.port
    except ValueError:
        raise ValueError("URL PostgreSQL invalide ; détails privés masqués.") from None
    if parsed.scheme not in {"postgresql", "postgres"}:
        raise ValueError("Une URL PostgreSQL est requise.")
    if not production and (
        hostname not in {"127.0.0.1", "localhost", "::1"}
        or unquote(parsed.path) != "/wumtaaba_dev"
        or parsed.query
        or parsed.fragment
    ):
        raise ValueError("Cible distante refusée sans --production ; dev exige une base locale wumtaaba_dev.")
    return url


def migration_files(directory=MIGRATIONS):
    files = sorted(directory.glob("[0-9]*.sql"))
    if not files or any(not path.name[:4].isdigit() for path in files):
        raise ValueError("Aucune migration numérotée valide.")
    return files


def checksum(path):
    return hashlib.sha256(path.read_bytes()).hexdigest()


def applied_versions(connection):
    exists = connection.execute("SELECT to_regclass('wumtaaba_migrations.schema_versions')").fetchone()[0]
    if not exists:
        return {}
    return dict(connection.execute("SELECT filename,checksum FROM wumtaaba_migrations.schema_versions"))


def plan(connection, files):
    applied = applied_versions(connection)
    known = {path.name for path in files}
    if set(applied) - known:
        raise ValueError("La base contient des migrations absentes du dépôt.")
    result = []
    for path in files:
        digest = checksum(path)
        if path.name in applied and applied[path.name] != digest:
            raise ValueError("Migration déjà appliquée modifiée : " + path.name)
        result.append({"filename": path.name, "checksum": digest, "applied": path.name in applied})
    return result


def apply(connection, files, *, adopt_baseline=False, manifest=None):
    # One transaction and an advisory lock prevent partial application/concurrent runners.
    with connection.transaction():
        connection.execute("SELECT pg_advisory_xact_lock(731109, 1)")
        rows = plan(connection, files)
        baseline = files[0]
        pending = [path for path, row in zip(files, rows) if not row["applied"]]
        exists = connection.execute(
            "SELECT EXISTS(SELECT 1 FROM pg_namespace WHERE nspname=%s)", (SCHEMA,)
        ).fetchone()[0]
        adopt = baseline in pending and exists
        if adopt:
            if not adopt_baseline:
                raise ValueError("Schéma existant : vérifier et utiliser explicitement --adopt-baseline.")
            if not manifest or manifest.get("baseline") != baseline.name or manifest.get("schema") != SCHEMA:
                raise ValueError("Manifest de baseline absent ou invalide.")
            if schema_fingerprint(connection) != manifest.get("fingerprint"):
                raise ValueError("Le schéma diffère de la baseline : adoption refusée.")
        connection.execute("CREATE SCHEMA IF NOT EXISTS wumtaaba_migrations")
        connection.execute(
            "CREATE TABLE IF NOT EXISTS wumtaaba_migrations.schema_versions (filename TEXT PRIMARY KEY,checksum TEXT NOT NULL,applied_at TIMESTAMPTZ NOT NULL DEFAULT now())"
        )
        connection.execute("ALTER TABLE wumtaaba_migrations.schema_versions ENABLE ROW LEVEL SECURITY")
        for path in pending:
            if path != baseline or not adopt:
                connection.execute(path.read_text())
            connection.execute(
                "INSERT INTO wumtaaba_migrations.schema_versions(filename,checksum) VALUES(%s,%s)",
                (path.name, checksum(path)),
            )
    return [path.name for path in pending]


def main(argv=None):
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("command", choices=["plan", "apply", "status", "fingerprint"])
    source = parser.add_mutually_exclusive_group()
    source.add_argument("--env-file", type=Path)
    source.add_argument("--from-environment", action="store_true")
    parser.add_argument("--production", action="store_true")
    parser.add_argument("--adopt-baseline", action="store_true")
    args = parser.parse_args(argv)
    try:
        values = os.environ if args.from_environment else dotenv_values(args.env_file or ROOT / ".env.dev")
        if values.get("DATABASE_SCHEMA", SCHEMA) != SCHEMA:
            raise ValueError("Le schéma de migrations doit être wuumtaaba.")
        url = validate_target(values.get("DATABASE_URL", ""), args.production)
        files = migration_files()
        with psycopg.connect(url, autocommit=True, connect_timeout=10) as connection:
            if args.command in {"plan", "status", "fingerprint"}:
                connection.execute("SET default_transaction_read_only=on")
            if args.command == "fingerprint":
                result = {"schema": SCHEMA, "fingerprint": schema_fingerprint(connection)}
            elif args.command == "apply":
                manifest_path = MIGRATIONS / "baseline.json"
                manifest = json.loads(manifest_path.read_text()) if manifest_path.exists() else None
                result = {
                    "applied": apply(connection, files, adopt_baseline=args.adopt_baseline, manifest=manifest)
                }
            else:
                result = {"migrations": plan(connection, files)}
        print(json.dumps(result, ensure_ascii=False))
    except ValueError as error:
        parser.exit(1, str(error) + "\n")
    except psycopg.Error:
        parser.exit(1, "Opération PostgreSQL échouée ; connexion et détails privés masqués.\n")


if __name__ == "__main__":
    main()
