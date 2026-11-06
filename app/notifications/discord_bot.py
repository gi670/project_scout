"""Discord bot setup and the initial health-check slash commands."""

from __future__ import annotations

import asyncio
import logging

import discord
from discord import app_commands
from discord.ext import commands

from app.config import Settings
from app.discovery.scan_service import DiscoveryScanError, ScanAlreadyRunning

logger = logging.getLogger(__name__)


class ScoutBot(commands.Bot):
    """Discord client with minimal intents and environment-based command sync."""

    def __init__(self, settings: Settings) -> None:
        intents = discord.Intents.none()
        super().__init__(command_prefix=commands.when_mentioned, intents=intents)
        self.settings = settings
        self.repository_store = None
        self.discovery_scan_service = None
        self.scan_scheduler = None

    async def setup_hook(self) -> None:
        """Register commands and sync them globally or to the development guild."""
        if self.settings.dev_guild_id is not None:
            guild = discord.Object(id=self.settings.dev_guild_id)
            self.tree.copy_global_to(guild=guild)
            synced = await self.tree.sync(guild=guild)
            logger.info("Synchronized %d application command(s) to development guild.", len(synced))
        else:
            synced = await self.tree.sync()
            logger.info("Synchronized %d global application command(s).", len(synced))

    async def on_ready(self) -> None:
        """Log a safe readiness message after Discord connects."""
        logger.info("Discord bot ready as %s (user id %s).", self.user, self.user.id if self.user else "unknown")
        if self.scan_scheduler is not None:
            try:
                self.scan_scheduler.start()
            except Exception as exc:
                logger.error("Could not start automatic scanning (%s).", type(exc).__name__)

    async def close(self) -> None:
        """Stop scheduled/manual scan activity before closing the Discord client."""
        try:
            if self.scan_scheduler is not None:
                await self.scan_scheduler.stop()
            elif self.discovery_scan_service is not None:
                self.discovery_scan_service.stop_accepting_scans()
                await self.discovery_scan_service.wait_until_idle()
        except Exception as exc:
            logger.error("Error while stopping repository scans (%s).", type(exc).__name__)
        finally:
            await super().close()

    async def on_app_command_error(
        self, interaction: discord.Interaction, error: app_commands.AppCommandError
    ) -> None:
        """Log command failures and return a generic response without leaking details."""
        logger.error("Application command failed: %s", type(error).__name__)
        message = "The command could not be completed. Please try again later."
        try:
            if interaction.response.is_done():
                await interaction.followup.send(message, ephemeral=True)
            else:
                await interaction.response.send_message(message, ephemeral=True)
        except discord.HTTPException:
            logger.warning("Could not send the application command error response.")


def create_bot(settings: Settings, repository_store=None) -> ScoutBot:
    """Build the bot and register its initial health-check commands."""
    bot = ScoutBot(settings)
    bot.repository_store = repository_store

    @bot.tree.command(name="ping", description="Check whether GitHub Scout is responding.")
    async def ping(interaction: discord.Interaction) -> None:
        await interaction.response.send_message(
            f"Pong! Gateway latency: {bot.latency * 1000:.0f} ms.", ephemeral=True
        )

    @bot.tree.command(name="status", description="Show GitHub Scout's current bot status.")
    async def status(interaction: discord.Interaction) -> None:
        guild_count = len(bot.guilds)
        await interaction.response.send_message(
            f"GitHub Scout is online. Connected to {guild_count} server(s); "
            f"gateway latency is {bot.latency * 1000:.0f} ms.",
            ephemeral=True,
        )

    @bot.tree.command(name="latest", description="Show recently discovered repositories.")
    @app_commands.describe(limit="Number of repositories to show (1–10).")
    async def latest(interaction: discord.Interaction, limit: app_commands.Range[int, 1, 10] = 5) -> None:
        if bot.repository_store is None:
            await interaction.response.send_message("Repository storage is unavailable.", ephemeral=True)
            return
        repositories = await asyncio.to_thread(bot.repository_store.latest_repositories, limit)
        await interaction.response.send_message(
            _format_repository_results(repositories, empty_message="No repositories have been discovered yet."),
            ephemeral=True,
        )

    @bot.tree.command(name="search", description="Search discovered repositories by language and stars.")
    @app_commands.describe(language="Programming language filter.", min_stars="Minimum star count.")
    async def search(
        interaction: discord.Interaction,
        language: str | None = None,
        min_stars: app_commands.Range[int, 0] = 0,
    ) -> None:
        if bot.repository_store is None:
            await interaction.response.send_message("Repository storage is unavailable.", ephemeral=True)
            return
        repositories = await asyncio.to_thread(
            bot.repository_store.search_repositories,
            language=language,
            min_stars=min_stars,
        )
        await interaction.response.send_message(
            _format_repository_results(repositories, empty_message="No stored repositories matched."),
            ephemeral=True,
        )

    @bot.tree.command(name="stats", description="Show the number of discovered repositories.")
    async def stats(interaction: discord.Interaction) -> None:
        if bot.repository_store is None:
            await interaction.response.send_message("Repository storage is unavailable.", ephemeral=True)
            return
        count = await asyncio.to_thread(bot.repository_store.count_repositories)
        await interaction.response.send_message(f"GitHub Scout has discovered **{count}** repositories.", ephemeral=True)

    @bot.tree.command(name="rescan", description="Run a GitHub repository discovery scan now.")
    async def rescan(interaction: discord.Interaction) -> None:
        scan_service = bot.discovery_scan_service
        if scan_service is None:
            await interaction.response.send_message("Discovery scanning is unavailable.", ephemeral=True)
            return
        await interaction.response.defer(ephemeral=True, thinking=True)
        try:
            result = await scan_service.scan()
        except ScanAlreadyRunning:
            await interaction.followup.send("A discovery scan is already in progress.", ephemeral=True)
            return
        except DiscoveryScanError:
            await interaction.followup.send("The discovery scan failed. Check the application logs.", ephemeral=True)
            return
        await interaction.followup.send(
            f"Scan complete: searched {result.searched_count}, saved {result.saved_count}, "
            f"sent {result.notification_count} notification(s).",
            ephemeral=True,
        )

    return bot


def _format_repository_results(repositories, *, empty_message: str) -> str:
    if not repositories:
        return empty_message
    lines = []
    for repository in repositories[:10]:
        name = repository.get("full_name", "Unknown repository")
        url = repository.get("url", "")
        stars = repository.get("stars", 0)
        language = repository.get("language") or "Unknown"
        score = repository.get("discovery_score")
        score_text = f" · score {score:.1f}" if isinstance(score, (int, float)) else ""
        lines.append(f"• [{name}]({url}) — ⭐ {stars} · {language}{score_text}")
    return "\n".join(lines)[:1900]
