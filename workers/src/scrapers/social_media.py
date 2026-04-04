"""Social media scraper for tracking game-related content virality.

Monitors Douyin (TikTok CN), Bilibili, TikTok, and YouTube
for game-related videos and engagement metrics.
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
class SocialSignal:
    """Social media signal for a game."""

    game_name: str
    platform: str  # douyin, bilibili, tiktok, youtube
    video_count: int = 0
    view_count: int = 0
    like_count: int = 0
    hashtag_volume: int = 0
    signal_date: date = field(default_factory=date.today)
    metadata: dict = field(default_factory=dict)


class SocialMediaScraper(BaseScraper):
    """Scraper for social media game virality signals.

    Uses:
    - Bilibili API (free, search endpoint)
    - TikHub API (paid, for Douyin/TikTok)
    - YouTube Data API (free tier with quota)
    """

    platform = "social_media"
    rate_limit = 2.0

    def __init__(self, proxy_url: str | None = None):
        super().__init__(proxy_url)
        self.tikhub_api_key = os.environ.get("TIKHUB_API_KEY")

    async def search_bilibili(self, game_name: str) -> SocialSignal:
        """Search Bilibili for game-related videos."""
        client = await self.get_client()

        # Bilibili search API (public endpoint)
        url = "https://api.bilibili.com/x/web-interface/search/type"
        params = {
            "keyword": game_name,
            "search_type": "video",
            "order": "totalrank",
            "duration": 0,
            "page": 1,
        }

        try:
            resp = await client.get(url, params=params)
            resp.raise_for_status()
            data = resp.json()

            result_data = data.get("data", {})
            num_results = result_data.get("numResults", 0)
            results = result_data.get("result", [])

            total_views = sum(r.get("play", 0) for r in results[:20])
            total_likes = sum(r.get("like", 0) for r in results[:20])

            return SocialSignal(
                game_name=game_name,
                platform="bilibili",
                video_count=num_results,
                view_count=total_views,
                like_count=total_likes,
            )
        except Exception as e:
            logger.warning(f"[bilibili] Search failed for '{game_name}': {e}")
            return SocialSignal(game_name=game_name, platform="bilibili")

    async def search_douyin(self, game_name: str) -> SocialSignal:
        """Search Douyin for game-related content via TikHub API."""
        if not self.tikhub_api_key:
            logger.debug("[douyin] No TikHub API key configured, skipping.")
            return SocialSignal(game_name=game_name, platform="douyin")

        client = await self.get_client()

        try:
            resp = await client.get(
                "https://api.tikhub.io/api/v1/douyin/web/search_video",
                params={"keyword": game_name, "count": 20, "offset": 0},
                headers={"Authorization": f"Bearer {self.tikhub_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", {}).get("data", [])
            total_views = 0
            total_likes = 0
            for item in items:
                stats = item.get("statistics", {})
                total_views += stats.get("play_count", 0)
                total_likes += stats.get("digg_count", 0)

            return SocialSignal(
                game_name=game_name,
                platform="douyin",
                video_count=len(items),
                view_count=total_views,
                like_count=total_likes,
            )
        except Exception as e:
            logger.warning(f"[douyin] Search failed for '{game_name}': {e}")
            return SocialSignal(game_name=game_name, platform="douyin")

    async def search_tiktok(self, game_name: str) -> SocialSignal:
        """Search TikTok for game-related content via TikHub API."""
        if not self.tikhub_api_key:
            return SocialSignal(game_name=game_name, platform="tiktok")

        client = await self.get_client()

        try:
            resp = await client.get(
                "https://api.tikhub.io/api/v1/tiktok/web/search_video",
                params={"keyword": game_name, "count": 20, "offset": 0},
                headers={"Authorization": f"Bearer {self.tikhub_api_key}"},
            )
            resp.raise_for_status()
            data = resp.json()

            items = data.get("data", {}).get("data", [])
            total_views = 0
            total_likes = 0
            for item in items:
                stats = item.get("statistics", {})
                total_views += stats.get("playCount", 0)
                total_likes += stats.get("diggCount", 0)

            return SocialSignal(
                game_name=game_name,
                platform="tiktok",
                video_count=len(items),
                view_count=total_views,
                like_count=total_likes,
            )
        except Exception as e:
            logger.warning(f"[tiktok] Search failed for '{game_name}': {e}")
            return SocialSignal(game_name=game_name, platform="tiktok")

    async def collect_signals(self, game_name: str) -> list[SocialSignal]:
        """Collect social signals from all platforms for a game."""
        signals = []
        signals.append(await self.search_bilibili(game_name))
        await self.throttle()
        signals.append(await self.search_douyin(game_name))
        await self.throttle()
        signals.append(await self.search_tiktok(game_name))
        return signals

    # BaseScraper interface (not directly used for social media)
    async def scrape_rankings(
        self, chart_type: str = "trending", region: str = "CN"
    ) -> list[RankingEntry]:
        return []

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        return None
