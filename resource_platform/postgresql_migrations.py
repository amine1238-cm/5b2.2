"""PostgreSQL schema migration tooling for Increment 5B1.

This module intentionally supports schema migration and verification only. It
contains no PostgreSQL application CRUD or connection-pool implementation.
"""
from __future__ import annotations

import argparse
import hashlib
import json
import os
import re
from pathlib import Path
from typing import Iterable

PROJECT_ROOT = Path(__file__).resolve().parent.parent
POSTGRES_MIGRATIONS_DIR = PROJECT_ROOT / "migrations_postgresql"
POSTGRES_MANIFEST = PROJECT_ROOT / "migrations_postgresql.manifest.json"
_VERSION_RE = re.compile(r"^(\d+)_([^/]+)\.sql$")


def migration_files(directory: Path = POSTGRES_MIGRATIONS_DIR) -> list[Path]:
    files = sorted(directory.glob("*.sql"))
    versions: set[int] = set()
    for path in files:
        match = _VERSION_RE.match(path.name)
        if not match:
            raise ValueError("Invalid PostgreSQL migration filename")
        version = int(match.group(1))
        if version in versions:
            raise ValueError("Duplicate PostgreSQL migration version")
        versions.add(version)
    return files


def migration_manifest(directory: Path = POSTGRES_MIGRATIONS_DIR) -> dict[str, str]:
    return {
        path.name: hashlib.sha256(path.read_bytes()).hexdigest()
        for path in migration_files(directory)
    }


def verify_manifest(
    directory: Path = POSTGRES_MIGRATIONS_DIR,
    manifest_path: Path = POSTGRES_MANIFEST,
) -> bool:
    actual = migration_manifest(directory)
    manifest_path = Path(manifest_path)
    if not manifest_path.exists():
        raise RuntimeError("PostgreSQL migration manifest is missing")
    try:
        expected = json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc:
        raise RuntimeError("PostgreSQL migration manifest is invalid") from exc
    if not isinstance(expected, dict) or set(expected) != set(actual):
        raise RuntimeError("PostgreSQL migration integrity check failed")
    for name, digest in expected.items():
        if not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64:
            raise RuntimeError("PostgreSQL migration manifest is invalid")
        if actual.get(name) != digest:
            raise RuntimeError("PostgreSQL migration integrity check failed")
    return True


def _quote_identifier(value: str) -> str:
    if not re.fullmatch(r"[A-Za-z_][A-Za-z0-9_]*", value):
        raise ValueError("Invalid PostgreSQL schema name")
    return '"' + value.replace('"', '""') + '"'


def connect(database_url: str):
    if not database_url:
        raise ValueError("DATABASE_URL is required")
    try:
        import psycopg
    except ImportError as exc:
        raise RuntimeError("PostgreSQL migration support requires psycopg") from exc
    return psycopg.connect(database_url)


def migrate(
    database_url: str,
    directory: Path = POSTGRES_MIGRATIONS_DIR,
    manifest_path: Path = POSTGRES_MANIFEST,
    schema: str = "public",
) -> list[int]:
    verify_manifest(directory, manifest_path)
    quoted_schema = _quote_identifier(schema)
    db = connect(database_url)
    applied_now: list[int] = []
    try:
        with db:
            with db.cursor() as cur:
                cur.execute(f"SET search_path TO {quoted_schema}, public")
                cur.execute(
                    "CREATE TABLE IF NOT EXISTS schema_migrations "
                    "(version INTEGER PRIMARY KEY, applied_at TIMESTAMPTZ NOT NULL DEFAULT CURRENT_TIMESTAMP)"
                )
                cur.execute("SELECT version FROM schema_migrations ORDER BY version")
                applied = {int(row[0]) for row in cur.fetchall()}
                files = migration_files(directory)
                expected_versions = {int(_VERSION_RE.match(p.name).group(1)) for p in files}
                unexpected = applied - expected_versions
                if unexpected:
                    raise RuntimeError("Unexpected PostgreSQL migration version")
                for path in files:
                    version = int(_VERSION_RE.match(path.name).group(1))
                    if version in applied:
                        continue
                    cur.execute(path.read_text(encoding="utf-8"))
                    cur.execute("INSERT INTO schema_migrations(version) VALUES (%s)", (version,))
                    applied_now.append(version)
        return applied_now
    finally:
        db.close()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Manage PostgreSQL schema migrations only.")
    sub = parser.add_subparsers(dest="command")
    migrate_parser = sub.add_parser("migrate")
    migrate_parser.add_argument("--database-url", default=os.getenv("DATABASE_URL"))
    migrate_parser.add_argument("--schema", default="public")
    args = parser.parse_args(argv)
    if args.command != "migrate":
        parser.print_help()
        return 0
    try:
        applied = migrate(args.database_url, schema=args.schema)
        print(f"PostgreSQL schema migration successful ({len(applied)} migration(s) applied).")
        return 0
    except Exception as exc:
        print(f"PostgreSQL schema migration failed: {type(exc).__name__}: {exc}")
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
