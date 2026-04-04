"""Base scraper class that all platform adapters must implement."""

from __future__ import annotations

import asyncio
import logging
import random
from abc import ABC, abstractmethod
from dataclasses import dataclass, field
from datetime import date, datetime

import httpx

logger = logging.getLogger(__name__)

_USER_AGENTS = [
    # Chrome on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Chrome on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/124.0.0.0 Safari/537.36",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/537.36 (KHTML, like Gecko) Chrome/123.0.0.0 Safari/537.36",
    # Firefox on Windows
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:125.0) Gecko/20100101 Firefox/125.0",
    "Mozilla/5.0 (Windows NT 10.0; Win64; x64; rv:124.0) Gecko/20100101 Firefox/124.0",
    # Firefox on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10.15; rv:125.0) Gecko/20100101 Firefox/125.0",
    # Safari on Mac
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.4 Safari/605.1.15",
    "Mozilla/5.0 (Macintosh; Intel Mac OS X 10_15_7) AppleWebKit/605.1.15 (KHTML, like Gecko) Version/17.3 Safari/605.1.15",
]


@dataclass
class RankingEntry:
    """A single game's ranking on a platform chart."""

    platform_id: str
    name: str
    rank_position: int
    chart_type: str  # top_free, top_grossing, trending, new, etc.
    region: str  # CN, US, JP, etc.
    rating: float | None = None
    rating_count: int | None = None
    download_est: int | None = None
    developer: str | None = None
    genre: str | None = None
    icon_url: str | None = None
    url: str | None = None
    metadata: dict = field(default_factory=dict)


@dataclass
class GameDetails:
    """Detailed info about a specific game on a platform."""

    platform_id: str
    name: str
    developer: str | None = None
    description: str | None = None
    genre: str | None = None
    sub_genres: list[str] = field(default_factory=list)
    rating: float | None = None
    rating_count: int | None = None
    download_est: int | None = None
    release_date: date | None = None
    last_updated: date | None = None
    icon_url: str | None = None
    screenshots: list[str] = field(default_factory=list)
    url: str | None = None
    price: str | None = None
    metadata: dict = field(default_factory=dict)


class BaseScraper(ABC):
    """Abstract base class for all platform scrapers.

    Each platform implements its own adapter by subclassing this.
    The adapter pattern isolates platform-specific logic so when
    a site changes, only one file needs updating.
    """

    platform: str = ""
    rate_limit: float = 2.0  # seconds between requests

    def __init__(self, proxy_url: str | None = None):
        self.proxy_url = proxy_url
        self._last_request_time: float = 0
        self._client: httpx.AsyncClient | None = None
        self._consecutive_failures = 0
        self._circuit_open_until: datetime | None = None

    async def get_client(self) -> httpx.AsyncClient:
        if self._client is None:
            transport = httpx.AsyncHTTPTransport(retries=2)
            proxies = self.proxy_url if self.proxy_url else None
            self._client = httpx.AsyncClient(
                transport=transport,
                proxy=proxies,
                timeout=30.0,
                headers={"User-Agent": random.choice(_USER_AGENTS)},
                follow_redirects=True,
            )
        return self._client

    async def throttle(self):
        """Enforce rate limiting with ±30% jitter to appear more human."""
        now = asyncio.get_event_loop().time()
        elapsed = now - self._last_request_time
        jittered = self.rate_limit * random.uniform(0.7, 1.3)
        if elapsed < jittered:
            await asyncio.sleep(jittered - elapsed)
        self._last_request_time = asyncio.get_event_loop().time()

    def is_circuit_open(self) -> bool:
        """Check if circuit breaker is open (too many failures)."""
        if self._circuit_open_until is None:
            return False
        if datetime.now() >= self._circuit_open_until:
            self._circuit_open_until = None
            self._consecutive_failures = 0
            return False
        return True

    def record_success(self):
        self._consecutive_failures = 0

    def record_failure(self):
        self._consecutive_failures += 1
        if self._consecutive_failures >= 5:
            from datetime import timedelta

            self._circuit_open_until = datetime.now() + timedelta(hours=1)
            logger.warning(
                f"[{self.platform}] Circuit breaker opened after "
                f"{self._consecutive_failures} failures. Pausing for 1 hour."
            )

    @abstractmethod
    async def scrape_rankings(
        self, chart_type: str, region: str = "CN"
    ) -> list[RankingEntry]:
        """Scrape ranking list for a given chart type and region."""
        ...

    @abstractmethod
    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed info about a specific game."""
        ...

    async def scrape_rankings_safe(
        self, chart_type: str, region: str = "CN"
    ) -> list[RankingEntry]:
        """Scrape with error handling, rate limiting, and circuit breaker."""
        if self.is_circuit_open():
            logger.warning(f"[{self.platform}] Circuit breaker open, skipping.")
            return []

        try:
            await self.throttle()
            results = await self.scrape_rankings(chart_type, region)
            self.record_success()
            logger.info(
                f"[{self.platform}] Scraped {len(results)} entries "
                f"for {chart_type}/{region}"
            )
            return results
        except Exception as e:
            self.record_failure()
            logger.error(f"[{self.platform}] Scrape failed: {e}")
            return []

    async def close(self):
        if self._client:
            await self._client.aclose()
            self._client = None
