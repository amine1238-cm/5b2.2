import argparse, hashlib, json, sqlite3
from pathlib import Path
from .config import MIGRATIONS_DIR, PROJECT_ROOT, Settings
from .database_backend import SQLiteBackend, backend_from_settings, classify_database_error, DatabaseErrorCategory, BackendNotImplementedError

def connect(path):
    """Backward-compatible SQLite connection factory."""
    return SQLiteBackend(path).connect()

def backend(settings):
    """Select a backend from validated settings. PostgreSQL is reserved for 5B."""
    return backend_from_settings(settings)

def _migration_files(migrations_dir=MIGRATIONS_DIR):
    files=sorted(Path(migrations_dir).glob("*.sql")); seen=set()
    for file in files:
        try: version=int(file.name.split("_",1)[0])
        except (ValueError, IndexError) as exc: raise ValueError(f"Invalid migration filename: {file.name}") from exc
        if version in seen: raise ValueError(f"Duplicate migration version: {version}")
        seen.add(version)
    return files

def migration_manifest(migrations_dir=MIGRATIONS_DIR):
    return {f.name: hashlib.sha256(f.read_bytes()).hexdigest() for f in _migration_files(migrations_dir)}

def verify_migration_integrity(migrations_dir=MIGRATIONS_DIR, manifest_path=None):
    actual=migration_manifest(migrations_dir)
    manifest_path=Path(manifest_path or PROJECT_ROOT/"migrations.manifest.json")
    if not manifest_path.exists(): raise RuntimeError("Migration integrity manifest is missing")
    try: expected=json.loads(manifest_path.read_text(encoding="utf-8"))
    except Exception as exc: raise RuntimeError("Migration integrity manifest is invalid") from exc
    if not isinstance(expected, dict):
        raise RuntimeError("Migration integrity manifest is invalid")
    if set(expected) != set(actual):
        raise RuntimeError("Migration integrity check failed")
    for name, digest in expected.items():
        if not isinstance(name, str) or not isinstance(digest, str) or len(digest) != 64:
            raise RuntimeError("Migration integrity manifest is invalid")
        if actual.get(name) != digest:
            raise RuntimeError("Migration integrity check failed")
    return True

def verify_database_ready(db, migrations_dir=MIGRATIONS_DIR, manifest_path=None):
    verify_migration_integrity(migrations_dir, manifest_path)
    try:
        rows=db.execute("SELECT version FROM schema_migrations ORDER BY version").fetchall()
    except sqlite3.Error as exc:
        raise RuntimeError("Required migration table is unavailable") from exc
    files = _migration_files(migrations_dir)
    expected=sorted(int(f.name.split("_",1)[0]) for f in files)
    applied=[int(r[0]) for r in rows]
    if len(applied) != len(set(applied)) or applied != expected:
        raise RuntimeError("Database migrations are incomplete or unexpected")
    return True

def migrate(path, migrations_dir=MIGRATIONS_DIR):
    db=connect(path); applied_now=[]
    try:
        with db:
            db.execute("CREATE TABLE IF NOT EXISTS schema_migrations (version INTEGER PRIMARY KEY, applied_at TEXT NOT NULL DEFAULT CURRENT_TIMESTAMP)")
            applied={row[0] for row in db.execute("SELECT version FROM schema_migrations")}
            files=_migration_files(migrations_dir)
            versions={int(f.name.split("_",1)[0]) for f in files}
            unexpected=applied-versions
            if unexpected: raise ValueError(f"Unexpected applied migration version(s): {sorted(unexpected)}")
            for file in files:
                version=int(file.name.split("_",1)[0])
                if version not in applied:
                    db.executescript(file.read_text(encoding="utf-8"))
                    db.execute("INSERT INTO schema_migrations(version) VALUES (?)",(version,)); applied_now.append(version)
            return applied_now
    finally: db.close()

def build_parser():
    parser=argparse.ArgumentParser(prog="python -m resource_platform.database",description="Manage the Resource Platform database.")
    sub=parser.add_subparsers(dest="command"); mp=sub.add_parser("migrate",help="Apply pending database migrations"); mp.add_argument("--database-path",default=None)
    return parser

def main(argv=None):
    args=build_parser().parse_args(argv)
    if args.command=="migrate":
        try:
            path=args.database_path or Settings.from_env().database_path; applied=migrate(path); print(f"Migration successful: {path} ({len(applied)} migration(s) applied). "); return 0
        except Exception as exc: print(f"Migration failed: {type(exc).__name__}: {exc}"); return 1
    build_parser().print_help(); return 0
if __name__=="__main__": raise SystemExit(main())
