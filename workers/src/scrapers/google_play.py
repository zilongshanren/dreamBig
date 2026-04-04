"""Google Play Store scraper using gplay-scraper library.

Uses the internal batchexecute API via gplay-scraper for reliable
access to game top charts across regions.
"""

from __future__ import annotations

import logging

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

REGION_MAP = {
    "US": "us",
    "JP": "jp",
    "KR": "kr",
    "TW": "tw",
    "GB": "gb",
    "DE": "de",
    "BR": "br",
    "IN": "in",
}

LANG_MAP = {
    "us": "en",
    "jp": "ja",
    "kr": "ko",
    "tw": "zh-TW",
    "gb": "en",
    "de": "de",
    "br": "pt-BR",
    "in": "en",
}

CHART_MAP = {
    "top_free": "TOP_FREE",
    "top_grossing": "TOP_GROSSING",
    "top_paid": "TOP_PAID",
}


class GooglePlayScraper(BaseScraper):
    platform = "google_play"
    rate_limit = 2.0

    async def scrape_rankings(
        self, chart_type: str = "top_free", region: str = "US"
    ) -> list[RankingEntry]:
        """Scrape Google Play game rankings using gplay-scraper.

        Uses the internal batchexecute API which reliably returns
        game-only rankings.
        """
        from gplay_scraper import GPlayScraper

        country = REGION_MAP.get(region, region.lower())
        lang = LANG_MAP.get(country, "en")
        chart = CHART_MAP.get(chart_type, "TOP_FREE")

        try:
            gp = GPlayScraper(http_client="curl_cffi")
            results = gp.list_analyze(chart, "GAME", count=100, lang=lang, country=country)
        except Exception as e:
            logger.error(f"[google_play] gplay-scraper failed for {chart_type}/{region}: {e}")
            return []

        if not results:
            logger.warning(f"[google_play] No results for {chart_type}/{region}")
            return []

        entries: list[RankingEntry] = []
        items = results if isinstance(results, list) else results.get("results", [])

        for rank, app in enumerate(items, 1):
            app_id = app.get("appId", "") or app.get("app_id", "")
            name = app.get("title", "")
            developer = app.get("developer", "") or app.get("developerName", "")
            icon_url = app.get("icon", "") or app.get("iconUrl", "")
            genre = app.get("genre", "") or app.get("genreId", "")
            rating = app.get("score") or app.get("rating")

            if not app_id:
                continue

            entries.append(
                RankingEntry(
                    platform_id=app_id,
                    name=name,
                    rank_position=rank,
                    chart_type=chart_type,
                    region=region,
                    developer=developer or None,
                    genre=genre or None,
                    icon_url=icon_url or None,
                    rating=float(rating) if rating else None,
                    url=f"https://play.google.com/store/apps/details?id={app_id}",
                )
            )

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed information about a specific game."""
        from gplay_scraper import GPlayScraper

        try:
            gp = GPlayScraper(http_client="curl_cffi")
            app = gp.app(platform_id, lang="en", country="us")
        except Exception as e:
            logger.error(f"[google_play] Detail failed for {platform_id}: {e}")
            return None

        if not app:
            return None

        return GameDetails(
            platform_id=platform_id,
            name=app.get("title", ""),
            developer=app.get("developer"),
            description=(app.get("description") or "")[:500],
            genre=app.get("genre"),
            rating=float(app["score"]) if app.get("score") else None,
            rating_count=app.get("ratings"),
            download_est=app.get("realInstalls") or app.get("minInstalls"),
            icon_url=app.get("icon"),
            url=f"https://play.google.com/store/apps/details?id={platform_id}",
        )
