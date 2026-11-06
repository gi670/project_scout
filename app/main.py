"""Application entry point for the GitHub Scout Discord bot."""

from __future__ import annotations

import asyncio
import logging
import sys

from app.config import Settings
from app.database import RepositoryStore
from app.discovery import DiscoveryEngine, DiscoveryFilters
from app.discovery.scan_service import DiscoveryScanService
from app.github import GitHubClient
from app.notifications.discord_bot import create_bot
from app.notifications.service import DiscordNotificationService
from app.scheduling import ScanScheduler


def main() -> int:
    """Configure logging, load settings, and run the Discord client."""
    try:
        settings = Settings.from_env()
    except ValueError as exc:
        logging.basicConfig(level=logging.ERROR, format="%(asctime)s %(levelname)s %(name)s: %(message)s")
        logging.getLogger(__name__).error("Invalid configuration: %s", exc)
        return 2

    logging.basicConfig(
        level=getattr(logging, settings.log_level),
        format="%(asctime)s %(levelname)s %(name)s: %(message)s",
    )
    logger = logging.getLogger(__name__)
    github_client = None
    repository_store = None
    try:
        repository_store = RepositoryStore()
        github_client = GitHubClient()
        discovery_filters = DiscoveryFilters(
            min_stars=settings.discovery_min_stars,
            language=settings.discovery_language,
            topics=settings.discovery_topics,
        )
        bot = create_bot(settings, repository_store=repository_store)
        notifications = DiscordNotificationService(
            bot,
            channel_ids=settings.channel_ids,
            dev_guild_id=settings.dev_guild_id,
        )
        bot.discovery_scan_service = DiscoveryScanService(
            github_client,
            DiscoveryEngine(discovery_filters),
            repository_store,
            notifications,
            language=settings.discovery_language,
            min_stars=settings.discovery_min_stars,
            topics=settings.discovery_topics,
            result_limit=settings.discovery_result_limit,
            trending_score_threshold=settings.trending_score_threshold,
        )
        bot.scan_scheduler = ScanScheduler(
            bot.discovery_scan_service,
            interval_minutes=settings.scan_interval_minutes,
            enabled=settings.scan_enabled,
        )
        bot.run(settings.discord_token, log_handler=None)
    except Exception as exc:
        # Log only the exception type; client messages may contain connection details.
        logger.error("GitHub Scout stopped due to %s.", type(exc).__name__)
        return 1
    finally:
        if github_client is not None:
            try:
                asyncio.run(github_client.aclose())
            except Exception as exc:
                logger.warning("Could not close GitHub HTTP client (%s).", type(exc).__name__)
        if repository_store is not None:
            repository_store.close()
    return 0


if __name__ == "__main__":
    sys.exit(main())
