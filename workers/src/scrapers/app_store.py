"""Apple App Store scraper using the iTunes RSS Feed and Lookup API."""

from __future__ import annotations

import logging
from datetime import date

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

# V1 iTunes RSS feed — supports genre filtering for games (genre=6014)
# Max effective limit is 100 entries
RSS_BASE = "https://itunes.apple.com/{region}/rss/{chart}/limit={limit}/genre=6014/json"

CHART_TYPE_MAP = {
    "top_free": "topfreeapplications",
    "top_paid": "toppaidapplications",
    "top_grossing": "topgrossingapplications",
}

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


class AppStoreScraper(BaseScraper):
    platform = "app_store"
    rate_limit = 1.0

    async def scrape_rankings(
        self, chart_type: str = "top_free", region: str = "US"
    ) -> list[RankingEntry]:
        """Scrape App Store game rankings using the iTunes v1 RSS feed.

        Uses genre=6014 to filter for games only. Max 100 results.
        """
        client = await self.get_client()
        region_code = REGION_MAP.get(region, region.lower())
        chart = CHART_TYPE_MAP.get(chart_type, "topfreeapplications")

        url = RSS_BASE.format(region=region_code, chart=chart, limit=100)

        resp = await client.get(url)
        resp.raise_for_status()
        data = resp.json()

        entries: list[RankingEntry] = []
        feed = data.get("feed", {})
        results = feed.get("entry", [])

        for rank, app in enumerate(results, 1):
            # V1 RSS format uses different field names than v2
            app_id = ""
            id_attrs = app.get("id", {}).get("attributes", {})
            app_id = id_attrs.get("im:id", "")

            name = ""
            name_field = app.get("im:name", {})
            if isinstance(name_field, dict):
                name = name_field.get("label", "")

            artist = ""
            artist_field = app.get("im:artist", {})
            if isinstance(artist_field, dict):
                artist = artist_field.get("label", "")

            # Category / genre
            genre = None
            category = app.get("category", {})
            if isinstance(category, dict):
                cat_attrs = category.get("attributes", {})
                genre = cat_attrs.get("label")

            # Icon URL — pick the largest image
            icon_url = None
            images = app.get("im:image", [])
            if images:
                last_img = images[-1]
                icon_url = last_img.get("label") if isinstance(last_img, dict) else None

            # App Store URL
            url_val = ""
            link = app.get("link", {})
            if isinstance(link, dict):
                link_attrs = link.get("attributes", {})
                url_val = link_attrs.get("href", "")

            entries.append(
                RankingEntry(
                    platform_id=str(app_id),
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region=region,
                    developer=artist or None,
                    genre=genre,
                    icon_url=icon_url,
                    url=url_val,
                )
            )

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape game details using the iTunes Lookup API."""
        client = await self.get_client()
        params = {"id": platform_id, "entity": "software"}

        resp = await client.get(SEARCH_API_URL, params=params)
        resp.raise_for_status()
        data = resp.json()

        results = data.get("results", [])
        if not results:
            return None

        app = results[0]

        release_date = None
        release_str = app.get("releaseDate", "")
        if release_str:
            try:
                release_date = date.fromisoformat(release_str[:10])
            except ValueError:
                pass

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
