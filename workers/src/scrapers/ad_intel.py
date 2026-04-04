"""Ad intelligence scraper for tracking game advertising activity.

Monitors Facebook Ad Library (free) and AppGrowing (paid)
for game advertising data.
"""

from __future__ import annotations

import logging
import os
import re
from dataclasses import dataclass, field
from datetime import date

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)


@dataclass
class AdSignal:
    """Advertising intelligence signal for a game."""

    game_name: str
    source: str  # facebook_ad_library, appgrowing
    active_creatives: int = 0
    markets: list[str] = field(default_factory=list)
    creative_types: list[str] = field(default_factory=list)
    estimated_spend: str = ""  # low, medium, high
    first_seen: date | None = None
    last_seen: date | None = None
    signal_date: date = field(default_factory=date.today)
    metadata: dict = field(default_factory=dict)


class AdIntelScraper(BaseScraper):
    """Scraper for ad intelligence data.

    Uses:
    - Facebook Ad Library API (free, public)
    - AppGrowing API (paid, requires key)
    """

    platform = "ad_intel"
    rate_limit = 2.0

    def __init__(self, proxy_url: str | None = None):
        super().__init__(proxy_url)
        self.appgrowing_api_key = os.environ.get("APPGROWING_API_KEY")

    async def search_facebook_ads(self, game_name: str) -> AdSignal:
        """Search Facebook Ad Library for game ads.

        The Facebook Ad Library API is free and public.
        It requires a Facebook app access token.

        For initial implementation, we search the public website.
        """
        client = await self.get_client()

        # Facebook Ad Library search (public website)
        url = "https://www.facebook.com/ads/library/"
        params = {
            "active_status": "active",
            "ad_type": "all",
            "country": "ALL",
            "q": game_name,
            "media_type": "all",
        }

        try:
            # Note: The Ad Library API is the proper way to do this
            # For now, just check if we can reach the search page
            resp = await client.get(url, params=params)

            # Facebook Ad Library requires JavaScript rendering
            # For production use, integrate the official Graph API:
            # GET /ads_archive?search_terms={game_name}&ad_active_status=ACTIVE
            logger.info(
                f"[fb_ads] Searching for '{game_name}' - "
                "Graph API integration recommended for production."
            )

            return AdSignal(
                game_name=game_name,
                source="facebook_ad_library",
            )
        except Exception as e:
            logger.warning(f"[fb_ads] Search failed for '{game_name}': {e}")
            return AdSignal(game_name=game_name, source="facebook_ad_library")

    async def search_appgrowing(self, game_name: str) -> AdSignal:
        """Search AppGrowing for game ad intelligence.

        Requires AppGrowing API key (paid service).
        """
        if not self.appgrowing_api_key:
            return AdSignal(game_name=game_name, source="appgrowing")

        client = await self.get_client()

        try:
            resp = await client.get(
                "https://api.appgrowing.co/v1/search",
                params={"keyword": game_name, "platform": "all"},
                headers={"Authorization": f"Bearer {self.appgrowing_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            results = data.get("data", {}).get("items", [])
            if not results:
                return AdSignal(game_name=game_name, source="appgrowing")

            # Aggregate ad data from results
            total_creatives = 0
            all_markets: set[str] = set()
            all_types: set[str] = set()

            for item in results:
                total_creatives += item.get("creative_count", 0)
                all_markets.update(item.get("markets", []))
                all_types.update(item.get("creative_types", []))

            # Estimate spend level
            spend = "low"
            if total_creatives > 50:
                spend = "medium"
            if total_creatives > 200:
                spend = "high"

            return AdSignal(
                game_name=game_name,
                source="appgrowing",
                active_creatives=total_creatives,
                markets=sorted(all_markets),
                creative_types=sorted(all_types),
                estimated_spend=spend,
            )
        except Exception as e:
            logger.warning(f"[appgrowing] Search failed for '{game_name}': {e}")
            return AdSignal(game_name=game_name, source="appgrowing")

    async def collect_signals(self, game_name: str) -> list[AdSignal]:
        """Collect ad intelligence signals for a game."""
        signals = []
        signals.append(await self.search_facebook_ads(game_name))
        await self.throttle()
        signals.append(await self.search_appgrowing(game_name))
        return signals

    # BaseScraper interface
    async def scrape_rankings(
        self, chart_type: str = "top_ads", region: str = "GLOBAL"
    ) -> list[RankingEntry]:
        return []

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        return None
