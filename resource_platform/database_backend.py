"""Database backend primitives for SQLite and PostgreSQL.

Increment 5B2.1 provides PostgreSQL connectivity primitives only. Business
repositories and application CRUD remain intentionally outside this increment.
"""
from __future__ import annotations

from contextlib import contextmanager
from dataclasses import dataclass
from enum import Enum
from pathlib import Path
from typing import Any, Iterator
from urllib.parse import urlparse
import re
import sqlite3


class DatabaseErrorCategory(str, Enum):
    BUSINESS_CONFLICT = "BusinessConflict"
    AUTHORIZATION_FAILURE = "AuthorizationFailure"
    VALIDATION_FAILURE = "ValidationFailure"
    TRANSIENT_DATABASE_FAILURE = "TransientDatabaseFailure"
    CONNECTION_FAILURE = "ConnectionFailure"
    INTEGRITY_FAILURE = "IntegrityFailure"
    UNEXPECTED_DATABASE_FAILURE = "UnexpectedDatabaseFailure"

BusinessConflict = DatabaseErrorCategory.BUSINESS_CONFLICT
AuthorizationFailure = DatabaseErrorCategory.AUTHORIZATION_FAILURE
ValidationFailure = DatabaseErrorCategory.VALIDATION_FAILURE
TransientDatabaseFailure = DatabaseErrorCategory.TRANSIENT_DATABASE_FAILURE
ConnectionFailure = DatabaseErrorCategory.CONNECTION_FAILURE
IntegrityFailure = DatabaseErrorCategory.INTEGRITY_FAILURE
UnexpectedDatabaseFailure = DatabaseErrorCategory.UNEXPECTED_DATABASE_FAILURE


class BackendNotImplementedError(RuntimeError):
    """Retained for compatibility with the Increment 5A public API."""


class PostgreSQLDependencyError(RuntimeError):
    """Raised when psycopg or psycopg_pool is unavailable."""


class PostgreSQLBackendError(RuntimeError):
    """Safe PostgreSQL error carrying only a stable category."""

    def __init__(self, category: DatabaseErrorCategory, message: str = "PostgreSQL backend operation failed"):
        super().__init__(message)
        self.category = category


class DatabaseBackend:
    name: str

    def connect(self):
        raise NotImplementedError

    def acquire(self):
        return self.connect()

    def release(self, connection):
        if connection is not None and hasattr(connection, "close"):
            connection.close()

    def begin(self, connection):
        raise NotImplementedError

    def commit(self, connection):
        connection.commit()

    def rollback(self, connection):
        connection.rollback()

    @contextmanager
    def transaction(self, connection=None) -> Iterator[Any]:
        owned = connection is None
        conn = self.acquire() if owned else connection
        try:
            self.begin(conn)
            try:
                yield conn
            except BaseException:
                try:
                    self.rollback(conn)
                finally:
                    raise
            else:
                self.commit(conn)
        finally:
            if owned:
                self.release(conn)

    def health_check(self) -> bool:
        raise NotImplementedError

    def close(self) -> None:
        return None

    def classify_error(self, error: BaseException) -> DatabaseErrorCategory:
        return classify_database_error(error)

    def sql(self, query: str) -> str:
        """Return a query unchanged for compatibility.

        Repository code should use ``prepare`` so named parameters are rendered
        together with their ordered values. This method remains available for
        callers that already provide backend-native placeholders.
        """
        return query

    def prepare(self, query: str, params=None):
        """Render named parameters safely and return ``(sql, ordered_values)``."""
        style = "sqlite" if self.name == "sqlite" else "postgresql"
        return render_named_query(query, params or {}, style=style)


    def execute(self, connection, sql: str, params=()):
        """Execute a parameterized statement without interpolating values."""
        return connection.execute(sql, params)


