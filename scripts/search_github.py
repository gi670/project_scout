"""Demonstrate searching GitHub for Python repositories with 100+ stars."""

from __future__ import annotations

import asyncio
import logging

from app.github import GitHubAPIError, GitHubClient


async def main() -> None:
    logging.basicConfig(level=logging.INFO, format="%(levelname)s %(name)s: %(message)s")
    try:
        async with GitHubClient() as github:
            repositories = await github.search_repositories(
                language="Python",
                min_stars=100,
                per_page=10,
            )
    except GitHubAPIError as exc:
        logging.getLogger(__name__).error("Repository search failed: %s", exc)
        return

    for repository in repositories:
        description = repository.description or "No description"
        print(f"{repository.full_name} ★ {repository.stars} — {description}")
        print(f"  {repository.url}")


if __name__ == "__main__":
    asyncio.run(main())
