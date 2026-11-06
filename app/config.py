"""Load and validate application settings from the environment."""

from __future__ import annotations

import os
from dataclasses import dataclass, field

from dotenv import load_dotenv


@dataclass(frozen=True, slots=True)
class Settings:
    """Runtime configuration; secrets are never included in its representation."""

    discord_token: str = field(repr=False)
    dev_guild_id: int | None
    log_level: str
    channel_ids: dict[str, int] = field(default_factory=dict)
    discovery_min_stars: int = 100
    discovery_language: str | None = None
    discovery_topics: tuple[str, ...] = ()
    discovery_result_limit: int = 30
    trending_score_threshold: float = 70.0
    scan_interval_minutes: int = 30
    scan_enabled: bool = True

    @classmethod
    def from_env(cls) -> "Settings":
        """Read settings from process environment, loading a local .env if present."""
        load_dotenv()

        token = os.getenv("DISCORD_TOKEN", "").strip()
        if not token:
            raise ValueError("DISCORD_TOKEN is required; set it in the environment or .env file.")

        raw_guild_id = os.getenv("DEV_GUILD_ID", "").strip()
        guild_id: int | None = None
        if raw_guild_id:
            try:
                guild_id = int(raw_guild_id)
            except ValueError as exc:
                raise ValueError("DEV_GUILD_ID must be a positive integer.") from exc
            if guild_id <= 0:
                raise ValueError("DEV_GUILD_ID must be a positive integer.")

        log_level = os.getenv("LOG_LEVEL", "INFO").strip().upper() or "INFO"
        if log_level not in {"DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"}:
            raise ValueError("LOG_LEVEL must be DEBUG, INFO, WARNING, ERROR, or CRITICAL.")

        channel_env_names = {
            "new_repositories": "NEW_REPOSITORIES_CHANNEL_ID",
            "trending_projects": "TRENDING_PROJECTS_CHANNEL_ID",
            "ai_ml_projects": "AI_ML_PROJECTS_CHANNEL_ID",
            "cybersecurity_projects": "CYBERSECURITY_PROJECTS_CHANNEL_ID",
        }
        channel_ids: dict[str, int] = {}
        for channel_key, env_name in channel_env_names.items():
            raw_id = os.getenv(env_name, "").strip()
            if raw_id:
                try:
                    channel_id = int(raw_id)
                except ValueError as exc:
                    raise ValueError(f"{env_name} must be a positive integer.") from exc
                if channel_id <= 0:
                    raise ValueError(f"{env_name} must be a positive integer.")
                channel_ids[channel_key] = channel_id

        try:
            discovery_min_stars = int(os.getenv("DISCOVERY_MIN_STARS", "100"))
            discovery_result_limit = int(os.getenv("DISCOVERY_RESULT_LIMIT", "30"))
            trending_score_threshold = float(os.getenv("TRENDING_SCORE_THRESHOLD", "70"))
        except ValueError as exc:
            raise ValueError("Discovery limits and trending threshold must be numeric.") from exc
        if discovery_min_stars < 0 or not 1 <= discovery_result_limit <= 100:
            raise ValueError("DISCOVERY_MIN_STARS must be non-negative and DISCOVERY_RESULT_LIMIT must be 1-100.")
        if not 0 <= trending_score_threshold <= 100:
            raise ValueError("TRENDING_SCORE_THRESHOLD must be between 0 and 100.")
        try:
            scan_interval_minutes = int(os.getenv("SCAN_INTERVAL_MINUTES", "30"))
        except ValueError as exc:
            raise ValueError("SCAN_INTERVAL_MINUTES must be a positive integer.") from exc
        if scan_interval_minutes < 1:
            raise ValueError("SCAN_INTERVAL_MINUTES must be a positive integer.")
        raw_scan_enabled = os.getenv("SCAN_ENABLED", "true").strip().casefold()
        if raw_scan_enabled not in {"true", "false", "1", "0", "yes", "no"}:
            raise ValueError("SCAN_ENABLED must be true or false.")
        scan_enabled = raw_scan_enabled in {"true", "1", "yes"}
        discovery_language = os.getenv("DISCOVERY_LANGUAGE", "").strip() or None
        discovery_topics = tuple(
            topic.strip() for topic in os.getenv("DISCOVERY_TOPICS", "").split(",") if topic.strip()
        )

        return cls(
            discord_token=token,
            dev_guild_id=guild_id,
            log_level=log_level,
            channel_ids=channel_ids,
            discovery_min_stars=discovery_min_stars,
            discovery_language=discovery_language,
            discovery_topics=discovery_topics,
            discovery_result_limit=discovery_result_limit,
            trending_score_threshold=trending_score_threshold,
            scan_interval_minutes=scan_interval_minutes,
            scan_enabled=scan_enabled,
        )
