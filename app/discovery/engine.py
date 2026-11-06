"""Filter, deduplicate, and rank repository results from the GitHub client."""

from __future__ import annotations

import logging
import math
from dataclasses import dataclass
from datetime import datetime, timezone
from typing import Iterable, TYPE_CHECKING

if TYPE_CHECKING:
    from app.github.client import Repository

logger = logging.getLogger(__name__)

# Scoring references keep the scale stable across separate discovery runs.
STAR_REFERENCE = 10_000
FORK_REFERENCE = 1_000
ACTIVITY_WINDOW_DAYS = 180
DESCRIPTION_REFERENCE_LENGTH = 120


@dataclass(frozen=True, slots=True)
class DiscoveryFilters:
    """Optional repository eligibility rules; topic matching accepts any target topic."""

    min_stars: int = 0
    language: str | None = None
    topics: tuple[str, ...] = ()
    max_age_days: int | None = None
    max_days_since_update: int | None = None

    def __post_init__(self) -> None:
        if self.min_stars < 0:
            raise ValueError("min_stars cannot be negative")
        if self.max_age_days is not None and self.max_age_days < 0:
            raise ValueError("max_age_days cannot be negative")
        if self.max_days_since_update is not None and self.max_days_since_update < 0:
            raise ValueError("max_days_since_update cannot be negative")


@dataclass(frozen=True, slots=True)
class ScoredRepository:
    """Repository and its overall score plus auditable weighted components."""

    repository: Repository
    score: float
    breakdown: dict[str, float]


class DiscoveryEngine:
    """Apply filters, deduplicate by GitHub ID, and rank repository results."""

    def __init__(self, filters: DiscoveryFilters | None = None) -> None:
        self.filters = filters or DiscoveryFilters()

    def discover(
        self,
        repositories: Iterable[Repository],
        *,
        top_n: int | None = None,
        now: datetime | None = None,
    ) -> list[ScoredRepository]:
        """Filter and rank repository objects returned by ``GitHubClient``.

        ``now`` is injectable to make date filtering and activity scores repeatable in
        tests. ``top_n=0`` returns an empty result; negative values are rejected.
        """
        if top_n is not None and top_n < 0:
            raise ValueError("top_n cannot be negative")
        reference_time = _as_utc(now or datetime.now(timezone.utc))

        unique: list[Repository] = []
        seen_ids: set[tuple[str, object]] = set()
        duplicates = 0
        for repository in repositories:
            repo_id = getattr(repository, "id", None)
            # API results always have an ID. The name fallback avoids collapsing
            # unrelated malformed records whose IDs are both absent.
            identity = ("id", repo_id) if repo_id is not None else (
                "name", str(getattr(repository, "full_name", "")).casefold()
            )
            if identity in seen_ids:
                duplicates += 1
                continue
            seen_ids.add(identity)
            unique.append(repository)

        ranked: list[ScoredRepository] = []
        for repository in unique:
            if self._matches(repository, reference_time):
                score, breakdown = score_repository(repository, self.filters.topics, reference_time)
                ranked.append(ScoredRepository(repository=repository, score=score, breakdown=breakdown))

        ranked.sort(key=lambda item: item.score, reverse=True)
        if top_n is not None:
            ranked = ranked[:top_n]
        logger.info(
            "Discovery processed %d repositories: %d duplicate(s), %d result(s).",
            len(unique) + duplicates,
            duplicates,
            len(ranked),
        )
        return ranked

    def _matches(self, repository: Repository, now: datetime) -> bool:
        if int(getattr(repository, "stars", 0) or 0) < self.filters.min_stars:
            return False

        required_language = (self.filters.language or "").strip().casefold()
        actual_language = (getattr(repository, "language", None) or "").strip().casefold()
        if required_language and actual_language != required_language:
            return False

        repository_topics = {
            str(topic).strip().casefold()
            for topic in (getattr(repository, "topics", None) or ())
            if str(topic).strip()
        }
        target_topics = {topic.strip().casefold() for topic in self.filters.topics if topic.strip()}
        if target_topics and not (repository_topics & target_topics):
            return False

        created_at = _read_date(getattr(repository, "created_at", None))
        if self.filters.max_age_days is not None:
            if created_at is None or _days_between(created_at, now) > self.filters.max_age_days:
                return False

        updated_at = _read_date(getattr(repository, "updated_at", None))
        if self.filters.max_days_since_update is not None:
            if updated_at is None or _days_between(updated_at, now) > self.filters.max_days_since_update:
                return False
        return True


def score_repository(
    repository: Repository,
    relevant_topics: tuple[str, ...] = (),
    now: datetime | None = None,
) -> tuple[float, dict[str, float]]:
    """Return a 0–100 score and weighted component values.

    Stars and forks use logarithmic normalization against fixed reference values;
    activity declines linearly to zero after 180 days. Topic relevance is the
    fraction of configured topics present, and description quality scales with
    non-whitespace length up to 120 characters. Component weights are 30/25/15/
    20/10 points, respectively. With no target topics the topic component is 0.
    """
    reference_time = _as_utc(now or datetime.now(timezone.utc))
    stars = max(0, int(getattr(repository, "stars", 0) or 0))
    forks = max(0, int(getattr(repository, "forks", 0) or 0))

    star_points = 30 * min(math.log1p(stars) / math.log1p(STAR_REFERENCE), 1.0)
    fork_points = 15 * min(math.log1p(forks) / math.log1p(FORK_REFERENCE), 1.0)

    updated_at = _read_date(getattr(repository, "updated_at", None))
    if updated_at is None:
        activity_points = 0.0
    else:
        days_old = max(0.0, _days_between(updated_at, reference_time))
        activity_points = 25 * max(0.0, 1 - days_old / ACTIVITY_WINDOW_DAYS)

    repo_topics = {
        str(topic).strip().casefold()
        for topic in (getattr(repository, "topics", None) or ())
        if str(topic).strip()
    }
    targets = {topic.strip().casefold() for topic in relevant_topics if topic.strip()}
    topic_points = 20 * len(repo_topics & targets) / len(targets) if targets else 0.0

    description = (getattr(repository, "description", None) or "").strip()
    description_points = 10 * min(len(description) / DESCRIPTION_REFERENCE_LENGTH, 1.0)

    breakdown = {
        "stars": round(star_points, 2),
        "recent_activity": round(activity_points, 2),
        "forks": round(fork_points, 2),
        "topic_relevance": round(topic_points, 2),
        "description_quality": round(description_points, 2),
    }
    score = round(min(100.0, max(0.0, sum(breakdown.values()))), 2)
    return score, breakdown


def _read_date(value: datetime | str | None) -> datetime | None:
    if value is None:
        return None
    if isinstance(value, str):
        try:
            value = datetime.fromisoformat(value.replace("Z", "+00:00"))
        except ValueError:
            return None
    if not isinstance(value, datetime):
        return None
    return _as_utc(value)


def _as_utc(value: datetime) -> datetime:
    if value.tzinfo is None:
        return value.replace(tzinfo=timezone.utc)
    return value.astimezone(timezone.utc)


def _days_between(then: datetime, now: datetime) -> float:
    return max(0.0, (now - then).total_seconds() / 86_400)
