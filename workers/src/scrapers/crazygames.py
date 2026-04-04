"""CrazyGames HTML5 game portal scraper."""

from __future__ import annotations

import logging
import re

from bs4 import BeautifulSoup

from .base import BaseScraper, GameDetails, RankingEntry

logger = logging.getLogger(__name__)

CRAZYGAMES_BASE_URL = "https://www.crazygames.com"


class CrazyGamesScraper(BaseScraper):
    platform = "crazygames"
    rate_limit = 2.0

    async def scrape_rankings(
        self, chart_type: str = "trending", region: str = "GLOBAL"
    ) -> list[RankingEntry]:
        """Scrape CrazyGames trending/popular pages."""
        client = await self.get_client()

        page_map = {
            "trending": f"{CRAZYGAMES_BASE_URL}/t/trending",
            "new": f"{CRAZYGAMES_BASE_URL}/t/new",
            "popular": f"{CRAZYGAMES_BASE_URL}/t/most-played",
        }
        url = page_map.get(chart_type, page_map["trending"])

        resp = await client.get(url)
        resp.raise_for_status()

        return self._parse_game_list(resp.text, chart_type)

    def _parse_game_list(self, html: str, chart_type: str) -> list[RankingEntry]:
        """Parse CrazyGames game listing."""
        soup = BeautifulSoup(html, "lxml")
        entries: list[RankingEntry] = []

        # CrazyGames uses game cards with links to /game/<slug>
        game_links = soup.select('a[href*="/game/"]')
        seen_slugs: set[str] = set()

        for link in game_links:
            href = link.get("href", "")
            match = re.search(r"/game/(.+?)(?:\?|$)", href)
            if not match:
                continue

            slug = match.group(1).strip("/")
            if slug in seen_slugs:
                continue
            seen_slugs.add(slug)

            name = link.get("title", "") or link.get_text(strip=True) or slug.replace("-", " ").title()

            icon_url = None
            img = link.select_one("img")
            if img:
                icon_url = img.get("src") or img.get("data-src")

            entries.append(
                RankingEntry(
                    platform_id=slug,
                    name=name,
                    rank_position=len(entries) + 1,
                    chart_type=chart_type,
                    region="GLOBAL",
                    icon_url=icon_url,
                    url=f"{CRAZYGAMES_BASE_URL}/game/{slug}",
                )
            )

            if len(entries) >= 100:
                break

        return entries

    async def scrape_game_details(self, platform_id: str) -> GameDetails | None:
        """Scrape detailed info about a CrazyGames game."""
        client = await self.get_client()
        url = f"{CRAZYGAMES_BASE_URL}/game/{platform_id}"

        resp = await client.get(url)
        if resp.status_code == 404:
            return None
        resp.raise_for_status()

        soup = BeautifulSoup(resp.text, "lxml")

        title_el = soup.select_one("h1")
        name = title_el.get_text(strip=True) if title_el else platform_id.replace("-", " ").title()

        developer = None
        dev_el = soup.select_one('a[href*="/developer/"]')
        if dev_el:
            developer = dev_el.get_text(strip=True)

        description = None
        desc_el = soup.select_one('meta[name="description"]')
        if desc_el:
            description = desc_el.get("content", "")[:500]

        # Try to find genre/tags
        genre = None
        tag_els = soup.select('a[href*="/t/"]')
        tags = [t.get_text(strip=True) for t in tag_els[:5] if t.get_text(strip=True)]
        if tags:
            genre = tags[0]

        return GameDetails(
            platform_id=platform_id,
            name=name,
            developer=developer,
            description=description,
            genre=genre,
            sub_genres=tags[1:4] if tags else [],
            url=url,
        )