def render_named_query(query: str, params, *, style: str):
    """Render ``:name`` markers without touching literals, casts, or comments.

    Values are never interpolated. The returned tuple is ordered according to
    marker appearance; repeated markers repeat their value.
    """
    if not isinstance(params, dict):
        raise TypeError("named SQL parameters must be a mapping")
    if style not in {"sqlite", "postgresql"}:
        raise ValueError("unsupported SQL parameter style")
    marker = "?" if style == "sqlite" else "%s"
    out, ordered, used = [], [], set()
    i, n = 0, len(query)
    state = "normal"
    while i < n:
        ch = query[i]
        nxt = query[i + 1] if i + 1 < n else ""
        if state == "normal":
            if ch == "'": state = "single"; out.append(ch); i += 1; continue
            if ch == '"': state = "double"; out.append(ch); i += 1; continue
            if ch == "-" and nxt == "-": state = "line_comment"; out.extend((ch,nxt)); i += 2; continue
            if ch == "/" and nxt == "*": state = "block_comment"; out.extend((ch,nxt)); i += 2; continue
            if ch == ":" and nxt != ":" and (i == 0 or query[i-1] != ":"):
                j = i + 1
                if j < n and (query[j].isalpha() or query[j] == "_"):
                    j += 1
                    while j < n and (query[j].isalnum() or query[j] == "_"): j += 1
                    name = query[i+1:j]
                    if name not in params: raise ValueError(f"missing SQL parameter: {name}")
                    out.append(marker); ordered.append(params[name]); used.add(name); i = j; continue
            out.append(ch); i += 1; continue
        if state == "single":
            out.append(ch)
            if ch == "'":
                if nxt == "'": out.append(nxt); i += 2; continue
                state = "normal"
            i += 1; continue
        if state == "double":
            out.append(ch)
            if ch == '"':
                if nxt == '"': out.append(nxt); i += 2; continue
                state = "normal"
            i += 1; continue
        if state == "line_comment":
            out.append(ch); i += 1
            if ch == "\n": state = "normal"
            continue
        if state == "block_comment":
            out.append(ch); i += 1
            if ch == "*" and nxt == "/": out.append(nxt); i += 2; state = "normal"
            continue
    unused = set(params) - used
    if unused: raise ValueError("unused SQL parameters: " + ", ".join(sorted(unused)))
    return "".join(out), tuple(ordered)


def _postgres_named_query(query: str) -> str:
    return render_named_query(query, {name: None for name in _marker_names(query)}, style="postgresql")[0]


def _marker_names(query: str):
    names=[]
    # compatibility helper only; full validation is done by render_named_query
    import re
    for m in re.finditer(r"(?<!:):([A-Za-z_][A-Za-z0-9_]*)", query):
        if m.group(1) not in names: names.append(m.group(1))
    return names


@dataclass(frozen=True)
class SQLiteBackend(DatabaseBackend):
    database_path: Path | str
    name: str = "sqlite"

    def connect(self):
        path = str(self.database_path)
        if path != ":memory:":
            Path(path).parent.mkdir(parents=True, exist_ok=True)
        db = sqlite3.connect(path, check_same_thread=False)
        db.row_factory = sqlite3.Row
        db.execute("PRAGMA foreign_keys=ON")
        db.execute("PRAGMA busy_timeout=5000")
        return db

    def begin(self, connection):
        connection.execute("BEGIN IMMEDIATE")

    def health_check(self) -> bool:
        conn = self.connect()
        try:
            conn.execute("SELECT 1").fetchone()
            return True
        finally:
            conn.close()


@dataclass(frozen=True)
class ParsedDatabaseTarget:
    backend: str
    database_path: Path | None = None
    database_url: str = ""


