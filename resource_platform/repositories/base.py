"""Backend-neutral repository primitives."""
from __future__ import annotations

class Repository:
    def __init__(self, backend, connection):
        if connection is None:
            raise ValueError("A backend-managed connection is required")
        self.backend = backend
        self.connection = connection

    def _execute(self, query: str, params=()):
        return self.backend.execute(self.connection, query, params)

    def _fetchone(self, query: str, params=()):
        row = self._execute(query, params).fetchone()
        return row

    def _fetchall(self, query: str, params=()):
        return self._execute(query, params).fetchall()
