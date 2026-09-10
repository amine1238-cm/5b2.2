"""Transaction ownership boundary for repositories."""
from __future__ import annotations
from contextlib import contextmanager
from typing import Iterator

class TransactionService:
    def __init__(self, backend):
        self.backend = backend

    @contextmanager
    def transaction(self) -> Iterator[object]:
        """Own one backend transaction; repositories never commit independently."""
        with self.backend.transaction() as connection:
            yield connection
