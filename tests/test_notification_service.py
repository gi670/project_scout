"""Mocked tests for Discord repository embeds and channel resolution."""

from datetime import datetime, timezone
from types import SimpleNamespace
import unittest
from unittest.mock import AsyncMock, Mock

from app.notifications.service import (
    DiscordNotificationService,
    NotificationError,
    build_repository_embed,
)


def sample_scored_repository():
    repository = SimpleNamespace(
        name="scout",
        full_name="sample/scout",
        description="Discover useful open source projects.",
        url="https://github.com/sample/scout",
        stars=350,
        forks=42,
        language="Python",
        topics=("discovery", "machine-learning"),
        updated_at=datetime(2026, 11, 5, tzinfo=timezone.utc),
    )
    return SimpleNamespace(repository=repository, score=82.25)


class NotificationServiceTests(unittest.IsolatedAsyncioTestCase):
    async def test_embed_contains_requested_repository_details(self) -> None:
        embed = build_repository_embed(sample_scored_repository())
        self.assertEqual(embed.title, "sample/scout")
        self.assertEqual(embed.url, "https://github.com/sample/scout")
        self.assertEqual(embed.description, "Discover useful open source projects.")
        field_values = {field.name: field.value for field in embed.fields}
        self.assertEqual(field_values["Stars"], "350")
        self.assertEqual(field_values["Forks"], "42")
        self.assertEqual(field_values["Language"], "Python")
        self.assertEqual(field_values["Topics"], "discovery, machine-learning")
        self.assertEqual(field_values["Discovery score"], "82.2/100")
        self.assertEqual(field_values["Last updated"], "2026-11-05")

    async def test_sends_to_configured_channel_id(self) -> None:
        channel = SimpleNamespace(send=AsyncMock())
        bot = SimpleNamespace(get_channel=Mock(return_value=channel), fetch_channel=AsyncMock())
        service = DiscordNotificationService(bot, {"new_repositories": 123}, dev_guild_id=10)
        await service.send_repository(sample_scored_repository(), "new_repositories")
        bot.get_channel.assert_called_once_with(123)
        bot.fetch_channel.assert_not_awaited()
        channel.send.assert_awaited_once()

    async def test_falls_back_to_channel_name_in_development_guild(self) -> None:
        channel = SimpleNamespace(name="ai-ml-projects", send=AsyncMock())
        guild = SimpleNamespace(text_channels=[channel])
        bot = SimpleNamespace(get_channel=Mock(return_value=None), get_guild=Mock(return_value=guild))
        service = DiscordNotificationService(bot, {}, dev_guild_id=99)
        await service.send_repository(sample_scored_repository(), "ai_ml_projects")
        bot.get_guild.assert_called_once_with(99)
        channel.send.assert_awaited_once()

    async def test_missing_channel_is_reported_without_external_call(self) -> None:
        bot = SimpleNamespace(get_guild=Mock(return_value=None))
        service = DiscordNotificationService(bot, {}, dev_guild_id=None)
        with self.assertRaises(NotificationError):
            await service.send_repository(sample_scored_repository(), "new_repositories")


if __name__ == "__main__":
    unittest.main()