class PostgreSQLBackend(DatabaseBackend):
    """PostgreSQL connection-pool backend; no business CRUD is implemented here."""

    name = "postgresql"

    def __init__(self, database_url: str, *, min_size: int = 1, max_size: int = 10,
                 connection_timeout: float = 5.0, acquisition_timeout: float = 5.0,
                 health_check_timeout: float = 3.0, sslmode: str = "prefer"):
        target = parse_database_target(backend="postgresql", database_url=database_url)
        self.database_url = target.database_url
        self.min_size = int(min_size)
        self.max_size = int(max_size)
        self.connection_timeout = float(connection_timeout)
        self.acquisition_timeout = float(acquisition_timeout)
        self.health_check_timeout = float(health_check_timeout)
        self.sslmode = sslmode or "prefer"
        if self.min_size <= 0 or self.max_size < self.min_size:
            raise ValueError("Invalid PostgreSQL pool size configuration")
        if min(self.connection_timeout, self.acquisition_timeout, self.health_check_timeout) <= 0:
            raise ValueError("PostgreSQL timeouts must be positive")
        self._pool = None

    def _imports(self):
        try:
            import psycopg
            from psycopg.rows import dict_row
            from psycopg_pool import ConnectionPool, PoolTimeout
            return psycopg, ConnectionPool, PoolTimeout, dict_row
        except ImportError as exc:
            raise PostgreSQLDependencyError(
                "PostgreSQL runtime dependencies are unavailable"
            ) from exc

    def _conninfo(self) -> str:
        return self.database_url

    def _connection_kwargs(self, dict_row):
        kwargs = {"connect_timeout": int(self.connection_timeout), "row_factory": dict_row}
        # Keep SSL mode in connection options without logging the URL.
        if self.sslmode:
            kwargs["sslmode"] = self.sslmode
        return kwargs

    def open(self):
        if self._pool is not None:
            return self
        _, ConnectionPool, _, dict_row = self._imports()
        try:
            pool = ConnectionPool(
                conninfo=self._conninfo(),
                min_size=self.min_size,
                max_size=self.max_size,
                timeout=self.acquisition_timeout,
                open=False,
                kwargs=self._connection_kwargs(dict_row),
            )
            pool.open(wait=True, timeout=self.connection_timeout)
        except PostgreSQLDependencyError:
            raise
        except Exception as exc:
            raise PostgreSQLBackendError(classify_postgresql_exception(exc)) from exc
        self._pool = pool
        return self

    def connect(self):
        """Open one direct connection; pool users should prefer acquire()."""
        psycopg, _, _, dict_row = self._imports()
        try:
            return psycopg.connect(
                self._conninfo(), **self._connection_kwargs(dict_row)
            )
        except Exception as exc:
            raise PostgreSQLBackendError(classify_postgresql_exception(exc)) from exc

    def acquire(self):
        self.open()
        try:
            conn = self._pool.getconn(timeout=self.acquisition_timeout)
            if bool(getattr(conn, "broken", False)) or bool(getattr(conn, "closed", False)):
                self._discard(conn)
                conn = self._pool.getconn(timeout=self.acquisition_timeout)
            return conn
        except PostgreSQLBackendError:
            raise
        except Exception as exc:
            raise PostgreSQLBackendError(classify_postgresql_exception(exc)) from exc

    def _discard(self, connection):
        try:
            self._pool.putconn(connection, destroy=True)
            return
        except (TypeError, AttributeError):
            pass
        try:
            if hasattr(connection, "close"):
                connection.close()
        finally:
            try:
                self._pool.putconn(connection)
            except Exception:
                pass

    def release(self, connection):
        if connection is None:
            return
        if self._pool is None:
            try:
                connection.close()
            except Exception:
                pass
            return
        broken = bool(getattr(connection, "broken", False) or getattr(connection, "closed", False))
        try:
            if not broken and hasattr(connection, "rollback"):
                connection.rollback()
        except Exception:
            broken = True
        if broken:
            self._discard(connection)
        else:
            try:
                self._pool.putconn(connection)
            except Exception:
                try:
                    connection.close()
                except Exception:
                    pass

    def begin(self, connection):
        connection.execute("BEGIN")

    def health_check(self) -> bool:
        conn = self.acquire()
        try:
            with conn.cursor() as cur:
                cur.execute("SELECT 1")
                cur.fetchone()
            return True
        except Exception as exc:
            try:
                conn.rollback()
            except Exception:
                pass
            raise PostgreSQLBackendError(classify_postgresql_exception(exc)) from exc
        finally:
            self.release(conn)

    def close(self):
        pool, self._pool = self._pool, None
        if pool is not None:
            try:
                pool.close(timeout=self.health_check_timeout)
            except TypeError:
                pool.close()

    def classify_error(self, error):
        return classify_postgresql_exception(error)

    def execute(self, connection, sql: str, params=()):
        """Execute a safe PostgreSQL statement using driver parameter binding.

        PostgreSQL statements must use %s placeholders. Values are never
        interpolated into SQL text.
        """
        return connection.execute(sql, params)


