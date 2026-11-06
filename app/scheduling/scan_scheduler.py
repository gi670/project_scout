"""APScheduler wrapper for periodic, lifecycle-safe discovery scans."""

from __future__ import annotations

import logging
from typing import Any

from app.discovery.scan_service import ScanAlreadyRunning, ScanServiceStopping

logger = logging.getLogger(__name__)


class ScanScheduler:
    """Schedule the existing scan service without constructing duplicate clients."""

    def __init__(
        self,
        scan_service: Any,
        *,
        interval_minutes: int = 30,
        enabled: bool = True,
        scheduler: Any | None = None,
    ) -> None:
        if interval_minutes < 1:
            raise ValueError("interval_minutes must be positive")
        self._scan_service = scan_service
        self._interval_minutes = interval_minutes
        self._enabled = enabled
        self._started = False
        self._scheduler = scheduler

    @property
    def started(self) -> bool:
        return self._started

    def start(self) -> bool:
        """Start the interval job; safe to call again after Discord reconnects."""
        if not self._enabled:
            logger.info("Automatic repository scanning is disabled by configuration.")
            return False
        if self._started:
            return False
        if self._scheduler is None:
            try:
                from apscheduler.schedulers.asyncio import AsyncIOScheduler
            except ImportError:
                raise RuntimeError("APScheduler is required; install the project requirements.") from None
            # Construct inside on_ready, where the bot's active event loop exists.
            self._scheduler = AsyncIOScheduler()
        self._scheduler.add_job(
            self._run_scan,
            trigger="interval",
            minutes=self._interval_minutes,
            id="github-scout-repository-scan",
            replace_existing=True,
            max_instances=1,
            coalesce=True,
            misfire_grace_time=300,
        )
        self._scheduler.start()
        self._started = True
        logger.info("Automatic repository scanning started with a %d-minute interval.", self._interval_minutes)
        return True

    async def _run_scan(self) -> None:
        """Run one scheduled scan and contain failures so future runs remain scheduled."""
        try:
            await self._scan_service.scan()
        except ScanAlreadyRunning:
            logger.info("Scheduled repository scan skipped because another scan is running.")
        except ScanServiceStopping:
            logger.info("Scheduled repository scan stopped because the application is shutting down.")
        except Exception as exc:
            logger.error("Scheduled repository scan failed (%s).", type(exc).__name__)

    async def stop(self) -> None:
        """Stop new scans, pause scheduling, and wait for an active scan to finish."""
        self._scan_service.stop_accepting_scans()
        if self._started and self._scheduler is not None:
            self._scheduler.pause()
        await self._scan_service.wait_until_idle()
        if self._started and self._scheduler is not None:
            self._scheduler.shutdown(wait=False)
            self._started = False
            logger.info("Automatic repository scanning stopped.")
