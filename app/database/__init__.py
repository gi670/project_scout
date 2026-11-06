"""SQLite persistence layer for discovered repositories."""

from app.database.config import DEFAULT_DATABASE_PATH
from app.database.store import DatabaseError, RepositorySaveResult, RepositoryStore

__all__ = [
    "DEFAULT_DATABASE_PATH",
    "DatabaseError",
    "RepositorySaveResult",
    "RepositoryStore",
]
