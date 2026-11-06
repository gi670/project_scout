"""Async GitHub REST API client for public repository search."""

from __future__ import annotations

import logging
import os
from dataclasses import dataclass
from datetime import date, datetime, timezone
from typing import Any, Sequence

import httpx
from dotenv import load_dotenv

logger = logging.getLogger(__name__)
GITHUB_API_URL = "https://api.github.com"


class GitHubAPIError(RuntimeError):
    """A safe, user-facing GitHub API error without response or credential data."""

    def __init__(self, message: str, *, status_code: int | None = None, rate_limited: bool = False) -> None:
        super().__init__(message)
        self.status_code = status_code
        self.rate_limited = rate_limited


@dataclass(frozen=True, slots=True)
class Repository:
    """Selected metadata returned for a GitHub repository."""

    name: str
    full_name: str
    description: str | None
    url: str
    stars: int
    forks: int
    language: str | None
    topics: tuple[str, ...]
    created_at: datetime | None
    updated_at: datetime | None
    id: int | None = None

    @classmethod
    def from_api(cls, data: dict[str, Any]) -> "Repository":
        """Convert one GitHub repository response into the stable application model."""
        try:
            return cls(
                name=data["name"],
                full_name=data["full_name"],
                id=int(data["id"]) if data.get("id") is not None else None,
                description=data.get("description"),
                url=data["html_url"],
                stars=int(data["stargazers_count"]),
                forks=int(data["forks_count"]),
                language=data.get("language"),
                topics=tuple(data.get("topics") or ()),
                created_at=_parse_timestamp(data.get("created_at")),
                updated_at=_parse_timestamp(data.get("updated_at")),
            )
        except (KeyError, TypeError, ValueError) as exc:
            raise GitHubAPIError("GitHub returned a repository with an invalid response format.") from exc


class GitHubClient:
    """Asynchronous client with bounded timeouts and optional token authentication."""

    def __init__(
        self,
        token: str | None = None,
        *,
        timeout: float = 15.0,
        transport: httpx.AsyncBaseTransport | None = None,
    ) -> None:
        if timeout <= 0:
            raise ValueError("timeout must be positive")
        if token is None:
            load_dotenv()
            token = os.getenv("GITHUB_TOKEN")

        headers = {
            "Accept": "application/vnd.github+json",
            "X-GitHub-Api-Version": "2022-11-28",
            "User-Agent": "GitHub-Scout",
        }
        if token and token.strip():
            headers["Authorization"] = f"Bearer {token.strip()}"

        self._client = httpx.AsyncClient(
            base_url=GITHUB_API_URL,
            headers=headers,
            timeout=httpx.Timeout(timeout),
            transport=transport,
        )

    async def __aenter__(self) -> "GitHubClient":
        return self

    async def __aexit__(self, *_: object) -> None:
        await self.aclose()

    async def aclose(self) -> None:
        """Close the underlying HTTP connection pool."""
        await self._client.aclose()

    async def search_repositories(
        self,
        *,
        language: str | None = None,
        min_stars: int = 0,
        created_after: date | datetime | str | None = None,
        topics: Sequence[str] = (),
        per_page: int = 30,
        page: int = 1,
    ) -> list[Repository]:
        """Search public repositories using GitHub's repository search qualifiers."""
        if min_stars < 0:
            raise ValueError("min_stars cannot be negative")
        if not 1 <= per_page <= 100:
            raise ValueError("per_page must be between 1 and 100")
        if page < 1:
            raise ValueError("page must be at least 1")

        qualifiers = ["is:public", f"stars:>={min_stars}"]
        if language and language.strip():
            qualifiers.append(f"language:{_validate_qualifier(language, 'language')}")
        if created_after is not None:
            qualifiers.append(f"created:>={_format_date(created_after)}")
        for topic in topics:
            qualifiers.append(f"topic:{_validate_qualifier(topic, 'topic')}")

        params = {"q": " ".join(qualifiers), "sort": "stars", "order": "desc", "per_page": per_page, "page": page}
        try:
            response = await self._client.get("/search/repositories", params=params)
            response.raise_for_status()
            payload = response.json()
            items = payload["items"]
            if not isinstance(items, list):
                raise TypeError("items is not a list")
            repositories = [Repository.from_api(item) for item in items]
        except httpx.HTTPStatusError as exc:
            status = exc.response.status_code
            rate_limited = status == 429 or (
                status == 403 and exc.response.headers.get("x-ratelimit-remaining") == "0"
            )
            if rate_limited:
                logger.warning("GitHub API rate limit reached; a later scan will retry.")
                raise GitHubAPIError(
                    "GitHub API rate limit reached; retry during a later scan.",
                    status_code=status,
                    rate_limited=True,
                ) from None
            logger.warning("GitHub repository search failed with HTTP status %d.", status)
            raise GitHubAPIError(
                f"GitHub repository search failed with HTTP status {status}.", status_code=status
            ) from None
        except httpx.TimeoutException:
            logger.warning("GitHub repository search timed out.")
            raise GitHubAPIError("GitHub repository search timed out.") from None
        except httpx.RequestError as exc:
            logger.warning("GitHub repository search failed due to %s.", type(exc).__name__)
            raise GitHubAPIError("Could not connect to the GitHub API.") from None
        except (KeyError, TypeError, ValueError) as exc:
            logger.warning("GitHub returned an invalid search response (%s).", type(exc).__name__)
            raise GitHubAPIError("GitHub returned an invalid search response.") from None

        logger.info("GitHub repository search returned %d result(s).", len(repositories))
        return repositories


def _parse_timestamp(value: str | None) -> datetime | None:
    if not value:
        return None
    parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    if parsed.tzinfo is None:
        return parsed.replace(tzinfo=timezone.utc)
    return parsed


def _format_date(value: date | datetime | str) -> str:
    if isinstance(value, datetime):
        return value.date().isoformat()
    if isinstance(value, date):
        return value.isoformat()
    try:
        return date.fromisoformat(value).isoformat()
    except ValueError as exc:
        raise ValueError("created_after must be an ISO date (YYYY-MM-DD)") from exc


def _validate_qualifier(value: str, name: str) -> str:
    value = value.strip()
    if not value or any(character.isspace() for character in value):
        raise ValueError(f"{name} must be non-empty and cannot contain whitespace")
    return value
