"""Apple App Store scraper using the iTunes RSS Feed and Search API."""

from __future__ import annotations

import logging
from datetime import date

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

# iTunes RSS feed for top charts - free, official, no authentication needed
# https://rss.applemarketingtools.com/
RSS_FEED_URL = "https://rss.applemarketingtools.com/api/v2/{region}/apps/top-free/{limit}/games.json"
RSS_FEED_PAID_URL = "https://rss.applemarketingtools.com/api/v2/{region}/apps/top-paid/{limit}/games.json"

# iTunes Search API
SEARCH_API_URL = "https://itunes.apple.com/lookup"

REGION_MAP = {
    "CN": "cn",
    "US": "us",
    "JP": "jp",
    "KR": "kr",
    "TW": "tw",
    "GB": "gb",
    "DE": "de",
    "BR": "br",
    "IN": "in",
}

CHART_TYPE_MAP = {
    "top_free": RSS_FEED_URL,
    "top_paid": RSS_FEED_PAID_URL,
}


class AppStoreScraper(BaseScraper):
    platform = "app_store"
    rate_limit = 1.0  # Apple's API is generous

    async def scrape_rankings(
        self, chart_type: str = "top_free", region: str = "US"
    ) -> list[RankingEntry]:
        """Scrape App Store rankings using the official RSS feed.

        The Apple Marketing Tools RSS feed is free, reliable, and
        returns up to 200 apps per request in JSON format.
        """
        client = await self.get_client()
        region_code = REGION_MAP.get(region, region.lower())

        url_template = CHART_TYPE_MAP.get(chart_type, RSS_FEED_URL)
        url = url_template.format(region=region_code, limit=200)

        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        entries: list[RankingEntry] = []
        feed = data.get("feed", {})
        results = feed.get("results", [])

        for rank, app in enumerate(results, 1):
            app_id = app.get("id", "")
            name = app.get("name", "")
            artist = app.get("artistName", "")
            icon = app.get("artworkUrl100", "")
            genres = app.get("genres", [])
            genre = genres[0].get("name") if genres else None
            url_val = app.get("url", "")

            entries.append(
                RankingEntry(
                    platform_id=str(app_id),
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region=region,
                    developer=artist,
                    genre=genre,
                    icon_url=icon,
                    url=url_val,
                )
            )

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape game details using the iTunes Lookup API.

        Free official API, no authentication required.
        """
        client = await self.get_client()
        params = {"id": platform_id, "entity": "software"}

        resp = await client.get(SEARCH_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        app = results[0]

        # Parse release date
        release_date = None
        release_str = app.get("releaseDate", "")
        if release_str:
            try:
                release_date = date.fromisoformat(release_str[:10])
            except ValueError:
                pass

        # Parse genres
        genres = app.get("genres", [])
        genre = genres[0] if genres else None
        sub_genres = genres[1:] if len(genres) > 1 else []

        return GameDetails(
            platform_id=str(platform_id),
            name=app.get("trackName", ""),
            developer=app.get("artistName"),
            description=app.get("description", "")[:500],
            genre=genre,
            sub_genres=sub_genres,
            rating=app.get("averageUserRating"),
            rating_count=app.get("userRatingCount"),
            release_date=release_date,
            icon_url=app.get("artworkUrl512") or app.get("artworkUrl100"),
            screenshots=app.get("screenshotUrls", [])[:5],
            url=app.get("trackViewUrl"),
            price=app.get("formattedPrice"),
            metadata={
                "bundle_id": app.get("bundleId"),
                "file_size": app.get("fileSizeBytes"),
                "content_rating": app.get("contentAdvisoryRating"),
                "minimum_os": app.get("minimumOsVersion"),
                "seller": app.get("sellerName"),
            },
        )
