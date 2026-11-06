"""Build and deliver repository embeds through the existing Discord bot."""

from __future__ import annotations

import logging
from datetime import datetime
from typing import Any

import discord

logger = logging.getLogger(__name__)

CHANNEL_NAMES = {
    "new_repositories": "new-repositories",
    "trending_projects": "trending-projects",
    "ai_ml_projects": "ai-ml-projects",
    "cybersecurity_projects": "cybersecurity-projects",
}


class NotificationError(RuntimeError):
    """A Discord notification could not be resolved or delivered."""


class DiscordNotificationService:
    """Send repository embeds using the bot instance already used by slash commands."""

    def __init__(self, bot: Any, channel_ids: dict[str, int], dev_guild_id: int | None) -> None:
        self._bot = bot
        self._channel_ids = dict(channel_ids)
        self._dev_guild_id = dev_guild_id

    async def send_repository(self, scored_repository: Any, channel_key: str) -> None:
        """Resolve the configured channel and send one repository embed."""
        if channel_key not in CHANNEL_NAMES:
            raise ValueError(f"Unknown notification channel key: {channel_key}")
        channel = await self._resolve_channel(channel_key)
        embed = build_repository_embed(scored_repository)
        try:
            await channel.send(embed=embed)
        except discord.HTTPException as exc:
            logger.error("Discord notification delivery failed (%s).", type(exc).__name__)
            raise NotificationError("Discord could not deliver the repository notification.") from None
        logger.info("Sent repository notification to channel category %s.", channel_key)

    async def _resolve_channel(self, channel_key: str) -> Any:
        channel_id = self._channel_ids.get(channel_key)
        if channel_id is not None:
            channel = self._bot.get_channel(channel_id)
            if channel is not None:
                return channel
            try:
                return await self._bot.fetch_channel(channel_id)
            except discord.HTTPException as exc:
                logger.error("Configured Discord channel lookup failed (%s).", type(exc).__name__)
                raise NotificationError("Configured Discord notification channel is unavailable.") from None

        if self._dev_guild_id is None:
            raise NotificationError("Set a channel ID or DEV_GUILD_ID for repository notifications.")
        guild = self._bot.get_guild(self._dev_guild_id)
        if guild is None:
            raise NotificationError("The configured development Discord server is unavailable.")
        channel_name = CHANNEL_NAMES[channel_key]
        for channel in guild.text_channels:
            if channel.name == channel_name:
                return channel
        raise NotificationError(f"Could not find the #{channel_name} channel in the development server.")


def build_repository_embed(scored_repository: Any) -> discord.Embed:
    """Create a compact embed containing the repository's discovery information."""
    repository = getattr(scored_repository, "repository", scored_repository)
    description = (getattr(repository, "description", None) or "No description provided.").strip()
    if len(description) > 4_000:
        description = description[:3_997] + "..."
    topics = getattr(repository, "topics", None) or ()
    topics_text = ", ".join(str(topic) for topic in topics) or "None listed"
    if len(topics_text) > 1_000:
        topics_text = topics_text[:997] + "..."
    score = getattr(scored_repository, "score", None)
    updated_at = _format_date(getattr(repository, "updated_at", None))

    embed = discord.Embed(
        title=getattr(repository, "full_name", None) or getattr(repository, "name", "Repository"),
        url=getattr(repository, "url", None),
        description=description,
        color=discord.Color.blurple(),
    )
    embed.add_field(name="Stars", value=str(getattr(repository, "stars", 0)), inline=True)
    embed.add_field(name="Forks", value=str(getattr(repository, "forks", 0)), inline=True)
    embed.add_field(name="Language", value=getattr(repository, "language", None) or "Not specified", inline=True)
    embed.add_field(name="Topics", value=topics_text, inline=False)
    embed.add_field(name="Discovery score", value=f"{float(score):.1f}/100" if score is not None else "Not scored", inline=True)
    embed.add_field(name="Last updated", value=updated_at, inline=True)
    return embed


def _format_date(value: datetime | str | None) -> str:
    if isinstance(value, datetime):
        return value.strftime("%Y-%m-%d")
    if isinstance(value, str):
        try:
            return datetime.fromisoformat(value.replace("Z", "+00:00")).strftime("%Y-%m-%d")
        except ValueError:
            return value[:32]
    return "Unknown"
