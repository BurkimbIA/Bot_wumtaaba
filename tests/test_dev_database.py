"""Checksummed migrations and explicit baseline adoption on disposable databases."""

import importlib.util
import json
import os
from pathlib import Path
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql
from psycopg.conninfo import conninfo_to_dict, make_conninfo

from bot_wumtaaba.store import Store

spec = importlib.util.spec_from_file_location(
    "dev_database", Path(__file__).parents[1] / "scripts/dev_database.py"
)
migrations = importlib.util.module_from_spec(spec)
spec.loader.exec_module(migrations)


@pytest.mark.parametrize(
    "url",
    [
        "postgresql://x@remote/wumtaaba_dev",
        "postgresql://x@localhost/postgres",
        "postgresql://x@localhost/wumtaaba_dev?host=remote",
    ],
)
def test_remote_targets_require_explicit_production(url):
    with pytest.raises(ValueError):
        migrations.validate_target(url)
    assert migrations.validate_target(url, production=True) == url


@pytest.fixture
def cli_connection(monkeypatch):
    class Connection:
        def __init__(self):
            self.urls = []
            self.queries = []

        def __enter__(self):
            return self

        def __exit__(self, *args):
            return False

        def execute(self, query):
            self.queries.append(query)

    connection = Connection()

    def connect(url, **kwargs):
        connection.urls.append(url)
        return connection

    monkeypatch.setattr(migrations.psycopg, "connect", connect)
    monkeypatch.setattr(migrations, "plan", lambda connection, files: [])
    return connection


def test_cli_defaults_to_dev_file_ignoring_process_database(monkeypatch, tmp_path, cli_connection, capsys):
    url = "postgresql://user:local-secret@127.0.0.1/wumtaaba_dev"
    (tmp_path / ".env.dev").write_text(f"DATABASE_URL={url}\nDATABASE_SCHEMA=wuumtaaba\n")
    monkeypatch.setattr(migrations, "ROOT", tmp_path)
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:production-secret@remote/postgres")
    migrations.main(["plan"])
    assert cli_connection.urls == [url]
    assert cli_connection.queries == ["SET default_transaction_read_only=on"]
    assert json.loads(capsys.readouterr().out) == {"migrations": []}


def test_cli_explicit_env_file_is_used(monkeypatch, tmp_path, cli_connection):
    url = "postgresql://user:local-secret@localhost/wumtaaba_dev"
    path = tmp_path / "selected.env"
    path.write_text(f"DATABASE_URL={url}\n")
    monkeypatch.setenv("DATABASE_URL", "postgresql://private:production-secret@remote/postgres")
    migrations.main(["status", "--env-file", str(path)])
    assert cli_connection.urls == [url]


def test_cli_runtime_environment_needs_no_dotenv_file(monkeypatch, cli_connection, capsys):
    url = "postgresql://private:production-secret@remote/postgres"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_SCHEMA", "wuumtaaba")
    monkeypatch.setattr(migrations, "dotenv_values", lambda path: pytest.fail("No dotenv file may be read"))
    migrations.main(["plan", "--from-environment", "--production"])
    assert cli_connection.urls == [url]
    output = capsys.readouterr()
    assert json.loads(output.out) == {"migrations": []}
    assert "production-secret" not in output.out + output.err


def test_cli_sources_are_mutually_exclusive(cli_connection, capsys):
    with pytest.raises(SystemExit) as error:
        migrations.main(["plan", "--from-environment", "--env-file", ".env.prod"])
    assert error.value.code == 2
    assert cli_connection.urls == []
    assert "not allowed with argument" in capsys.readouterr().err


@pytest.mark.parametrize(
    ("url", "schema", "production", "message"),
    [
        ("postgresql://private:secret@remote/postgres", "wuumtaaba", False, "Cible distante refusée"),
        ("postgresql://private:secret@remote/postgres", "other_schema", True, "schéma de migrations"),
    ],
)
def test_cli_environment_keeps_target_and_schema_guards(
    monkeypatch, cli_connection, capsys, url, schema, production, message
):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_SCHEMA", schema)
    args = ["plan", "--from-environment"] + (["--production"] if production else [])
    with pytest.raises(SystemExit) as error:
        migrations.main(args)
    assert error.value.code == 1
    assert cli_connection.urls == []
    output = capsys.readouterr()
    assert message in output.err
    assert "private:secret" not in output.out + output.err


def test_cli_connection_errors_do_not_expose_runtime_secrets(monkeypatch, capsys):
    url = "postgresql://private:production-secret@remote/postgres"
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_SCHEMA", "wuumtaaba")

    def fail_connection(*args, **kwargs):
        raise psycopg.OperationalError("Cannot connect to " + url)

    monkeypatch.setattr(migrations.psycopg, "connect", fail_connection)
    with pytest.raises(SystemExit) as error:
        migrations.main(["plan", "--from-environment", "--production"])
    assert error.value.code == 1
    output = capsys.readouterr()
    assert "détails privés masqués" in output.err
    assert "production-secret" not in output.out + output.err


