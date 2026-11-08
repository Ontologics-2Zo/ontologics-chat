"""PostgreSQL access and checksum-locked, transactional schema migrations."""

from hashlib import sha256
from pathlib import Path
import re

import psycopg
from psycopg.rows import dict_row

from service.errors import ServiceError


MIGRATIONS = Path(__file__).resolve().parents[1] / "migrations"


class Database:
    def __init__(self, dsn: str, *, schema: str | None = None):
        if not dsn or not dsn.startswith(("postgresql://", "postgres://", "host=")):
            raise ServiceError("POSTGRESQL_REQUIRED", 503)
        self._dsn = dsn
        if schema is not None and not re.fullmatch(r"[a-z][a-z0-9_]{0,62}", schema):
            raise ServiceError("INVALID_DATABASE_SCHEMA", 503)
        self._schema = schema

    def connect(self):
        # DSN and driver errors must not be serialized by the HTTP error handler.
        options = {"options": "-csearch_path=" + self._schema} if self._schema else {}
        return psycopg.connect(self._dsn, row_factory=dict_row, connect_timeout=5, **options)

    def migrate(self, directory: Path = MIGRATIONS) -> list[str]:
        applied = []
        with self.connect() as conn:
            # Serialize schema creation and migrations across starting workers.
            conn.execute("SELECT pg_advisory_xact_lock(%s)", (674958213,))
            conn.execute("""
                CREATE TABLE IF NOT EXISTS schema_migrations (
                    name text PRIMARY KEY,
                    checksum text NOT NULL,
                    applied_at timestamptz NOT NULL DEFAULT now()
                )
            """)
            for path in sorted(directory.glob("*.sql")):
                content = path.read_bytes()
                checksum = sha256(content).hexdigest()
                previous = conn.execute(
                    "SELECT checksum FROM schema_migrations WHERE name = %s", (path.name,)
                ).fetchone()
                if previous:
                    if previous["checksum"] != checksum:
                        raise ServiceError("MIGRATION_CHECKSUM_MISMATCH", 503)
                    continue
                conn.execute(content.decode("utf-8"))
                conn.execute(
                    "INSERT INTO schema_migrations (name, checksum) VALUES (%s, %s)",
                    (path.name, checksum),
                )
                applied.append(path.name)
        return applied

    def health(self) -> dict:
        with self.connect() as conn:
            migrations = conn.execute("SELECT count(*) AS n FROM schema_migrations").fetchone()["n"]
            conn.execute("SELECT 1")
        return {"database": "ready", "migrations": migrations}
