"""Base review scraper class.

Extends BaseScraper with the review-specific scrape contract. Each
platform adapter implements `scrape_reviews()` returning a list of
`ReviewEntry` objects normalized to a common 0-5 rating scale.
"""

from __future__ import annotations

import logging
from abc import abstractmethod
from dataclasses import dataclass, field
from datetime import datetime

from ..base import BaseScraper

logger = logging.getLogger(__name__)


@dataclass
class ReviewEntry:
    """A single user review for a game listing on a platform.

    `rating` is normalized to a 0-5 integer scale across platforms:
    - Google Play / App Store / TapTap: native 1-5 stars
    - Steam: voted_up=True -> 5, voted_up=False -> 1
    """

    external_id: str  # platform-specific review ID (stable, for dedupe)
    rating: int | None  # normalized to 0-5 scale
    content: str  # review text (UTF-8)
    author_name: str | None
    helpful_count: int | None  # upvotes / thumbs-up / helpful votes
    language: str | None  # detected or requested language code
    posted_at: datetime
    metadata: dict = field(default_factory=dict)


class BaseReviewScraper(BaseScraper):
    """Abstract base for review scrapers.

    Review scrapers inherit rate limiting, UA rotation, HTTP client
    management, and circuit breaker logic from BaseScraper. They only
    need to implement `scrape_reviews()` — the ranking abstract
    methods from BaseScraper are stubbed as no-ops since review
    scrapers don't participate in ranking scraping.
    """

    # Subclasses set these
    platform: str = ""
    rate_limit: float = 2.0

    async def scrape_rankings(self, chart_type: str, region: str = "CN"):
        """Review scrapers do not implement ranking scraping."""
        return []

    async def scrape_game_details(self, platform_id: str):
        """Review scrapers do not implement game detail scraping."""
        return None

    @abstractmethod
    async def scrape_reviews(
        self,
        platform_id: str,
        region: str = "US",
        limit: int = 200,
        lang: str = "en",
    ) -> list[ReviewEntry]:
        """Scrape up to `limit` most recent reviews for a platform listing.

        Args:
            platform_id: platform-specific app/game identifier
                (e.g. "com.king.candycrushsaga" for Google Play,
                "730" for Steam appid, "1480134174" for App Store track id)
            region: 2-letter region/country code (e.g. "US", "CN", "JP")
            limit: maximum number of reviews to fetch
            lang: 2-letter language code (e.g. "en", "zh")

        Returns:
            list of ReviewEntry objects, most-recent first
        """
        ...

    async def scrape_reviews_safe(
        self,
        platform_id: str,
        region: str = "US",
        limit: int = 200,
        lang: str = "en",
    ) -> list[ReviewEntry]:
        """Scrape with error handling, rate limiting, and circuit breaker.

        Mirrors `BaseScraper.scrape_rankings_safe()`. Returns an empty
        list on failure so that batch jobs don't crash on a single
        bad platform.
        """
        if self.is_circuit_open():
            logger.warning(f"[{self.platform}] Circuit breaker open, skipping reviews.")
            return []

        try:
            await self.throttle()
            results = await self.scrape_reviews(
                platform_id=platform_id,
                region=region,
                limit=limit,
                lang=lang,
            )
            self.record_success()
            logger.info(
                f"[{self.platform}] Scraped {len(results)} reviews "
                f"for {platform_id} ({region}/{lang})"
            )
            return results
        except Exception as e:
            self.record_failure()
            logger.error(
                f"[{self.platform}] Review scrape failed for {platform_id}: {e}"
            )
            return []
