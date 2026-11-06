"""SQLite storage for repositories and per-channel notification state."""

from __future__ import annotations

import json
import logging
import sqlite3
import uuid
from contextlib import closing, contextmanager
from dataclasses import dataclass
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Iterable, Iterator

from app.database.config import DEFAULT_DATABASE_PATH

logger = logging.getLogger(__name__)
NOTIFICATION_CLAIM_TIMEOUT_SECONDS = 300

_REPOSITORY_COLUMNS = (
    "name, full_name, description, url, stars, forks, language, topics_json, "
    "created_at, updated_at, discovery_score, last_discovered_at"
)
_UPSERT_REPOSITORY_SQL = """
INSERT INTO repositories (
    github_id, name, full_name, description, url, stars, forks, language, topics_json,
    created_at, updated_at, discovery_score, first_discovered_at, last_discovered_at
) VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
ON CONFLICT(github_id) DO UPDATE SET
    name=excluded.name,
    full_name=excluded.full_name,
    description=excluded.description,
    url=excluded.url,
    stars=excluded.stars,
    forks=excluded.forks,
    language=excluded.language,
    topics_json=excluded.topics_json,
    created_at=excluded.created_at,
    updated_at=excluded.updated_at,
    discovery_score=excluded.discovery_score,
    last_discovered_at=excluded.last_discovered_at
"""


@dataclass(frozen=True, slots=True)
class RepositorySaveResult:
    """Bulk-save count and GitHub IDs inserted during this operation."""

    saved_count: int
    inserted_ids: frozenset[int]


class DatabaseError(RuntimeError):
    """A local database initialization or operation failed."""


