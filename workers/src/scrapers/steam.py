"""Steam scraper using the official Steam Web API + SteamSpy."""

from __future__ import annotations

import logging
import os
from datetime import date

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

# Steam Store API endpoints (no auth needed for most)
STEAM_TOP_SELLERS_URL = "https://store.steampowered.com/api/featuredcategories"
STEAM_APP_DETAILS_URL = "https://store.steampowered.com/api/appdetails"
STEAM_SEARCH_URL = "https://store.steampowered.com/api/storesearch"

# SteamSpy API (free, no auth)
STEAMSPY_TOP_URL = "https://steamspy.com/api.php"


class SteamScraper(BaseScraper):
    platform = "steam"
    rate_limit = 1.5

    async def scrape_rankings(
        self, chart_type: str = "top_sellers", region: str = "US"
    ) -> list[RankingEntry]:
        """Scrape Steam rankings.

        Uses SteamSpy API for top games by various criteria,
        and Steam Store API for featured/top sellers.
        """
        if chart_type == "top_sellers":
            return await self._scrape_top_sellers()
        elif chart_type == "trending":
            return await self._scrape_steamspy("top2weeks")
        elif chart_type == "most_played":
            return await self._scrape_steamspy("top100forever")
        else:
            return await self._scrape_steamspy("top100in2weeks")

    async def _scrape_top_sellers(self) -> list[RankingEntry]:
        """Scrape Steam featured categories for top sellers."""
        client = await self.get_client()
        resp = await client.get(STEAM_TOP_SELLERS_URL)
        resp.raise_for_status()
        data = resp.json()

        entries: list[RankingEntry] = []

        # The "top_sellers" key contains the top selling games
        top_sellers = data.get("top_sellers", {}).get("items", [])

        for rank, item in enumerate(top_sellers, 1):
            app_id = str(item.get("id", ""))
            name = item.get("name", "")

            entries.append(
                RankingEntry(
                    platform_id=app_id,
                    name=name,
                    rank_position=rank,
                    chart_type="top_sellers",
                    region="GLOBAL",
                    icon_url=item.get("large_capsule_image"),
                    url=f"https://store.steampowered.com/app/{app_id}",
                    metadata={
                        "discount_percent": item.get("discount_percent"),
                        "final_price": item.get("final_price"),
                    },
                )
            )

        return entries

    async def _scrape_steamspy(self, request_type: str) -> list[RankingEntry]:
        """Scrape SteamSpy for top games by various criteria."""
        client = await self.get_client()
        params = {"request": request_type}

        resp = await client.get(STEAMSPY_TOP_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        entries: list[RankingEntry] = []
        chart_map = {
            "top2weeks": "trending",
            "top100forever": "most_played",
            "top100in2weeks": "top_recent",
        }
        chart_type = chart_map.get(request_type, request_type)

        # Sort by estimated owners (descending)
        sorted_games = sorted(
            data.items(),
            key=lambda x: x[1].get("positive", 0) + x[1].get("negative", 0)
            if isinstance(x[1], dict)
            else 0,
            reverse=True,
        )

        for rank, (app_id, info) in enumerate(sorted_games[:200], 1):
            if not isinstance(info, dict):
                continue

            name = info.get("name", "")
            developer = info.get("developer", "")
            genre_str = info.get("genre", "")

            # Parse owners string like "1,000,000 .. 2,000,000"
            owners_str = info.get("owners", "0 .. 0")
            try:
                low = int(owners_str.split("..")[0].strip().replace(",", ""))
            except (ValueError, IndexError):
                low = 0

            positive = info.get("positive", 0)
            negative = info.get("negative", 0)
            total_reviews = positive + negative
            rating = round(positive / total_reviews * 5, 2) if total_reviews > 0 else None

            entries.append(
                RankingEntry(
                    platform_id=str(app_id),
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region="GLOBAL",
                    rating=rating,
                    rating_count=total_reviews,
                    download_est=low,
                    developer=developer,
                    genre=genre_str.split(",")[0].strip() if genre_str else None,
                    url=f"https://store.steampowered.com/app/{app_id}",
                    metadata={
                        "owners": owners_str,
                        "positive_reviews": positive,
                        "negative_reviews": negative,
                        "average_playtime": info.get("average_forever"),
                        "price": info.get("price"),
                        "tags": info.get("tags", {}),
                    },
                )
            )

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed game info from Steam Store API."""
        client = await self.get_client()
        params = {"appids": platform_id, "l": "english"}

        resp = await client.get(STEAM_APP_DETAILS_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        app_data = data.get(str(platform_id), {})
        if not app_data.get("success"):
            return None

        info = app_data.get("data", {})

        # Parse release date
        release_date = None
        rd = info.get("release_date", {})
        if rd and not rd.get("coming_soon"):
            try:
                release_date = date.fromisoformat(rd.get("date", "")[:10])
            except ValueError:
                pass

        # Parse genres
        genres = [g["description"] for g in info.get("genres", [])]
        genre = genres[0] if genres else None
        sub_genres = genres[1:] if len(genres) > 1 else []

        # Get price
        price = None
        price_info = info.get("price_overview", {})
        if price_info:
            price = price_info.get("final_formatted")
        elif info.get("is_free"):
            price = "Free"

        return GameDetails(
            platform_id=str(platform_id),
            name=info.get("name", ""),
            developer=", ".join(info.get("developers", [])),
            description=info.get("short_description", "")[:500],
            genre=genre,
            sub_genres=sub_genres,
            release_date=release_date,
            icon_url=info.get("header_image"),
            screenshots=[s["path_full"] for s in info.get("screenshots", [])[:5]],
            url=f"https://store.steampowered.com/app/{platform_id}",
            price=price,
            metadata={
                "publishers": info.get("publishers", []),
                "categories": [c["description"] for c in info.get("categories", [])],
                "platforms": info.get("platforms", {}),
                "metacritic": info.get("metacritic", {}),
                "recommendations": info.get("recommendations", {}).get("total"),
            },
        )
