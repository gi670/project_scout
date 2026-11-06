"""Unit tests for scheduler configuration and bot-ready lifecycle hooks."""

import unittest
from unittest.mock import AsyncMock, Mock

from app.config import Settings
from app.notifications.discord_bot import create_bot
from app.scheduling import ScanScheduler


class ScanSchedulerTests(unittest.IsolatedAsyncioTestCase):
    def setUp(self) -> None:
        self.scan_service = Mock()
        self.scan_service.scan = AsyncMock()
        self.scan_service.wait_until_idle = AsyncMock()
        self.scan_service.stop_accepting_scans = Mock()
        self.scheduler_backend = Mock()
        self.scheduler = ScanScheduler(
            self.scan_service,
            interval_minutes=30,
            scheduler=self.scheduler_backend,
        )

    def test_start_adds_single_interval_job_with_overlap_guards(self) -> None:
        self.assertTrue(self.scheduler.start())
        self.assertFalse(self.scheduler.start())
        self.scheduler_backend.add_job.assert_called_once()
        args, kwargs = self.scheduler_backend.add_job.call_args
        self.assertEqual(kwargs["trigger"], "interval")
        self.assertEqual(kwargs["minutes"], 30)
        self.assertEqual(kwargs["max_instances"], 1)
        self.assertTrue(kwargs["coalesce"])
        self.scheduler_backend.start.assert_called_once()

    async def test_scheduled_job_reuses_scan_service_and_contains_errors(self) -> None:
        await self.scheduler._run_scan()
        self.scan_service.scan.assert_awaited_once()
        self.scan_service.scan.side_effect = RuntimeError("sensitive details")
        await self.scheduler._run_scan()
        self.assertEqual(self.scan_service.scan.await_count, 2)

    async def test_stop_pauses_scheduler_and_waits_for_active_scan(self) -> None:
        self.scheduler.start()
        await self.scheduler.stop()
        self.scan_service.stop_accepting_scans.assert_called_once()
        self.scheduler_backend.pause.assert_called_once()
        self.scan_service.wait_until_idle.assert_awaited_once()
        self.scheduler_backend.shutdown.assert_called_once_with(wait=False)
        self.assertFalse(self.scheduler.started)

    async def test_bot_starts_scheduler_only_from_ready_and_stops_on_close(self) -> None:
        bot = create_bot(Settings(discord_token="not-a-real-token", dev_guild_id=None, log_level="INFO"))
        lifecycle = Mock()
        lifecycle.start = Mock()
        lifecycle.stop = AsyncMock()
        bot.scan_scheduler = lifecycle
        self.assertEqual(
            {command.name for command in bot.tree.get_commands()},
            {"ping", "status", "latest", "search", "stats", "rescan"},
        )
        lifecycle.start.assert_not_called()
        await bot.on_ready()
        lifecycle.start.assert_called_once()
        await bot.close()
        lifecycle.stop.assert_awaited_once()

    async def test_disabled_scheduler_does_not_start_backend(self) -> None:
        scheduler = ScanScheduler(self.scan_service, enabled=False, scheduler=self.scheduler_backend)
        self.assertFalse(scheduler.start())
        self.scheduler_backend.start.assert_not_called()
        await scheduler.stop()
        self.scan_service.stop_accepting_scans.assert_called_once()


if __name__ == "__main__":
    unittest.main()
