"""Offline unit tests for repository filtering, deduplication, and ranking."""

from datetime import datetime, timedelta, timezone
from types import SimpleNamespace
import unittest

from app.discovery import DiscoveryEngine, DiscoveryFilters
from app.discovery.engine import score_repository


NOW = datetime(2026, 11, 6, tzinfo=timezone.utc)


def repository(
    repo_id: int | None,
    *,
    full_name: str | None = None,
    stars: int = 500,
    forks: int = 50,
    language: str | None = "Python",
    topics: tuple[str, ...] | None = ("machine-learning",),
    description: str | None = "An example repository description.",
    created_at: datetime | None = NOW - timedelta(days=100),
    updated_at: datetime | None = NOW - timedelta(days=2),
) -> SimpleNamespace:
    return SimpleNamespace(
        id=repo_id,
        name=(full_name or "owner/project").split("/")[-1],
        full_name=full_name or "owner/project",
        description=description,
        url=f"https://github.com/{full_name or 'owner/project'}",
        stars=stars,
        forks=forks,
        language=language,
        topics=topics,
        created_at=created_at,
        updated_at=updated_at,
    )


class DiscoveryEngineTests(unittest.TestCase):
    def test_filters_and_deduplicates_by_repository_id(self) -> None:
        first = repository(10, full_name="owner/first")
        duplicate = repository(10, full_name="renamed/duplicate", stars=10_000)
        low_stars = repository(11, full_name="owner/low", stars=20)
        wrong_language = repository(12, full_name="owner/js", language="JavaScript")
        wrong_topic = repository(13, full_name="owner/other", topics=("web",))

        engine = DiscoveryEngine(
            DiscoveryFilters(min_stars=100, language="python", topics=("machine-learning",))
        )
        results = engine.discover(
            [first, duplicate, low_stars, wrong_language, wrong_topic],
            now=NOW,
        )

        self.assertEqual([item.repository.id for item in results], [10])
        self.assertEqual(results[0].repository.full_name, "owner/first")

    def test_age_and_update_filters_exclude_missing_dates(self) -> None:
        recent = repository(1, created_at=NOW - timedelta(days=30), updated_at=NOW - timedelta(days=5))
        old = repository(2, created_at=NOW - timedelta(days=400))
        missing_created = repository(3, created_at=None)
        stale = repository(4, updated_at=NOW - timedelta(days=45))
        missing_updated = repository(5, updated_at=None)

        engine = DiscoveryEngine(
            DiscoveryFilters(max_age_days=90, max_days_since_update=30)
        )
        results = engine.discover(
            [recent, old, missing_created, stale, missing_updated],
            now=NOW,
        )

        self.assertEqual([item.repository.id for item in results], [1])

    def test_top_n_and_descending_order(self) -> None:
        modest = repository(1, full_name="owner/modest", stars=100, forks=5)
        popular = repository(2, full_name="owner/popular", stars=10_000, forks=1_000)
        results = DiscoveryEngine().discover([modest, popular], top_n=1, now=NOW)
        self.assertEqual(len(results), 1)
        self.assertEqual(results[0].repository.id, 2)

    def test_score_is_bounded_and_breakdown_is_transparent(self) -> None:
        complete = repository(1, stars=100_000, forks=10_000, topics=("python", "ai"))
        score, breakdown = score_repository(complete, ("python", "ai"), NOW)
        self.assertGreaterEqual(score, 0)
        self.assertLessEqual(score, 100)
        self.assertEqual(set(breakdown), {
            "stars", "recent_activity", "forks", "topic_relevance", "description_quality"
        })
        self.assertEqual(breakdown["topic_relevance"], 20.0)

    def test_missing_metadata_is_safe_and_scores_zero_for_missing_parts(self) -> None:
        sparse = repository(
            None,
            language=None,
            topics=None,
            description=None,
            created_at=None,
            updated_at=None,
            stars=0,
            forks=0,
        )
        score, breakdown = score_repository(sparse, ("python",), NOW)
        self.assertEqual(score, 0.0)
        self.assertEqual(breakdown["recent_activity"], 0.0)
        self.assertEqual(breakdown["topic_relevance"], 0.0)
        results = DiscoveryEngine().discover([sparse], now=NOW)
        self.assertEqual(len(results), 1)

    def test_zero_top_n_and_negative_top_n(self) -> None:
        sample = repository(1)
        self.assertEqual(DiscoveryEngine().discover([sample], top_n=0, now=NOW), [])
        with self.assertRaises(ValueError):
            DiscoveryEngine().discover([sample], top_n=-1, now=NOW)


if __name__ == "__main__":
    unittest.main()
