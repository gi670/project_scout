"""SQLite database tests using isolated temporary database files."""

from datetime import datetime, timezone
from pathlib import Path
import sqlite3
import tempfile
from types import SimpleNamespace
import unittest

from app.database import DEFAULT_DATABASE_PATH, DatabaseError, RepositoryStore


def sample_repository(repo_id: int = 123, *, name: str = "scout", language: str = "Python", stars: int = 250):
    return SimpleNamespace(
        id=repo_id,
        name=name,
        full_name=f"sample/{name}",
        description="A sample GitHub project",
        url=f"https://github.com/sample/{name}",
        stars=stars,
        forks=12,
        language=language,
        topics=("discovery", "python"),
        created_at=datetime(2025, 1, 1, tzinfo=timezone.utc),
        updated_at=datetime(2026, 1, 1, tzinfo=timezone.utc),
    )


class RepositoryStoreTests(unittest.TestCase):
    def setUp(self) -> None:
        self.temp_dir = tempfile.TemporaryDirectory()
        self.db_path = Path(self.temp_dir.name) / "test_scout.db"
        self.store = RepositoryStore(self.db_path)

    def tearDown(self) -> None:
        self.store.close()
        self.temp_dir.cleanup()

    def test_initialization_creates_tables_indexes_and_foreign_key(self) -> None:
        self.assertTrue(self.db_path.is_file())
        with sqlite3.connect(self.db_path) as connection:
            tables = {
                row[0]
                for row in connection.execute("SELECT name FROM sqlite_master WHERE type='table'")
            }
            repository_indexes = {
                row[1]
                for row in connection.execute("PRAGMA index_list(repositories)")
            }
            notification_foreign_keys = connection.execute(
                "PRAGMA foreign_key_list(notification_state)"
            ).fetchall()
        self.assertIn("repositories", tables)
        self.assertIn("notification_state", tables)
        self.assertIn("idx_repositories_latest", repository_indexes)
        self.assertIn("idx_repositories_language_stars", repository_indexes)
        self.assertEqual(notification_foreign_keys[0][2], "repositories")

    def test_insert_and_duplicate_prevention_by_github_id(self) -> None:
        repo = sample_repository()
        self.assertTrue(self.store.insert_repository(repo, score=81.5))
        first = self.store.latest_repositories()[0]
        self.assertFalse(self.store.insert_repository(repo, score=82.0))
        self.assertEqual(self.store.count_repositories(), 1)

        updated = self.store.latest_repositories()[0]
        self.assertEqual(updated["github_id"], 123)
        self.assertEqual(updated["discovery_score"], 82.0)
        self.assertEqual(updated["first_discovered_at"], first["first_discovered_at"])
        self.assertEqual(updated["topics"], ["discovery", "python"])

    def test_update_upserts_missing_repository_and_updates_existing(self) -> None:
        self.assertTrue(self.store.update_repository(sample_repository(7), score=40))
        self.assertTrue(self.store.update_repository(sample_repository(7, stars=500), score=50))
        record = self.store.latest_repositories()[0]
        self.assertEqual(record["stars"], 500)
        self.assertEqual(record["discovery_score"], 50)
        self.assertEqual(self.store.count_repositories(), 1)

    def test_bulk_save_is_transactional_and_reports_new_ids(self) -> None:
        results = [
            SimpleNamespace(repository=sample_repository(1, name="first"), score=70.0),
            SimpleNamespace(repository=sample_repository(2, name="second"), score=75.0),
            SimpleNamespace(repository=sample_repository(1, name="first-updated", stars=900), score=80.0),
        ]
        saved = self.store.save_repositories_with_status(results)
        self.assertEqual(saved.saved_count, 2)
        self.assertEqual(saved.inserted_ids, frozenset({1, 2}))
        self.assertEqual(self.store.count_repositories(), 2)
        self.assertEqual(self.store.search_repositories(min_stars=800)[0]["name"], "first-updated")
        self.assertEqual(self.store.save_repositories([]), 0)

        with self.assertRaisesRegex(ValueError, "GitHub repository ID"):
            self.store.save_repositories([sample_repository(3), SimpleNamespace(id=None)])
        self.assertEqual(self.store.count_repositories(), 2)

    def test_latest_retrieval_and_search_filters(self) -> None:
        self.store.insert_repository(sample_repository(1, name="python-low", stars=100))
        self.store.insert_repository(sample_repository(2, name="python-high", stars=500))
        self.store.insert_repository(sample_repository(3, name="rust-high", language="Rust", stars=800))

        latest = self.store.latest_repositories(limit=2)
        self.assertEqual(len(latest), 2)
        matches = self.store.search_repositories(language="Python", min_stars=200)
        self.assertEqual([row["github_id"] for row in matches], [2])
        self.assertEqual(len(self.store.search_repositories(min_stars=700)), 1)
        self.assertEqual(self.store.latest_repositories(limit=0), [])

    def test_search_parameters_are_bound_and_not_executed_as_sql(self) -> None:
        self.store.insert_repository(sample_repository())
        malicious_language = "Python' OR 1=1 --"
        self.assertEqual(self.store.search_repositories(language=malicious_language), [])
        self.assertEqual(self.store.count_repositories(), 1)

    def test_notification_claim_sent_and_retry_state(self) -> None:
        self.store.insert_repository(sample_repository())
        claim = self.store.claim_notification(123, "new_repositories")
        self.assertIsNotNone(claim)
        self.assertEqual(self.store.notification_status(123, "new_repositories"), "sending")
        self.assertIsNone(self.store.claim_notification(123, "new_repositories"))
        self.assertTrue(self.store.release_notification_claim(123, "new_repositories", claim))
        self.assertEqual(self.store.notification_status(123, "new_repositories"), "failed")

        retry_claim = self.store.claim_notification(123, "new_repositories")
        self.assertIsNotNone(retry_claim)
        self.assertTrue(self.store.mark_notification_sent(123, "new_repositories", retry_claim))
        self.assertEqual(self.store.notification_status(123, "new_repositories"), "sent")
        self.assertIsNone(self.store.claim_notification(123, "new_repositories"))
        self.assertFalse(self.store.mark_notification_sent(123, "new_repositories", claim))

    def test_notification_state_requires_existing_repository_and_cascades(self) -> None:
        self.assertIsNone(self.store.claim_notification(404, "trending_projects"))
        self.store.insert_repository(sample_repository())
        claim = self.store.claim_notification(123, "trending_projects")
        self.assertIsNotNone(claim)
        with sqlite3.connect(self.db_path) as connection:
            connection.execute("PRAGMA foreign_keys = ON")
            connection.execute("DELETE FROM repositories WHERE github_id = ?", (123,))
            state_count = connection.execute("SELECT COUNT(*) FROM notification_state").fetchone()[0]
        self.assertEqual(state_count, 0)

    def test_invalid_limits_and_closed_store_raise(self) -> None:
        with self.assertRaises(ValueError):
            self.store.latest_repositories(-1)
        with self.assertRaises(ValueError):
            self.store.search_repositories(min_stars=-1)
        self.store.close()
        with self.assertRaises(DatabaseError):
            self.store.count_repositories()

    def test_database_default_path_is_project_data_location(self) -> None:
        self.assertEqual(DEFAULT_DATABASE_PATH.parts[-2:], ("data", "github_scout.db"))


if __name__ == "__main__":
    unittest.main()
