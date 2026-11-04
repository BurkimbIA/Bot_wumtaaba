"""Each test gets its own Postgres schema, dropped afterwards; audio goes to an in-memory bucket."""

import os
from uuid import uuid4

import psycopg
import pytest
from psycopg import sql

from bot_wumtaaba.store import Store

DATABASE_URL = os.environ.get("TEST_DATABASE_URL", "postgresql://postgres:postgres@localhost:5432/postgres")


class FakeMedia:
    def __init__(self):
        self.objects = {}

    async def put(self, name, data, mime):
        self.objects[name] = (data, mime)

    async def get(self, name):
        return self.objects.get(name, (None,))[0]


@pytest.fixture
def schema():
    name = "test_" + uuid4().hex[:12]
    yield name
    with psycopg.connect(DATABASE_URL, autocommit=True) as connection:
        connection.execute(sql.SQL("DROP SCHEMA IF EXISTS {} CASCADE").format(sql.Identifier(name)))


@pytest.fixture
def make_store(schema):
    """make_store() twice returns two connections to the same database, like reopening a file."""
    stores = []

    def make():
        stores.append(Store(DATABASE_URL, schema))
        return stores[-1]

    yield make
    for store in stores:
        store.close()


@pytest.fixture
def store(make_store):
    return make_store()


@pytest.fixture
def database(schema):
    """Settings fields for create_app tests."""
    return {"database_url": DATABASE_URL, "database_schema": schema}


@pytest.fixture
def media():
    return FakeMedia()


@pytest.fixture
def dump():
    """Every row of every table as text, like sqlite3's iterdump, to look for leaked secrets."""

    def run(store):
        tables = store.db.execute("SELECT tablename FROM pg_tables WHERE schemaname=%s", (store.db.schema,))
        return "\n".join(
            str(row)
            for (table,) in [tuple(row.values()) for row in tables]
            for row in store.db.execute(sql.SQL("SELECT * FROM {}").format(sql.Identifier(table)))
        )

    return run