_SQLSTATE_CATEGORY = {
    "23505": BusinessConflict,
    "23P01": BusinessConflict,
    "23503": IntegrityFailure,
    "40001": TransientDatabaseFailure,
    "40P01": TransientDatabaseFailure,
    "55P03": TransientDatabaseFailure,
    "08001": ConnectionFailure,
    "08006": ConnectionFailure,
}


def classify_postgresql_exception(error: BaseException) -> DatabaseErrorCategory:
    state = getattr(error, "sqlstate", None)
    if callable(state):
        state = state()
    if state in _SQLSTATE_CATEGORY:
        return _SQLSTATE_CATEGORY[state]
    try:
        from psycopg_pool import PoolTimeout
        if isinstance(error, PoolTimeout):
            return TransientDatabaseFailure
    except ImportError:
        pass
    try:
        import psycopg
        if isinstance(error, (psycopg.OperationalError, psycopg.InterfaceError)):
            return ConnectionFailure
    except ImportError:
        pass
    if isinstance(error, (ValueError, TypeError)):
        return ValidationFailure
    return UnexpectedDatabaseFailure


def classify_database_error(error: BaseException) -> DatabaseErrorCategory:
    if getattr(error, "sqlstate", None):
        return classify_postgresql_exception(error)
    text = str(error).lower()
    if isinstance(error, sqlite3.IntegrityError):
        return IntegrityFailure
    if isinstance(error, sqlite3.OperationalError):
        if any(w in text for w in ("locked", "busy", "timeout")):
            return TransientDatabaseFailure
        if any(w in text for w in ("unable to open", "cannot open", "disk i/o")):
            return ConnectionFailure
        return UnexpectedDatabaseFailure
    if isinstance(error, (sqlite3.InterfaceError, sqlite3.DatabaseError)):
        return ConnectionFailure
    if isinstance(error, (ValueError, TypeError)):
        return ValidationFailure
    if isinstance(error, PermissionError):
        return AuthorizationFailure
    return UnexpectedDatabaseFailure


def backend_from_settings(settings) -> DatabaseBackend:
    backend = (getattr(settings, "database_backend", "") or "").lower()
    if not backend:
        backend = (
            "postgresql"
            if urlparse(getattr(settings, "database_url", "") or "").scheme
            in {"postgres", "postgresql"}
            else "sqlite"
        )
    if backend == "sqlite":
        return SQLiteBackend(settings.database_path)
    if backend in {"postgres", "postgresql"}:
        return PostgreSQLBackend(
            settings.database_url,
            min_size=getattr(settings, "db_pool_min_size", 1),
            max_size=getattr(settings, "db_pool_max_size", 10),
            connection_timeout=getattr(settings, "db_connection_timeout_seconds", 5),
            acquisition_timeout=getattr(settings, "db_pool_acquisition_timeout_seconds", 5),
            health_check_timeout=getattr(settings, "db_health_check_timeout_seconds", 3),
            sslmode=getattr(settings, "database_sslmode", "prefer"),
        )
    raise ValueError("DATABASE_BACKEND is invalid")


def parse_database_target(*, backend: str, database_path=None, database_url: str = "") -> ParsedDatabaseTarget:
    backend = (backend or "").strip().lower()
    if backend == "sqlite":
        if not database_path:
            raise ValueError("DATABASE_PATH is required for SQLite")
        return ParsedDatabaseTarget("sqlite", Path(database_path), "")
    if backend in {"postgres", "postgresql"}:
        parsed = urlparse(database_url or "")
        if (
            parsed.scheme not in {"postgres", "postgresql"}
            or not parsed.hostname
            or not parsed.path
            or parsed.path == "/"
        ):
            raise ValueError("DATABASE_URL is invalid")
        return ParsedDatabaseTarget("postgresql", None, database_url)
    raise ValueError("DATABASE_BACKEND is invalid")
