from __future__ import annotations

import sqlite3
from contextlib import contextmanager
from pathlib import Path
import re
from typing import Any, Iterator


def _sqlite_path(db_url: str) -> str:
    raw_path = db_url.removeprefix("sqlite:///")
    if not raw_path:
        raise ValueError("SQLite URL must include a database path")
    if raw_path != ":memory:":
        Path(raw_path).expanduser().parent.mkdir(parents=True, exist_ok=True)
    return raw_path


class Database:
    """Small transaction wrapper for SQLite and PostgreSQL."""

    def __init__(self, db_url: str):
        self.db_url = db_url
        if db_url.startswith("sqlite:///"):
            self.backend = "sqlite"
            self._sqlite_database = _sqlite_path(db_url)
        elif db_url.startswith(("postgresql://", "postgres://")):
            self.backend = "postgres"
            self._sqlite_database = None
        else:
            raise ValueError(
                "CLOUD_DB_URL must start with sqlite:///, postgresql://, or postgres://"
            )

    @contextmanager
    def connection(self) -> Iterator[Any]:
        if self.backend == "sqlite":
            connection = sqlite3.connect(self._sqlite_database)
            connection.row_factory = sqlite3.Row
        else:
            import psycopg
            from psycopg.rows import dict_row

            connection = psycopg.connect(self.db_url, row_factory=dict_row)

        try:
            yield connection
        finally:
            connection.close()

    @contextmanager
    def transaction(self) -> Iterator[Any]:
        with self.connection() as connection:
            if self.backend == "sqlite":
                try:
                    connection.execute("BEGIN")
                    yield connection
                    connection.commit()
                except Exception:
                    connection.rollback()
                    raise
            else:
                with connection.transaction():
                    yield connection

    def sql(self, statement: str) -> str:
        if self.backend == "postgres":
            converted = statement.replace("?", "%s")
            if re.search(r"%(?!s)", converted):
                raise ValueError(
                    "PostgreSQL SQL must not contain a literal percent sign"
                )
            return converted
        return statement

    def execute(self, connection: Any, statement: str, params: tuple[Any, ...] = ()):
        return connection.execute(self.sql(statement), params)

    def fetchone(
        self, connection: Any, statement: str, params: tuple[Any, ...] = ()
    ) -> Any | None:
        return self.execute(connection, statement, params).fetchone()

    def fetchall(
        self, connection: Any, statement: str, params: tuple[Any, ...] = ()
    ) -> list[Any]:
        return list(self.execute(connection, statement, params).fetchall())

    @staticmethod
    def row_value(row: Any, key: str) -> Any:
        if isinstance(row, sqlite3.Row):
            return row[key]
        return row[key]
