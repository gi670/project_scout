"""Coordinate GitHub search, repository ranking, storage, and notifications."""

from __future__ import annotations

import asyncio
import logging
import time
from dataclasses import dataclass
from typing import Any

logger = logging.getLogger(__name__)

AI_ML_TOPICS = {
    "ai", "artificial-intelligence", "machine-learning", "deep-learning", "llm", "nlp", "computer-vision"
}
CYBERSECURITY_TOPICS = {
    "security", "cybersecurity", "infosec", "penetration-testing", "vulnerability", "malware"
}


class ScanAlreadyRunning(RuntimeError):
    """Raised when another scan is already executing in this bot process."""


class ScanServiceStopping(RuntimeError):
    """Raised when the application is shutting down and no new scan may start."""


class DiscoveryScanError(RuntimeError):
    """A scan failed; details are intentionally omitted from its public message."""


@dataclass(frozen=True, slots=True)
class ScanResult:
    searched_count: int
    saved_count: int
    notification_count: int
    new_count: int
    updated_count: int
    duration_seconds: float


class DiscoveryScanService:
    """Run one guarded discovery pipeline using already-created application services."""

    def __init__(
        self,
        github_client: Any,
        discovery_engine: Any,
        repository_store: Any,
        notification_service: Any,
        *,
        language: str | None = None,
        min_stars: int = 100,
        topics: tuple[str, ...] = (),
        result_limit: int = 30,
        trending_score_threshold: float = 70.0,
    ) -> None:
        self._github = github_client
        self._engine = discovery_engine
        self._store = repository_store
        self._notifications = notification_service
        self._language = language
        self._min_stars = min_stars
        self._topics = topics
        self._result_limit = result_limit
        self._trending_score_threshold = trending_score_threshold
        self._scan_lock = asyncio.Lock()
        self._closing = False

    def stop_accepting_scans(self) -> None:
        """Prevent scheduled or manual scans from starting during shutdown."""
        self._closing = True

    async def wait_until_idle(self) -> None:
        """Wait for the current scan, if any, to finish."""
        await self._scan_lock.acquire()
        self._scan_lock.release()

    async def scan(self) -> ScanResult:
        """Search, rank, persist and notify, rejecting overlapping scans."""
        if self._closing:
            raise ScanServiceStopping("Discovery scans are stopping.")
        if self._scan_lock.locked():
            raise ScanAlreadyRunning("A discovery scan is already in progress.")
        await self._scan_lock.acquire()
        started_at = time.monotonic()
        try:
            if self._closing:
                raise ScanServiceStopping("Discovery scans are stopping.")
            logger.info("Discovery scan started.")
            raw_repositories = await self._github.search_repositories(
                language=self._language,
                min_stars=self._min_stars,
                topics=self._topics,
                per_page=self._result_limit,
            )
            scored_repositories = self._engine.discover(raw_repositories, top_n=self._result_limit)
            save_result = await asyncio.to_thread(
                self._store.save_repositories_with_status, scored_repositories
            )
            notifications_sent = 0
            for scored in scored_repositories:
                repository_id = getattr(scored.repository, "id", None)
                if repository_id is None:
                    logger.warning("Skipping notification for a repository without a GitHub ID.")
                    continue
                for channel_key in self._destinations(scored, repository_id in save_result.inserted_ids):
                    claim_id = await asyncio.to_thread(
                        self._store.claim_notification, repository_id, channel_key
                    )
                    if claim_id is None:
                        continue
                    try:
                        await self._notifications.send_repository(scored, channel_key)
                    except Exception as exc:
                        logger.error(
                            "Repository notification failed for category %s (%s).",
                            channel_key,
                            type(exc).__name__,
                        )
                        await asyncio.to_thread(
                            self._store.release_notification_claim,
                            repository_id,
                            channel_key,
                            claim_id,
                        )
                        continue
                    marked = await asyncio.to_thread(
                        self._store.mark_notification_sent, repository_id, channel_key, claim_id
                    )
                    if marked:
                        notifications_sent += 1
                    else:
                        logger.warning("Notification claim changed before completion for %s.", channel_key)

            new_count = len(save_result.inserted_ids)
            updated_count = max(0, save_result.saved_count - new_count)
            duration_seconds = time.monotonic() - started_at
            logger.info(
                "Discovery scan completed: fetched=%d new=%d updated=%d notifications=%d duration=%.2fs.",
                len(raw_repositories), new_count, updated_count, notifications_sent, duration_seconds,
            )
            return ScanResult(
                searched_count=len(raw_repositories),
                saved_count=save_result.saved_count,
                notification_count=notifications_sent,
                new_count=new_count,
                updated_count=updated_count,
                duration_seconds=duration_seconds,
            )
        except (ScanAlreadyRunning, ScanServiceStopping, DiscoveryScanError):
            raise
        except Exception as exc:
            if getattr(exc, "rate_limited", False):
                logger.error("Discovery scan deferred after a GitHub API rate limit.")
            else:
                logger.error(
                    "Discovery scan failed after %.2fs (%s).",
                    time.monotonic() - started_at,
                    type(exc).__name__,
                )
            raise DiscoveryScanError("The discovery scan failed. Check application logs.") from None
        finally:
            self._scan_lock.release()

    def _destinations(self, scored: Any, is_new: bool) -> tuple[str, ...]:
        repository = scored.repository
        topic_names = {
            str(topic).casefold() for topic in (getattr(repository, "topics", None) or ())
        }
        destinations: list[str] = []
        if is_new:
            destinations.append("new_repositories")
        if scored.score >= self._trending_score_threshold:
            destinations.append("trending_projects")
        if topic_names & AI_ML_TOPICS:
            destinations.append("ai_ml_projects")
        if topic_names & CYBERSECURITY_TOPICS:
            destinations.append("cybersecurity_projects")
        return tuple(destinations)
