from dataclasses import dataclass
from pathlib import Path
from urllib.parse import urlparse
import os
import re

PACKAGE_ROOT = Path(__file__).resolve().parent
PROJECT_ROOT = PACKAGE_ROOT.parent
MIGRATIONS_DIR = PROJECT_ROOT / "migrations"
_DEV_QR_PLACEHOLDER = "generate-a-separate-fernet-key-per-environment"
_DEV_SESSION_PLACEHOLDER = "development-only-secret-change-me"
_ALLOWED_ENVIRONMENTS = {"development", "test", "staging", "production"}
_ALLOWED_LOG_LEVELS = {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}
_HOST_RE = re.compile(r"^[A-Za-z0-9](?:[A-Za-z0-9._:-]*[A-Za-z0-9])?$|^\*$")

@dataclass(frozen=True)
class Settings:
    database_path: Path
    session_secret: str
    cookie_secure: bool = False
    session_max_age: int = 8 * 60 * 60
    environment: str = "development"
    host: str = "127.0.0.1"
    port: int = 8000
    qr_token_encryption_key: str = ""
    database_url: str = ""
    database_backend: str = ""
    allowed_hosts: tuple = ()
    log_level: str = "INFO"
    migration_manifest_path: Path = PROJECT_ROOT / "migrations.manifest.json"
    db_pool_min_size: int = 1
    db_pool_max_size: int = 10
    db_connection_timeout_seconds: float = 5.0
    db_pool_acquisition_timeout_seconds: float = 5.0
    db_health_check_timeout_seconds: float = 3.0
    database_sslmode: str = "prefer"

    def validate(self):
        env = (self.environment or "").strip().lower()
        configured_backend = (self.database_backend or "").strip().lower()
        parsed_database = urlparse(self.database_url or "")
        inferred_backend = "postgresql" if parsed_database.scheme in {"postgres", "postgresql"} else "sqlite"
        backend = configured_backend or inferred_backend
        if backend not in {"sqlite", "postgres", "postgresql"}:
            raise RuntimeError("DATABASE_BACKEND is invalid")
        if backend in {"postgres", "postgresql"} and not self.database_url:
            raise RuntimeError("DATABASE_URL must be configured for PostgreSQL")
        if backend == "sqlite" and env in {"staging", "production"}:
            raise RuntimeError("SQLite is not permitted in staging or production")
        if env == "test" and backend in {"postgres", "postgresql"} and not self.database_url:
            raise RuntimeError("DATABASE_URL must be configured for PostgreSQL tests")
        if env not in _ALLOWED_ENVIRONMENTS:
            raise RuntimeError("APP_ENV is invalid")
        if not isinstance(self.host, str) or not self.host.strip() or any(c.isspace() for c in self.host):
            raise RuntimeError("HOST is invalid")
        try:
            port = int(self.port)
        except (TypeError, ValueError):
            raise RuntimeError("PORT is invalid")
        if not 1 <= port <= 65535:
            raise RuntimeError("PORT is invalid")
        if not isinstance(self.session_secret, str) or not self.session_secret:
            raise RuntimeError("SESSION_SECRET must be configured")
        if not isinstance(self.log_level, str) or self.log_level.upper() not in _ALLOWED_LOG_LEVELS:
            raise RuntimeError("LOG_LEVEL is invalid")
        for value, label in ((self.db_pool_min_size, "DB_POOL_MIN_SIZE"), (self.db_pool_max_size, "DB_POOL_MAX_SIZE"), (self.db_connection_timeout_seconds, "DB_CONNECTION_TIMEOUT_SECONDS"), (self.db_pool_acquisition_timeout_seconds, "DB_POOL_ACQUISITION_TIMEOUT_SECONDS"), (self.db_health_check_timeout_seconds, "DB_HEALTH_CHECK_TIMEOUT_SECONDS")):
            try:
                if float(value) <= 0:
                    raise ValueError
            except (TypeError, ValueError):
                raise RuntimeError(f"{label} must be positive")
        if int(self.db_pool_max_size) < int(self.db_pool_min_size):
            raise RuntimeError("DB_POOL_MAX_SIZE must not be smaller than DB_POOL_MIN_SIZE")
        if self.database_sslmode not in {"disable", "allow", "prefer", "require", "verify-ca", "verify-full"}:
            raise RuntimeError("DATABASE_SSLMODE is invalid")
        if not isinstance(self.allowed_hosts, tuple):
            raise RuntimeError("ALLOWED_HOSTS is invalid")
        for host in self.allowed_hosts:
            if not isinstance(host, str) or not _HOST_RE.fullmatch(host) or ".." in host:
                raise RuntimeError("ALLOWED_HOSTS contains an invalid host")
        if "*" in self.allowed_hosts and env in {"staging", "production"}:
            raise RuntimeError("Wildcard ALLOWED_HOSTS is not permitted in staging or production")
        if env == "test":
            p = str(self.database_path)
            if not p or p in {".", "resource_platform.sqlite3"} or not Path(p).is_absolute():
                raise RuntimeError("Test configuration requires an isolated absolute database path")
        if env in {"staging", "production"}:
            if self.session_secret in {"", _DEV_SESSION_PLACEHOLDER} or len(self.session_secret) < 16:
                raise RuntimeError("SESSION_SECRET must be explicitly configured in staging or production")
            if not self.qr_token_encryption_key or self.qr_token_encryption_key == _DEV_QR_PLACEHOLDER:
                raise RuntimeError("QR_TOKEN_ENCRYPTION_KEY must be explicitly configured in staging or production")
            if self.cookie_secure is not True:
                raise RuntimeError("COOKIE_SECURE must be enabled in staging or production")
            parsed = urlparse(self.database_url or "")
            if backend not in {"postgres", "postgresql"} or parsed.scheme not in {"postgres", "postgresql"} or not parsed.netloc:
                raise RuntimeError("A valid non-SQLite DATABASE_URL is required in staging or production")
            if not self.allowed_hosts:
                raise RuntimeError("ALLOWED_HOSTS must be configured in staging or production")
        manifest = Path(self.migration_manifest_path)
        if not manifest.is_absolute():
            raise RuntimeError("MIGRATION_MANIFEST_PATH must be absolute")
        return self

    @classmethod
    def from_env(cls, *, database_path=None, session_secret=None):
        raw_hosts = os.getenv("ALLOWED_HOSTS", "")
        hosts = tuple(x.strip().lower() for x in raw_hosts.split(",") if x.strip())
        raw_port = os.getenv("PORT", "8000")
        try: port = int(raw_port)
        except ValueError: port = raw_port
        path = Path(database_path or os.getenv("DATABASE_PATH", str(PROJECT_ROOT / "resource_platform.sqlite3")))
        manifest = Path(os.getenv("MIGRATION_MANIFEST_PATH", str(PROJECT_ROOT / "migrations.manifest.json"))).resolve()
        return cls(
            database_path=path,
            session_secret=session_secret if session_secret is not None else os.getenv("SESSION_SECRET", _DEV_SESSION_PLACEHOLDER),
            cookie_secure=os.getenv("COOKIE_SECURE", "0") == "1",
            session_max_age=int(os.getenv("SESSION_MAX_AGE", str(8 * 60 * 60))),
            environment=os.getenv("APP_ENV", "development").lower(),
            host=os.getenv("HOST", "127.0.0.1"),
            port=port,
            qr_token_encryption_key=os.getenv("QR_TOKEN_ENCRYPTION_KEY", ""),
            database_url=os.getenv("DATABASE_URL", ""),
            database_backend=os.getenv("DATABASE_BACKEND", "sqlite").lower(),
            allowed_hosts=hosts,
            log_level=os.getenv("LOG_LEVEL", "INFO").upper(),
            migration_manifest_path=manifest,
            db_pool_min_size=int(os.getenv("DB_POOL_MIN_SIZE", "1")),
            db_pool_max_size=int(os.getenv("DB_POOL_MAX_SIZE", "10")),
            db_connection_timeout_seconds=float(os.getenv("DB_CONNECTION_TIMEOUT_SECONDS", "5")),
            db_pool_acquisition_timeout_seconds=float(os.getenv("DB_POOL_ACQUISITION_TIMEOUT_SECONDS", "5")),
            db_health_check_timeout_seconds=float(os.getenv("DB_HEALTH_CHECK_TIMEOUT_SECONDS", "3")),
            database_sslmode=os.getenv("DATABASE_SSLMODE", "prefer").lower(),
        ).validate()