@pytest.mark.parametrize("production", [False, True])
@pytest.mark.parametrize(
    "url",
    [
        "postgresql://user:dummy-secret＠host/postgres",
        "postgresql://user:dummy-secret@127.0.0.1:wrong/wumtaaba_dev",
        "postgresql://user:dummy-secret@127.0.0.1:65536/wumtaaba_dev",
        "postgresql://user:dummy-secret@[::1/wumtaaba_dev",
    ],
)
def test_cli_malformed_urls_never_expose_runtime_secrets(
    monkeypatch, cli_connection, capsys, url, production
):
    monkeypatch.setenv("DATABASE_URL", url)
    monkeypatch.setenv("DATABASE_SCHEMA", "wuumtaaba")
    args = ["plan", "--from-environment"] + (["--production"] if production else [])
    with pytest.raises(SystemExit) as error:
        migrations.main(args)
    assert error.value.code == 1
    assert cli_connection.urls == []
    output = capsys.readouterr()
    assert output.err == "URL PostgreSQL invalide ; détails privés masqués.\n"
    assert "dummy-secret" not in output.out + output.err


@pytest.fixture
def migration_database():
    url = os.environ.get(
        "DEV_DATABASE_TEST_URL",
        os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"),
    )
    params = conninfo_to_dict(url)
    assert params.get("host") in {"localhost", "127.0.0.1", "::1"}
    name = "wumtaaba_migration_test_" + uuid4().hex[:12]
    with psycopg.connect(url, autocommit=True) as admin:
        admin.execute(sql.SQL("CREATE DATABASE {}").format(sql.Identifier(name)))
        try:
            with psycopg.connect(**{**params, "dbname": name}, autocommit=True) as connection:
                yield connection
        finally:
            admin.execute(sql.SQL("DROP DATABASE {} WITH (FORCE)").format(sql.Identifier(name)))


def baseline(tmp_path):
    path = tmp_path / "0001_production_baseline.sql"
    path.write_text(
        "CREATE SCHEMA wuumtaaba; CREATE TABLE wuumtaaba.example(id integer primary key, note text); ALTER TABLE wuumtaaba.example ENABLE ROW LEVEL SECURITY;"
    )
    return path


def test_plan_does_not_write_and_apply_is_idempotent(migration_database, tmp_path):
    db = migration_database
    path = baseline(tmp_path)
    assert migrations.plan(db, [path])[0]["applied"] is False
    assert db.execute("SELECT to_regnamespace('wumtaaba_migrations')").fetchone()[0] is None
    assert migrations.apply(db, [path]) == [path.name]
    assert migrations.apply(db, [path]) == []
    path.write_text(path.read_text() + "\n-- changed")
    with pytest.raises(ValueError, match="modifiée"):
        migrations.plan(db, [path])


def test_existing_baseline_requires_exact_fingerprint(migration_database, tmp_path):
    db = migration_database
    path = baseline(tmp_path)
    db.execute(path.read_text())
    expected = migrations.schema_fingerprint(db)
    with pytest.raises(ValueError, match="explicitement"):
        migrations.apply(db, [path])
    manifest = {"baseline": path.name, "schema": "wuumtaaba", "fingerprint": expected}
    db.execute("ALTER TABLE wuumtaaba.example ADD COLUMN unexpected text")
    with pytest.raises(ValueError, match="diffère"):
        migrations.apply(db, [path], adopt_baseline=True, manifest=manifest)
    db.execute("ALTER TABLE wuumtaaba.example DROP COLUMN unexpected")
    assert migrations.schema_fingerprint(db) == expected
    assert migrations.apply(db, [path], adopt_baseline=True, manifest=manifest) == [path.name]


def test_failed_migration_rolls_back_all_changes_and_ledger(migration_database, tmp_path):
    db = migration_database
    first = baseline(tmp_path)
    second = tmp_path / "0002_broken.sql"
    second.write_text("CREATE TABLE wuumtaaba.rollback_test(id int); SELECT nonexistent_function();")
    with pytest.raises(psycopg.Error):
        migrations.apply(db, [first, second])
    assert db.execute("SELECT to_regnamespace('wuumtaaba')").fetchone()[0] is None
    assert db.execute("SELECT to_regnamespace('wumtaaba_migrations')").fetchone()[0] is None


def test_versioned_schema_matches_runtime_without_hidden_ddl(migration_database):
    db = migration_database
    files = migrations.migration_files()
    manifest = json.loads((migrations.MIGRATIONS / "baseline.json").read_text())
    assert migrations.apply(db, files, manifest=manifest) == [path.name for path in files]
    expected = migrations.schema_fingerprint(db)
    url = os.environ.get(
        "DEV_DATABASE_TEST_URL",
        os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres"),
    )
    connection_info = make_conninfo(**{**conninfo_to_dict(url), "dbname": db.info.dbname})
    store = Store(connection_info, migrations.SCHEMA)
    store.close()
    assert migrations.schema_fingerprint(db) == expected
    assert db.execute(
        "SELECT relrowsecurity FROM pg_class WHERE oid='wuumtaaba.admin_sessions'::regclass"
    ).fetchone()[0] is True
