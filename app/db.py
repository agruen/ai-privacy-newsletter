"""Database engine and session management (SQLite, WAL mode)."""

from __future__ import annotations

import os
from collections.abc import Iterator
from pathlib import Path

from sqlalchemy import event
from sqlalchemy.engine import Engine
from sqlmodel import Session, SQLModel, create_engine

from app.config import get_settings

_settings = get_settings()

# Ensure the data directory exists before SQLite opens the file.
if _settings.resolved_database_url.startswith("sqlite:///"):
    db_path = _settings.resolved_database_url.removeprefix("sqlite:///")
    Path(db_path).parent.mkdir(parents=True, exist_ok=True)

engine = create_engine(
    _settings.resolved_database_url,
    echo=False,
    connect_args={"check_same_thread": False},
)


@event.listens_for(Engine, "connect")
def _set_sqlite_pragma(dbapi_connection, _connection_record) -> None:
    """Enable WAL + foreign keys for safe light concurrency on SQLite."""
    cursor = dbapi_connection.cursor()
    cursor.execute("PRAGMA journal_mode=WAL;")
    cursor.execute("PRAGMA foreign_keys=ON;")
    cursor.execute("PRAGMA busy_timeout=5000;")
    cursor.close()


# Columns added after the initial schema shipped. SQLModel.create_all() creates
# missing *tables* but never alters existing ones, so new columns on a populated
# table need an explicit (idempotent) ALTER. SQLite supports ADD COLUMN.
_ADDED_COLUMNS = {
    "incident": {
        "llm_screened": "BOOLEAN NOT NULL DEFAULT 0",
        "llm_privacy_angle": "BOOLEAN NOT NULL DEFAULT 0",
        "llm_salience": "VARCHAR NOT NULL DEFAULT ''",
        "llm_privacy_note": "VARCHAR NOT NULL DEFAULT ''",
        "llm_screened_at": "DATETIME",
        "llm_screen_model": "VARCHAR NOT NULL DEFAULT ''",
    },
}


def _ensure_columns() -> None:
    """Add any columns that exist on the models but not yet in the database."""
    with engine.begin() as conn:
        for table, columns in _ADDED_COLUMNS.items():
            present = {
                row[1]  # PRAGMA table_info: (cid, name, type, ...)
                for row in conn.exec_driver_sql(f"PRAGMA table_info({table})")
            }
            for name, ddl in columns.items():
                if name not in present:
                    conn.exec_driver_sql(
                        f"ALTER TABLE {table} ADD COLUMN {name} {ddl}"
                    )


def init_db() -> None:
    """Create tables for all registered models, then apply column migrations."""
    # Import models so they are registered on SQLModel.metadata.
    from app import models  # noqa: F401

    SQLModel.metadata.create_all(engine)
    _ensure_columns()


def get_session() -> Iterator[Session]:
    """FastAPI dependency yielding a database session."""
    with Session(engine) as session:
        yield session