class RepositoryStore:
    """Thread-safe repository store backed by a local SQLite database file."""

    def __init__(
        self,
        database_path: str | Path = DEFAULT_DATABASE_PATH,
        *,
        timeout: float = 10.0,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        self.database_path = Path(database_path).expanduser().resolve()
        self._timeout = timeout
        self._closed = False
        self.database_path.parent.mkdir(parents=True, exist_ok=True)
        self._initialize()

    def _connect(self) -> sqlite3.Connection:
        if self._closed:
            raise DatabaseError("The SQLite repository store is closed.")
        connection = sqlite3.connect(self.database_path, timeout=self._timeout)
        connection.row_factory = sqlite3.Row
        connection.execute("PRAGMA foreign_keys = ON")
        connection.execute(f"PRAGMA busy_timeout = {int(self._timeout * 1000)}")
        return connection

    def _initialize(self) -> None:
        schema = """
        CREATE TABLE IF NOT EXISTS repositories (
            github_id INTEGER PRIMARY KEY,
            name TEXT NOT NULL,
            full_name TEXT NOT NULL,
            description TEXT,
            url TEXT NOT NULL,
            stars INTEGER NOT NULL DEFAULT 0 CHECK (stars >= 0),
            forks INTEGER NOT NULL DEFAULT 0 CHECK (forks >= 0),
            language TEXT,
            topics_json TEXT NOT NULL DEFAULT '[]',
            created_at TEXT,
            updated_at TEXT,
            discovery_score REAL CHECK (discovery_score IS NULL OR (discovery_score >= 0 AND discovery_score <= 100)),
            first_discovered_at TEXT NOT NULL,
            last_discovered_at TEXT NOT NULL
        );
        CREATE TABLE IF NOT EXISTS notification_state (
            github_id INTEGER NOT NULL,
            channel_key TEXT NOT NULL,
            status TEXT NOT NULL CHECK (status IN ('sending', 'sent', 'failed')),
            claim_id TEXT,
            claimed_at TEXT,
            sent_at TEXT,
            PRIMARY KEY (github_id, channel_key),
            FOREIGN KEY (github_id) REFERENCES repositories(github_id) ON DELETE CASCADE
        );
        CREATE INDEX IF NOT EXISTS idx_repositories_latest
            ON repositories(last_discovered_at DESC);
        CREATE INDEX IF NOT EXISTS idx_repositories_language_stars
            ON repositories(language, stars DESC);
        """
        connection: sqlite3.Connection | None = None
        try:
            connection = sqlite3.connect(self.database_path, timeout=self._timeout)
            connection.execute("PRAGMA foreign_keys = ON")
            connection.executescript(schema)
            connection.commit()
        except sqlite3.Error as exc:
            self._raise_database_error("initialization", exc)
        finally:
            if connection is not None:
                connection.close()
        logger.info("SQLite repository database initialized at %s.", self.database_path)

    @contextmanager
    def _transaction(self) -> Iterator[sqlite3.Connection]:
        connection = self._connect()
        try:
            connection.execute("BEGIN IMMEDIATE")
            yield connection
            connection.commit()
        except Exception:
            connection.rollback()
            raise
        finally:
            connection.close()

    def close(self) -> None:
        """Mark the store closed; each operation owns and closes its connection."""
        self._closed = True

    def insert_repository(self, repository: Any, score: float | None = None) -> bool:
        """Upsert one repository and return True if its GitHub ID was new."""
        document = _document_for(repository, score)
        try:
            with self._transaction() as connection:
                exists = connection.execute(
                    "SELECT 1 FROM repositories WHERE github_id = ?", (document["github_id"],)
                ).fetchone() is not None
                _upsert(connection, document)
            return not exists
        except sqlite3.Error as exc:
            self._raise_database_error("repository insert", exc)

    def update_repository(self, repository: Any, score: float | None = None) -> bool:
        """Update by GitHub ID, inserting the repository if it is not stored yet."""
        document = _document_for(repository, score)
        try:
            with self._transaction() as connection:
                _upsert(connection, document)
            return True
        except sqlite3.Error as exc:
            self._raise_database_error("repository update", exc)

    def save_repositories(self, repositories: Iterable[Any]) -> int:
        """Save a batch in one transaction and return the number of unique records."""
        return self.save_repositories_with_status(repositories).saved_count

    def save_repositories_with_status(self, repositories: Iterable[Any]) -> RepositorySaveResult:
        """Bulk upsert repositories and report IDs inserted during the transaction."""
        documents: dict[int, dict[str, Any]] = {}
        for repository in repositories:
            document = _document_for(repository, None)
            documents[document["github_id"]] = document
        if not documents:
            return RepositorySaveResult(saved_count=0, inserted_ids=frozenset())

        inserted_ids: set[int] = set()
        try:
            with self._transaction() as connection:
                for github_id, document in documents.items():
                    exists = connection.execute(
                        "SELECT 1 FROM repositories WHERE github_id = ?", (github_id,)
                    ).fetchone() is not None
                    _upsert(connection, document)
                    if not exists:
                        inserted_ids.add(github_id)
            return RepositorySaveResult(len(documents), frozenset(inserted_ids))
        except sqlite3.Error as exc:
            self._raise_database_error("bulk repository save", exc)

    def latest_repositories(self, limit: int = 20) -> list[dict[str, Any]]:
        """Return the most recently discovered repositories, newest first."""
        if limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0:
            return []
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(
                    f"SELECT github_id, {_REPOSITORY_COLUMNS}, first_discovered_at "
                    "FROM repositories ORDER BY last_discovered_at DESC LIMIT ?",
                    (limit,),
                ).fetchall()
            return [_row_to_repository(row) for row in rows]
        except sqlite3.Error as exc:
            self._raise_database_error("latest repository query", exc)

    def search_repositories(
        self, *, language: str | None = None, min_stars: int = 0, limit: int = 100
    ) -> list[dict[str, Any]]:
        """Search stored repositories by exact language and minimum star count."""
        if min_stars < 0:
            raise ValueError("min_stars cannot be negative")
        if limit < 0:
            raise ValueError("limit cannot be negative")
        if limit == 0:
            return []

        query = "SELECT github_id, " + _REPOSITORY_COLUMNS + ", first_discovered_at FROM repositories WHERE stars >= ?"
        params: list[Any] = [min_stars]
        if language and language.strip():
            query += " AND language = ?"
            params.append(language.strip())
        query += " ORDER BY last_discovered_at DESC LIMIT ?"
        params.append(limit)
        try:
            with closing(self._connect()) as connection:
                rows = connection.execute(query, params).fetchall()
            return [_row_to_repository(row) for row in rows]
        except sqlite3.Error as exc:
            self._raise_database_error("repository search", exc)

    def count_repositories(self) -> int:
        """Return the total number of stored repositories."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute("SELECT COUNT(*) AS count FROM repositories").fetchone()
            return int(row["count"])
        except sqlite3.Error as exc:
            self._raise_database_error("repository count", exc)

    def claim_notification(self, github_id: int, channel_key: str) -> str | None:
        """Atomically claim a notification unless it was sent or is already claimed."""
        now = _datetime_to_text(datetime.now(timezone.utc))
        stale_before = _datetime_to_text(
            datetime.now(timezone.utc) - timedelta(seconds=NOTIFICATION_CLAIM_TIMEOUT_SECONDS)
        )
        claim_id = uuid.uuid4().hex
        try:
            with self._transaction() as connection:
                if connection.execute(
                    "SELECT 1 FROM repositories WHERE github_id = ?", (int(github_id),)
                ).fetchone() is None:
                    return None
                state = connection.execute(
                    "SELECT status, claimed_at FROM notification_state WHERE github_id = ? AND channel_key = ?",
                    (int(github_id), channel_key),
                ).fetchone()
                if state is not None:
                    if state["status"] == "sent":
                        return None
                    if state["status"] == "sending" and state["claimed_at"] and state["claimed_at"] >= stale_before:
                        return None
                connection.execute(
                    """INSERT INTO notification_state
                       (github_id, channel_key, status, claim_id, claimed_at, sent_at)
                       VALUES (?, ?, 'sending', ?, ?, NULL)
                       ON CONFLICT(github_id, channel_key) DO UPDATE SET
                           status='sending', claim_id=excluded.claim_id,
                           claimed_at=excluded.claimed_at, sent_at=NULL""",
                    (int(github_id), channel_key, claim_id, now),
                )
            return claim_id
        except sqlite3.Error as exc:
            self._raise_database_error("notification claim", exc)

    def mark_notification_sent(self, github_id: int, channel_key: str, claim_id: str) -> bool:
        """Mark a notification sent only if the caller still owns its claim."""
        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    """UPDATE notification_state
                       SET status='sent', claim_id=NULL, sent_at=?
                       WHERE github_id=? AND channel_key=? AND claim_id=? AND status='sending'""",
                    (_datetime_to_text(datetime.now(timezone.utc)), int(github_id), channel_key, claim_id),
                )
            return cursor.rowcount == 1
        except sqlite3.Error as exc:
            self._raise_database_error("notification completion", exc)

    def release_notification_claim(self, github_id: int, channel_key: str, claim_id: str) -> bool:
        """Mark a failed send retryable if the claim is still owned by the caller."""
        try:
            with self._transaction() as connection:
                cursor = connection.execute(
                    """UPDATE notification_state
                       SET status='failed', claim_id=NULL, claimed_at=NULL
                       WHERE github_id=? AND channel_key=? AND claim_id=? AND status='sending'""",
                    (int(github_id), channel_key, claim_id),
                )
            return cursor.rowcount == 1
        except sqlite3.Error as exc:
            self._raise_database_error("notification claim release", exc)

    def notification_status(self, github_id: int, channel_key: str) -> str | None:
        """Return the persisted status for one repository/channel pair."""
        try:
            with closing(self._connect()) as connection:
                row = connection.execute(
                    "SELECT status FROM notification_state WHERE github_id=? AND channel_key=?",
                    (int(github_id), channel_key),
                ).fetchone()
            return str(row["status"]) if row is not None else None
        except sqlite3.Error as exc:
            self._raise_database_error("notification status query", exc)

    def _raise_database_error(self, operation: str, exc: sqlite3.Error) -> None:
        logger.error("SQLite %s failed (%s).", operation, type(exc).__name__)
        raise DatabaseError(f"SQLite {operation} failed.") from None


def _document_for(item: Any, score: float | None) -> dict[str, Any]:
    """Convert a Repository or ScoredRepository into SQLite-bound values."""
    scored = item
    repository = getattr(item, "repository", item)
    if score is None and hasattr(scored, "score"):
        score = float(scored.score)
    github_id = getattr(repository, "id", None)
    if github_id is None:
        raise ValueError("Repository must include a GitHub repository ID.")
    description = getattr(repository, "description", None)
    language = getattr(repository, "language", None)
    discovered_at = _datetime_to_text(datetime.now(timezone.utc))
    return {
        "github_id": int(github_id),
        "name": str(getattr(repository, "name", "")),
        "full_name": str(getattr(repository, "full_name", "")),
        "description": description,
        "url": str(getattr(repository, "url", "")),
        "stars": max(0, int(getattr(repository, "stars", 0) or 0)),
        "forks": max(0, int(getattr(repository, "forks", 0) or 0)),
        "language": language,
        "topics_json": json.dumps(list(getattr(repository, "topics", None) or ())),
        "created_at": _datetime_to_text(getattr(repository, "created_at", None)),
        "updated_at": _datetime_to_text(getattr(repository, "updated_at", None)),
        "discovery_score": score,
        "first_discovered_at": discovered_at,
        "last_discovered_at": discovered_at,
    }


def _upsert(connection: sqlite3.Connection, document: dict[str, Any]) -> None:
    connection.execute(
        _UPSERT_REPOSITORY_SQL,
        (
            document["github_id"],
            document["name"],
            document["full_name"],
            document["description"],
            document["url"],
            document["stars"],
            document["forks"],
            document["language"],
            document["topics_json"],
            _datetime_to_text(document["created_at"]),
            _datetime_to_text(document["updated_at"]),
            document["discovery_score"],
            _datetime_to_text(document["first_discovered_at"]),
            _datetime_to_text(document["last_discovered_at"]),
        ),
    )


def _datetime_to_text(value: datetime | str | None) -> str | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return value
    if value.tzinfo is None:
        value = value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc).isoformat(timespec="microseconds")


def _parse_datetime(value: str | None) -> datetime | None:
    if value is None:
        return None
    parsed = datetime.fromisoformat(value)
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _row_to_repository(row: sqlite3.Row) -> dict[str, Any]:
    result = dict(row)
    result["topics"] = json.loads(result.pop("topics_json"))
    result["created_at"] = _parse_datetime(result["created_at"])
    result["updated_at"] = _parse_datetime(result["updated_at"])
    result["first_discovered_at"] = _parse_datetime(result["first_discovered_at"])
    result["last_discovered_at"] = _parse_datetime(result["last_discovered_at"])
    return result
