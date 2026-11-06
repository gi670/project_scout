"""Mocked tests for the discovery-to-database-to-Discord scan flow."""

import asyncio
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.discovery.scan_service import DiscoveryScanService, ScanAlreadyRunning


def scored_repository(repo_id=42, *, topics=("machine-learning", "cybersecurity"), score=90.0):
    repository = SimpleNamespace(id=repo_id, topics=topics, full_name="sample/project")
    return SimpleNamespace(repository=repository, score=score)


class DiscoveryScanServiceTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.github = SimpleNamespace(search_repositories=AsyncMock(return_value=[object()]))
        self.scored = scored_repository()
        self.engine = SimpleNamespace(discover=Mock(return_value=[self.scored]))
        self.store = SimpleNamespace(
            save_repositories_with_status=Mock(return_value=SimpleNamespace(saved_count=1, inserted_ids=frozenset({42}))),
            claim_notification=Mock(return_value="claim"),
            mark_notification_sent=Mock(return_value=True),
            release_notification_claim=Mock(return_value=True),
        )
        self.notifications = SimpleNamespace(send_repository=AsyncMock())
        self.service = DiscoveryScanService(
            self.github,
            self.engine,
            self.store,
            self.notifications,
            language="Python",
            min_stars=100,
            topics=("machine-learning",),
            result_limit=12,
            trending_score_threshold=70,
        )

    async def test_scan_routes_new_trending_and_topic_repositories(self) -> None:
        result = await self.service.scan()
        self.assertEqual(result.searched_count, 1)
        self.assertEqual(result.saved_count, 1)
        self.assertEqual(result.notification_count, 4)
        self.assertEqual(result.new_count, 1)
        self.assertEqual(result.updated_count, 0)
        self.assertGreaterEqual(result.duration_seconds, 0)
        self.assertEqual(
            [call.args[1] for call in self.notifications.send_repository.await_args_list],
            ["new_repositories", "trending_projects", "ai_ml_projects", "cybersecurity_projects"],
        )
        self.github.search_repositories.assert_awaited_once_with(
            language="Python", min_stars=100, topics=("machine-learning",), per_page=12
        )

    async def test_successful_notification_claims_prevent_repeat_announcements(self) -> None:
        result = SimpleNamespace(saved_count=1, inserted_ids=frozenset({42}))
        self.store.save_repositories_with_status.side_effect = [
            result,
            SimpleNamespace(saved_count=1, inserted_ids=frozenset()),
        ]
        claimed = set()

        def claim_once(repo_id, channel_key):
            key = (repo_id, channel_key)
            if key in claimed:
                return None
            claimed.add(key)
            return "claim"

        self.store.claim_notification.side_effect = claim_once
        await self.service.scan()
        self.notifications.send_repository.reset_mock()
        second_result = await self.service.scan()
        self.assertEqual(second_result.notification_count, 0)
        self.notifications.send_repository.assert_not_awaited()

    async def test_concurrent_scan_is_rejected(self) -> None:
        started = asyncio.Event()
        finish = asyncio.Event()

        async def blocked_search(**kwargs):
            started.set()
            await finish.wait()
            return []

        self.github.search_repositories.side_effect = blocked_search
        first_scan = asyncio.create_task(self.service.scan())
        await started.wait()
        with self.assertRaises(ScanAlreadyRunning):
            await self.service.scan()
        finish.set()
        await first_scan

    async def test_notification_failure_releases_claim_for_retry(self) -> None:
        self.notifications.send_repository.side_effect = RuntimeError("private failure details")
        result = await self.service.scan()
        self.assertEqual(result.notification_count, 0)
        self.assertEqual(self.store.release_notification_claim.call_count, 4)
        self.store.mark_notification_sent.assert_not_called()


if __name__ == "__main__":
    unittest.main()
